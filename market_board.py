# -*- coding: utf-8 -*-
"""相場ボード（7分類・24本の日足チャート）→ docs/market_board.json（株レーダー・2026-11-18）

設計書: 「株レーダー 相場ボード・マイボード 設計書 v1」2章・4章（段階2）
本人の決定（2026-11-18）: 世界の値動き（world.html）と同じ扱いで24本を並べる。取得元も同じ yfinance。
                          日本10年国債利回りだけは財務省「国債金利情報」の CSV。

■ 呼ばれ方
  world_fetch.py の main() が world.json を書いた後に write(...) を呼ぶ（world.yml・JST 7:00/16:00/23:00）。
  world_fetch.py がもう取った 420日分の日足（df）をそのまま使い、world.json に無い系列だけ
  ここで追加で取る（EXTRA の4本と財務省 CSV）。ここで何が起きても world.json の公開は止めない
  （呼ぶ側が try/except で包む）。1本が取れなくても残りで出す（その本は前回の値を残さず ok=0 の欠けで出す）。

■ 名前について
  docs/board.json はトップの要約（make_board.py）が使っているので、このファイルは market_board.json。

■ 形（小さくするため、日付の配列 x（日数の差）と終値の配列 c の2本）
  {"v":1,"updated":"...+09:00","edition":"morning|asia|night","base":"YYYY-MM-DD",
   "groups":[{"k":"jp","n":"日本"},...],
   "items":[{"id":"n225","n":"日経平均","g":"jp","k":"idx","t":"^N225","src":"Yahoo!ファイナンス",
             "ok":1,"d":"2026-11-18","p":59024.28,"chg":-123.4,"pct":-0.21,
             "live":0,                       # 1 = 24時間動く為替・暗号資産。d の終値ではなく updated 時点の値
             "h3":..,"l3":..,"h1":..,"l1":..,  # 3か月・1年の終値の高値・安値
             "x":[3,1,1,3,...],"c":[...],   # 直近約1年の日足終値。x は日付: 先頭は base からの日数、以降は前の点からの日数
             "note":"..."}]}
  金利（k=rate）の chg は %pt、pct は null。

■ 終値だけを出す
  23時の回は米国・欧州の当日の足がまだ途中。取引所の引けの時刻（CLOSE）より前の当日の足は捨て、
  前の日の終値を「その日の終値」として出す。為替と暗号資産は終わりの無い市場なので live=1 にして
  「◯月◯日 ◯時時点」とサイトで書く。

依存: yfinance, pandas（world_fetch.py と同じ）。財務省 CSV は urllib。
"""
import io
import json
import math
import sys
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
DST = HERE / "docs" / "market_board.json"

GROUPS = [("jp", "日本"), ("us", "米国"), ("eu", "欧州"), ("as", "アジア"), ("fx", "為替"),
          ("rt", "金利"), ("cm", "商品・暗号資産")]

