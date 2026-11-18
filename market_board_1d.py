# -*- coding: utf-8 -*-
"""相場ボードの「1日」の線 → docs/market_board_1d.json（株レーダー・2026-11-18）

相場ボード（market_board.py・docs/market_board.json の日足）の24本に、直近の取引日1日の中の動きを足す。
日足のファイルは形を変えず、1日の線は別ファイルにする（大きさを分け、こちらが失敗しても日足は出る）。

■ 呼ばれ方
  world_fetch.py の main() が market_board.write(...) の後に、別の try で write(now, edition) を呼ぶ
  （world.yml・JST 7:00/16:00/23:00）。前日終値は market_board.py が組み立てに使った日足（LAST_SERIES）から取る。
  取得の追加は yfinance の 5分足を1回（24本まとめて・period=5d）だけ。

■ 1日の決め方（本ごと）
  ・取引所の指数・ETF・金利: 現地の時刻で取引時間（SESS）の中の足だけを使い、足がある一番新しい現地の日付を「その取引日」。
    いまの時刻がその日の引け（SESS の終わり）を過ぎていれば fin=1（その日の終わりまで）、まだなら fin=0（途中・「◯:◯◯時点」）。
    線の基準（pc）は日足のその取引日より前の最後の終値（=前日終値）。
    引け後（fin=1）で日足にその日の終値があれば、最後の値（last）は日足の終値にそろえる（引けの値が5分足に無いことがあるため）。
  ・CME の先物（日経平均先物・原油・金・銅）: 取引日は米東部 18:00 から翌日 17:00。日付は終わる側の日。
  ・為替・ビットコイン（24時間）: 直近24時間（roll=24）。基準は24時間前の値。いつでも fin=0（「◯:◯◯時点」）。
  ・日本10年（財務省 CSV・日次だけ）: 1日の線なし（ok=0・note）。サイトは日足に落とす。

■ 形
  {"v":1,"updated":"...+09:00","edition":"morning|asia|night",
   "items":[{"id":"n225","t":"^N225","ok":1,
             "d":"2026-11-18",   # その取引日（現地の日付。roll のときは終わりの日本時間の日付）
             "tz":"Asia/Tokyo",  # 現地の時刻の基準
             "fin":1,            # 1 = その取引日の引けまで（終値）／0 = 途中
             "roll":24,          # 為替・暗号資産だけ（直近24時間）
             "te":"15:30",       # 最後の時刻（現地。fin=1 は引けの時刻、fin=0 は最後の足の終わり）
             "tj":"2026-11-18T15:30:00+09:00",   # 同じ時刻を日本時間で
             "iv":5,             # 足の間隔（分）
             "t0":"09:00",       # 最初の足の時刻（現地）
             "m":[0,1,1,...],    # 足の時刻: 先頭は t0 からの、以降は前の足からの「iv の何本分」（昼休みは大きく飛ぶ）
             "c":[...],          # 足の終値（有効数字5桁）
             "pc":..., "last":..., "chg":..., "pct":...,   # 前日終値（基準線）・最後の値・その差・騰落率（金利は pct なし・chg は %pt）
             "hi":..., "lo":...}]}                      # その日の高値・安値（5分足の高値・安値から）
  yfinance の値は遅れて届くので、サイトでは「リアルタイム」と書かない。
"""
import json
import math
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DST = HERE / "docs" / "market_board_1d.json"
JST = ZoneInfo("Asia/Tokyo")

# 取引時間（現地）。この中の足だけを使う。終わりの時刻を過ぎていればその日は引け後
SESS = {
    "tse": ("Asia/Tokyo", time(9, 0), time(15, 30)),
    "nyse": ("America/New_York", time(9, 30), time(16, 0)),
    "cboe": ("America/New_York", time(9, 30), time(16, 15)),
    "ust": ("America/New_York", time(8, 0), time(15, 0)),       # 米10年（^TNX の配信は 8時台〜15時）
    "xetra": ("Europe/Berlin", time(9, 0), time(17, 30)),
    "lse": ("Europe/London", time(8, 0), time(16, 30)),
    "sse": ("Asia/Shanghai", time(9, 30), time(15, 0)),
    "hkex": ("Asia/Hong_Kong", time(9, 30), time(16, 10)),
    "krx": ("Asia/Seoul", time(9, 0), time(15, 30)),
    "bse": ("Asia/Kolkata", time(9, 15), time(15, 30)),
}
CME = ("America/New_York", time(17, 0))      # 先物: 18:00 に始まり翌日 17:00 に終わる
IV = 5                 # 取る足（分）
MAX_PTS = 120          # これより多い本（24時間の市場）は 15分にまとめる
IV_BIG = 15
STALE_DAYS = 6         # 一番新しい足がこれより古ければ欠け（連休を見込む）