# (id, 表示名, 分類, 種類, ティッカー, 代わりのティッカー, 代わりのときの表示名, 引けの時刻のキー)
ITEMS = [
    ("n225", "日経平均", "jp", "idx", "^N225", None, None, "tse"),
    ("topix", "TOPIX", "jp", "idx", "^TOPX", "1306.T", "TOPIX（連動ETF 1306）", "tse"),
    ("g250", "グロース250（連動ETF 2516）", "jp", "idx", "2516.T", None, None, "tse"),
    ("nkf", "日経平均先物（CME・円建て）", "jp", "fut", "NIY=F", None, None, "cme"),
    ("dji", "NYダウ", "us", "idx", "^DJI", None, None, "nyse"),
    ("spx", "S&P500", "us", "idx", "^GSPC", None, None, "nyse"),
    ("ixic", "ナスダック総合", "us", "idx", "^IXIC", None, None, "nyse"),
    ("sox", "SOX指数（半導体）", "us", "idx", "^SOX", None, None, "nyse"),
    ("vix", "VIX（恐怖指数）", "us", "vix", "^VIX", None, None, "cboe"),
    ("dax", "独 DAX", "eu", "idx", "^GDAXI", None, None, "xetra"),
    ("ftse", "英 FTSE100", "eu", "idx", "^FTSE", None, None, "lse"),
    ("sse", "上海総合", "as", "idx", "000001.SS", None, None, "sse"),
    ("hsi", "香港 ハンセン", "as", "idx", "^HSI", None, None, "hkex"),
    ("kospi", "韓国 KOSPI", "as", "idx", "^KS11", None, None, "krx"),
    ("sensex", "インド SENSEX", "as", "idx", "^BSESN", None, None, "bse"),
    ("usdjpy", "ドル円", "fx", "fx", "JPY=X", None, None, "live"),
    ("eurjpy", "ユーロ円", "fx", "fx", "EURJPY=X", None, None, "live"),
    ("eurusd", "ユーロドル", "fx", "fx", "EURUSD=X", None, None, "live"),
    ("us10", "米10年国債利回り", "rt", "rate", "^TNX", None, None, "nyse"),
    ("jp10", "日本10年国債利回り", "rt", "rate", "MOF", None, None, "mof"),
    ("wti", "WTI原油", "cm", "cmd", "CL=F", None, None, "cme"),
    ("gold", "金", "cm", "cmd", "GC=F", None, None, "cme"),
    ("copper", "銅", "cm", "cmd", "HG=F", None, None, "cme"),
    ("btc", "ビットコイン", "cm", "crypto", "BTC-USD", None, None, "live"),
]
# world_fetch.py の表に無いので、ここで追加で取るもの（取得の追加は最小限に）
EXTRA = ["2516.T", "NIY=F", "^IXIC", "^BSESN"]

# 当日の足が「終値」になる時刻（現地）。これより前の当日の足は途中なので捨てる
CLOSE = {
    "tse": ("Asia/Tokyo", time(15, 30)),
    "cme": ("America/New_York", time(17, 0)),
    "nyse": ("America/New_York", time(16, 0)),
    "cboe": ("America/New_York", time(16, 15)),
    "xetra": ("Europe/Berlin", time(17, 30)),
    "lse": ("Europe/London", time(16, 30)),
    "sse": ("Asia/Shanghai", time(15, 0)),
    "hkex": ("Asia/Hong_Kong", time(16, 10)),
    "krx": ("Asia/Seoul", time(15, 30)),
    "bse": ("Asia/Kolkata", time(15, 30)),
}

MOF_CUR = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
MOF_ALL = "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
SRC_YF = "Yahoo!ファイナンス"
SRC_MOF = "財務省「国債金利情報」（加工して作成）"
YEAR_DAYS = 366      # チャートに入れる長さ（暦日）
Q_DAYS = 92          # 3か月（暦日）
STALE_DAYS = 10      # 最終の足がこれより古ければ欠け
LAST_SERIES = {}     # build() が使った {id: (ティッカー, 日足の Series)}。market_board_1d.py が読む
BANNED = ["買い", "売り", "推奨", "おすすめ", "必ず", "リアルタイム", "予想", "狙い目", "有望", "チャンス",
          "面白い", "過熱", "底", "天井", "警戒"]


# ---------- 取得 ----------

def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (kaburadar board)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _era_date(s):
    """'R8.11.17' → date(2026,11,17)。S・H・R に対応"""
    s = s.strip()
    base = {"S": 1925, "H": 1988, "R": 2018}.get(s[:1])
    if base is None:
        return None
    try:
        y, m, d = (int(x) for x in s[1:].split("."))
        return date(base + y, m, d)
    except Exception:
        return None


def parse_mof(raw):
    """財務省の CSV（Shift_JIS）→ {date: 10年の利回り}"""
    out = {}
    text = raw.decode("cp932", errors="replace")
    col = None
    for line in text.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if cells and cells[0] == "基準日":
            col = cells.index("10年") if "10年" in cells else None
            continue
        if col is None or len(cells) <= col:
            continue
        d = _era_date(cells[0])
        if d is None:
            continue
        try:
            out[d] = float(cells[col])
        except ValueError:
            pass      # "-"（その年限の発行が無かった日）
    return out


def fetch_jp10(get=_get):
    """日本10年の日次（今月分の CSV ＋ 先月までの CSV）。pandas.Series（index=Timestamp）か None"""
    import pandas as pd
    vals = {}
    for url in (MOF_ALL, MOF_CUR):
        try:
            vals.update(parse_mof(get(url)))
        except Exception as e:
            print(f"  財務省 CSV が取れず（{url}）: {e}", file=sys.stderr)
    if not vals:
        return None
    s = pd.Series(vals).sort_index()
    s.index = pd.to_datetime(s.index)
    return s


def fetch_extra(tickers):
    """world_fetch.py が取らない yfinance の系列。{ticker: 終値の Series（空欄は最新日だけ補完）}"""
    import yfinance as yf
    out = {}
    try:
        df = yf.download(tickers, period="420d", progress=False, auto_adjust=False, group_by="ticker", threads=False)
    except Exception as e:
        print(f"  相場ボードの追加の取得に失敗: {e}", file=sys.stderr)
        return out
    try:
        from yf_latest import fill_latest
    except Exception:
        fill_latest = None
    for t in tickers:
        try:
            c = df[t]["Close"]
        except Exception:
            continue
        if not c.notna().any():
            continue
        out[t] = fill_latest(t, c, verbose=False) if fill_latest else c.dropna()
    return out


# ---------- 組み立て ----------