def sig(v, n=5):
    if v == 0:
        return 0
    r = round(v, n - 1 - int(math.floor(math.log10(abs(v)))))
    return int(r) if r == int(r) else r


def rnd(v, k, ref=None):
    """market_board.rnd と同じ桁"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if k == "rate":
        return round(v, 3)
    a = abs(v if ref is None else ref)
    d = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 5
    return round(v, d)


def sess_key(iid, ck):
    if iid == "us10":
        return "ust"
    return ck


def fetch(tickers):
    """5分足を1回でまとめて取る。DataFrame（列は (ticker, OHLC)）か None"""
    import yfinance as yf
    try:
        return yf.download(tickers, period="5d", interval=f"{IV}m", progress=False, auto_adjust=False,
                           group_by="ticker", threads=False)
    except Exception as e:
        print(f"  1日の線の取得に失敗: {e}", file=sys.stderr)
        return None


def bars_of(df, t):
    """その本の 5分足（index=UTC の Timestamp・列 High/Low/Close）。無ければ None"""
    try:
        b = df[t][["High", "Low", "Close"]].dropna(subset=["Close"])
    except Exception:
        return None
    if not len(b):
        return None
    if b.index.tz is None:
        b.index = b.index.tz_localize("UTC")
    b = b[~b.index.duplicated(keep="last")].sort_index()
    b = b[b["Close"] > 0]
    return b if len(b) >= 2 else None


def pick_session(b, key, now_utc, iid):
    """(その日の足, 取引日 date, 引けの時刻 aware datetime か None, tz 名, roll)"""
    if key == "live":
        end = b.index[-1]
        win = b[b.index > end - timedelta(hours=24)]
        # 基準は24時間前（窓のすぐ前の足）
        before = b[b.index <= end - timedelta(hours=24)]
        pc = float(before["Close"].iloc[-1]) if len(before) else None
        return win, end.tz_convert(JST).date(), None, "Asia/Tokyo", 24, pc
    if key == "cme":
        tz, t_close = CME
        loc = b.index.tz_convert(tz)
        sday = (loc + timedelta(hours=6)).date      # 18:00 からが翌日の取引日
        last = max(sday)
        win = b[sday == last]
        close_at = datetime.combine(last, t_close, tzinfo=ZoneInfo(tz))
        return win, last, close_at, tz, None, None
    tz, t_open, t_close = SESS[key]
    loc = b.index.tz_convert(tz)
    tod = loc.time
    inside = (tod >= t_open) & (tod < t_close)
    b = b[inside]
    if not len(b):
        return None
    days = b.index.tz_convert(tz).date
    last = max(days)
    win = b[days == last]
    close_at = datetime.combine(last, t_close, tzinfo=ZoneInfo(tz))
    return win, last, close_at, tz, None, None


def prev_close(daily, sday):
    """日足の、その取引日より前の最後の終値（daily は market_board の正規化済み Series）"""
    if daily is None or not len(daily):
        return None, None
    import pandas as pd
    s = daily[daily.index < pd.Timestamp(sday)]
    if not len(s):
        return None, None
    same = daily[daily.index == pd.Timestamp(sday)]
    return float(s.iloc[-1]), (float(same.iloc[-1]) if len(same) else None)


def build_one(iid, t, k, ck, b, daily, now_utc):
    row = {"id": iid, "t": t, "ok": 0}
    key = sess_key(iid, ck)
    if b is None or (key not in SESS and key not in ("cme", "live")):
        row["note"] = "1日の線なし"
        return row
    got = pick_session(b, key, now_utc, iid)
    if got is None:
        row["note"] = "1日の線なし"
        return row
    win, sday, close_at, tz, roll, pc_roll = got
    if len(win) < 2:
        row["note"] = "1日の線なし（足が足りない）"
        return row
    if (now_utc.date() - sday).days > STALE_DAYS:
        row["note"] = f"1日の線なし（最終 {sday.isoformat()}）"
        return row
    iv = IV
    if len(win) > MAX_PTS:
        import pandas as pd
        g = win.groupby(win.index.floor(f"{IV_BIG}min"))
        win = pd.DataFrame({"High": g["High"].max(), "Low": g["Low"].min(), "Close": g["Close"].last()})
        iv = IV_BIG
    fin = 0 if close_at is None else int(now_utc >= close_at)
    if roll:
        pc, day_close = pc_roll, None
    else:
        pc, day_close = prev_close(daily, sday)
    if pc is None:
        row["note"] = "1日の線なし（前日終値なし）"
        return row
    ztz = ZoneInfo(tz)
    loc = list(win.index.tz_convert(ztz))
    closes = [float(v) for v in win["Close"].values]
    last = closes[-1]
    hi, lo = float(win["High"].max()), float(win["Low"].min())
    end_utc = win.index[-1] + timedelta(minutes=iv)
    if fin:
        end_utc = close_at
        if day_close is not None:
            last = day_close           # 引けの値（5分足に入らないことがある）にそろえる
            if abs(closes[-1] - last) > 1e-9:
                closes.append(last)
                loc.append(max(close_at.astimezone(ztz), loc[-1] + timedelta(minutes=iv)))
            hi, lo = max(hi, last), min(lo, last)
    else:
        end_utc = min(end_utc, now_utc)
    # 時刻（iv の何本分）
    t0 = loc[0]
    steps = [int(round((x - t0).total_seconds() / 60 / iv)) for x in loc]
    m = [s - (steps[n - 1] if n else 0) for n, s in enumerate(steps)]
    end_loc = end_utc.astimezone(ztz)
    row.update({
        "ok": 1, "d": sday.isoformat(), "tz": tz, "fin": fin,
        "te": end_loc.strftime("%H:%M"),
        "tj": end_utc.astimezone(JST).isoformat(timespec="minutes"),
        "iv": iv, "t0": t0.strftime("%H:%M"), "m": m,
        "c": [sig(v) for v in closes],
        "pc": rnd(pc, k), "last": rnd(last, k),
        "chg": rnd(last - pc, k, last),
        "pct": None if k == "rate" else round((last / pc - 1) * 100, 2),
        "hi": rnd(hi, k), "lo": rnd(lo, k),
    })
    if roll:
        row["roll"] = roll
    return row


def build(df, daily_of, now):
    """daily_of(iid) → (使ったティッカー, 日足の Series) か (None, None)"""
    import market_board as mb
    now_utc = now.astimezone(ZoneInfo("UTC"))
    items = []
    for iid, name, g, k, t, alt, alt_name, ck in mb.ITEMS:
        used, daily = daily_of(iid)
        tk = used or t
        if tk == "MOF":
            items.append({"id": iid, "t": tk, "ok": 0, "note": "1日の線なし（日次のデータだけ）"})
            continue
        b = bars_of(df, tk) if df is not None else None
        try:
            items.append(build_one(iid, tk, k, ck, b, daily, now_utc))
        except Exception as e:
            print(f"  1日の線: {iid} で失敗 {e!r}", file=sys.stderr)
            items.append({"id": iid, "t": tk, "ok": 0, "note": "1日の線なし"})
    return {"v": 1, "updated": now.isoformat(timespec="seconds"),
            "ok": sum(i["ok"] for i in items), "count": len(items),
            "source": "Yahoo!ファイナンスの配信値（5分足・遅れて届く値）", "items": items}


def write(now, edition):
    """world_fetch.py から呼ぶ（market_board.write の後）。docs/market_board_1d.json を書く"""
    import market_board as mb
    used = getattr(mb, "LAST_SERIES", {}) or {}

    def daily_of(iid):
        return used.get(iid, (None, None))
    tickers = []
    for iid, name, g, k, t, alt, alt_name, ck in mb.ITEMS:
        tk = used.get(iid, (None, None))[0] or t
        if tk != "MOF" and tk not in tickers:
            tickers.append(tk)
    df = fetch(tickers)
    out = build(df, daily_of, now)
    out["edition"] = edition
    if out["ok"] == 0:
        print("1日の線: 1本も取れなかったので書かない（前のファイルのまま）", file=sys.stderr)
        return None
    DST.parent.mkdir(exist_ok=True)
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    DST.write_text(body, encoding="utf-8")
    miss = [i["id"] for i in out["items"] if not i["ok"]]
    print(f"market_board_1d.json: {out['ok']}/{out['count']}本 {len(body.encode('utf-8')) // 1024}KB 線なし={miss or 'なし'}")
    return out