def _norm(s):
    """Timestamp の index を日付（時刻なし・tz なし）にそろえ、正の値だけ残す"""
    import pandas as pd
    if s is None or not len(s):
        return None
    idx = pd.to_datetime(s.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    s = pd.Series(pd.to_numeric(s.values, errors="coerce"), index=idx.normalize())
    s = s[~s.index.duplicated(keep="last")].sort_index().dropna()
    s = s[s > 0]
    return s if len(s) >= 2 else None


def drop_spikes(s, k):
    """桁が飛んだ点（配信の誤り）を捨てる。1306.T の 2026-03-30・31 が 1/10 の値（37円台）で入っていた。
    前後の点がほぼ同じ（差10%以内）なのに、間の1〜3日だけ前後の両方から40%以上離れているものだけを捨てる。
    VIX は本当に1日で大きく動き、金利は0%近くで比が意味を持たないので触らない。最新の点は前後がそろわないので触らない"""
    if s is None or k in ("vix", "rate") or len(s) < 3:
        return s
    v = s.values

    def far(x, y):
        return x / y > 1.4 or x / y < 1 / 1.4
    bad = set()
    for n in range(1, len(v) - 1):
        for run in (1, 2, 3):          # 2026-03-30・31 のように2日続けて飛んだものもある
            if n + run >= len(v):
                break
            a, c = v[n - 1], v[n + run]
            if abs(c / a - 1) <= 0.10 and all(far(v[m], a) and far(v[m], c) for m in range(n, n + run)):
                bad.update(s.index[n:n + run])
                break
    bad = sorted(bad)
    if bad:
        print(f"  相場ボード: とび値を捨てた {[str(x.date()) for x in bad]}", file=sys.stderr)
        s = s.drop(bad)
    return s


def drop_unfinished(s, close_key, now_utc):
    """当日の足が、その取引所の引けの時刻より前なら捨てる（途中の値を終値として出さない）"""
    if s is None or close_key not in CLOSE:
        return s
    tz, t_close = CLOSE[close_key]
    last = s.index[-1].date()
    close_at = datetime.combine(last, t_close, tzinfo=ZoneInfo(tz))
    if now_utc < close_at:
        s = s.iloc[:-1]
    return s if len(s) >= 2 else None


def rnd(v, k, ref=None):
    """桁はその本の値の大きさで決める（前日比も終値と同じ桁）"""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if k == "rate":
        return round(v, 3)
    a = abs(v if ref is None else ref)
    d = 2 if a >= 100 else 3 if a >= 10 else 4 if a >= 1 else 5
    return round(v, d)


def deltas(xs):
    """[3,4,5,8] → [3,1,1,3]（先頭は base からの日数、2つ目からは前の点からの日数。ファイルを小さくする）"""
    return [x - (xs[k - 1] if k else 0) for k, x in enumerate(xs)]


def sig(v, n=5):
    """チャートの線の点は有効数字5桁（描くには十分。ファイルを小さくする）"""
    if v == 0:
        return 0
    r = round(v, n - 1 - int(math.floor(math.log10(abs(v)))))
    return int(r) if r == int(r) else r


def build_item(spec, s, now_utc, today, base):
    import pandas as pd
    iid, name, g, k, t, alt, alt_name, ck = spec
    row = {"id": iid, "n": name, "g": g, "k": k, "t": t, "src": SRC_MOF if t == "MOF" else SRC_YF, "ok": 0}
    if ck == "live":
        row["live"] = 1
    s = drop_unfinished(s, ck, now_utc)
    if s is None:
        row["note"] = "取得できず"
        return row
    last = s.index[-1].date()
    if (today - last).days > STALE_DAYS:
        row["note"] = f"取得できず（最終 {last.isoformat()}）"
        return row
    p, p0 = float(s.iloc[-1]), float(s.iloc[-2])
    y1 = s[s.index > s.index[-1] - pd.Timedelta(days=YEAR_DAYS)]
    q = s[s.index > s.index[-1] - pd.Timedelta(days=Q_DAYS)]
    row.update({
        "ok": 1, "d": last.isoformat(), "p": rnd(p, k),
        "chg": rnd(p - p0, k, p),
        "pct": None if k == "rate" else round((p / p0 - 1) * 100, 2),
        "h3": rnd(float(q.max()), k), "l3": rnd(float(q.min()), k),
        "h1": rnd(float(y1.max()), k), "l1": rnd(float(y1.min()), k),
        "x": deltas([(ts.date() - base).days for ts in y1.index]),
        "c": [sig(float(v)) for v in y1.values],
    })
    return row


def build(series_of, extra, jp10, now):
    """series_of(ticker) は world_fetch.py の df から終値の Series を返す関数（無ければ None）"""
    now_utc = now.astimezone(ZoneInfo("UTC"))
    today = now.date()
    base = today - timedelta(days=YEAR_DAYS + 7)
    items = []
    for spec in ITEMS:
        iid, name, g, k, t, alt, alt_name, ck = spec
        if t == "MOF":
            s = _norm(jp10)
        elif t in extra:
            s = _norm(extra[t])
        else:
            s = _norm(series_of(t))
        if s is None and alt:
            s2 = _norm(extra.get(alt)) if alt in extra else _norm(series_of(alt))
            if s2 is not None:
                s, spec = s2, (iid, alt_name, g, k, alt, None, None, ck)
        s = drop_spikes(s, k)
        LAST_SERIES[iid] = (spec[4], s)      # 1日の線（market_board_1d.py）の前日終値に使う
        items.append(build_item(spec, s, now_utc, today, base))
    for i in items:
        for b in BANNED:
            if b in i["n"] or b in i.get("note", ""):
                raise SystemExit(f"使わない語『{b}』が相場ボードの {i['id']} に出ました")
    return {"v": 1, "updated": now.isoformat(timespec="seconds"), "base": base.isoformat(),
            "groups": [{"k": k, "n": n} for k, n in GROUPS],
            "ok": sum(i["ok"] for i in items), "count": len(items),
            "source": "Yahoo!ファイナンスの配信値（日足の終値）。日本10年国債利回りは財務省「国債金利情報」",
            "items": items}


def write(series_of, now, edition):
    """world_fetch.py から呼ぶ。docs/market_board.json を書く"""
    extra = fetch_extra(EXTRA)
    jp10 = fetch_jp10()
    out = build(series_of, extra, jp10, now)
    out["edition"] = edition
    if out["ok"] == 0:
        print("相場ボード: 1本も取れなかったので書かない", file=sys.stderr)
        return None
    DST.parent.mkdir(exist_ok=True)
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    DST.write_text(body, encoding="utf-8")
    miss = [i["n"] for i in out["items"] if not i["ok"]]
    print(f"market_board.json: {out['ok']}/{out['count']}本 {len(body.encode('utf-8')) // 1024}KB 取れなかった={miss or 'なし'}")
    return out
