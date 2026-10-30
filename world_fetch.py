# -*- coding: utf-8 -*-
"""世界の値動きの地図 → docs/world.json（株レーダー・2026-10-30）

起動文: kaburadar\\起動文_実務_世界の値動きの地図_2026-10-30.md

■ 何を出すか
  株価指数（米州・欧州・日本とアジア）・商品・為替・米国の金利・暗号資産・恐怖指数（VIX）の約36銘柄。
  各銘柄: 終値・1日/5日/20日/年初来の騰落・円で持っていた場合の同じ4つ・最終の取引日・通貨。
  items[] の列:
    t  ティッカー（Yahoo の表記）  n  日本語名  g  まとまり（am 米州 / eu 欧州 / as 日本とアジア /
       cm 商品 / fx 為替 / rt 金利 / cr 暗号資産 / vx 恐怖指数）
    k  種類（idx 株価指数 / cmd 商品 / fx 為替 / rate 金利 / crypto 暗号資産 / vix）
    cur 通貨  ok 取れたか  d 最終の取引日  p 終値（金利は %）
    r  {d1,d5,d20,ytd} 現地通貨の騰落%（金利だけは %pt の変化）
    y  {d1,d5,d20,ytd} 円で持っていたら の騰落%（(1+現地)×(1+対円の為替)−1。円建て・金利・為替・VIX は null）
    yen 為替だけ: 1 = 円の絡む組（ドル円など。上がる＝円安）。色は「円から見て」（サイト側）
    sub 1 = まとめの1行の「世界の株」に数えない（SOX指数）  note 注記
  先頭: updated（日本時間）・edition（morning 7時の確定版 / asia 16時のアジア引け / night 23時の欧州・米国の途中）・
        final（確定版か）・summary（{d1,d5,d20,ytd} まとめの1行。機械的な事実だけ）・counts（上昇した指数の数）

■ 「昨夜〜今朝」で締める: 7:00 の回（edition=morning）だけ docs/world_days/YYYY-MM-DD.json（日本時間の日付）に積む。
  ただし S&P500 の最終の取引日が前の記録と同じとき（日曜・月曜の朝、米国の休場明けでない朝）は積まない。
  東証の休場日でも海外は動くので、東証の営業日とは無関係に判定する。

■ データは yfinance（日足）。JPX のデータは使わない。新しい有料データも使わない。
  TOPIX（^TOPX）は yfinance で取れないので、TOPIX 連動 ETF（1306.T）で代用し注記する。
■ 判断の言葉（リスクオン/オフ・お金が流れた・買い/売り・推奨）は書かない。まとめの1行は生成時に検査して止める。

使い方: python -X utf8 world_fetch.py   （world.yml・cron-worker が JST 7:00 / 16:00 / 23:00 に起こす）
依存: yfinance, pandas
"""
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
DST = DOCS / "world.json"
DAYS = DOCS / "world_days"

# (ティッカー, 日本語名, まとまり, 種類, 通貨, 追加)
ITEMS = [
    # 米州
    ("^GSPC", "S&P500", "am", "idx", "USD", {}),
    ("^NDX", "ナスダック100", "am", "idx", "USD", {}),
    ("^DJI", "NYダウ", "am", "idx", "USD", {}),
    ("^RUT", "ラッセル2000", "am", "idx", "USD", {}),
    ("^SOX", "SOX指数（半導体）", "am", "idx", "USD", {"sub": 1}),
    ("^BVSP", "ブラジル ボベスパ", "am", "idx", "BRL", {}),
    # 欧州
    ("^FTSE", "英 FTSE100", "eu", "idx", "GBP", {}),
    ("^GDAXI", "独 DAX", "eu", "idx", "EUR", {}),
    ("^FCHI", "仏 CAC40", "eu", "idx", "EUR", {}),
    ("^STOXX50E", "ユーロ・ストックス50", "eu", "idx", "EUR", {}),
    # 日本とアジア
    ("^N225", "日経平均", "as", "idx", "JPY", {}),
    ("^TOPX", "TOPIX", "as", "idx", "JPY", {"alt": "1306.T"}),
    ("^HSI", "香港 ハンセン", "as", "idx", "HKD", {}),
    ("000001.SS", "上海総合", "as", "idx", "CNY", {}),
    ("^KS11", "韓国 KOSPI", "as", "idx", "KRW", {}),
    ("^TWII", "台湾 加権", "as", "idx", "TWD", {}),
    ("^NSEI", "インド NIFTY50", "as", "idx", "INR", {}),
    ("^AXJO", "豪 ASX200", "as", "idx", "AUD", {}),
    # 商品
    ("GC=F", "金", "cm", "cmd", "USD", {}),
    ("SI=F", "銀", "cm", "cmd", "USD", {}),
    ("CL=F", "WTI原油", "cm", "cmd", "USD", {}),
    ("BZ=F", "北海ブレント", "cm", "cmd", "USD", {}),
    ("NG=F", "天然ガス", "cm", "cmd", "USD", {}),
    ("HG=F", "銅", "cm", "cmd", "USD", {}),
    # 為替（yen=1 は円の絡む組。上がる＝円安）
    ("JPY=X", "ドル円", "fx", "fx", "JPY", {"yen": 1}),
    ("EURJPY=X", "ユーロ円", "fx", "fx", "JPY", {"yen": 1}),
    ("AUDJPY=X", "豪ドル円", "fx", "fx", "JPY", {"yen": 1}),
    ("EURUSD=X", "ユーロドル", "fx", "fx", "USD", {}),
    ("DX-Y.NYB", "ドル指数", "fx", "fx", "USD", {}),
    # 金利（%pt の変化で出す）
    ("^IRX", "米3か月金利", "rt", "rate", "USD", {}),
    ("^TNX", "米10年金利", "rt", "rate", "USD", {}),
    ("^TYX", "米30年金利", "rt", "rate", "USD", {}),
    # 暗号資産
    ("BTC-USD", "ビットコイン", "cr", "crypto", "USD", {}),
    ("ETH-USD", "イーサリアム", "cr", "crypto", "USD", {}),
    # 恐怖指数
    ("^VIX", "VIX（恐怖指数）", "vx", "vix", "USD", {}),
]
GROUPS = [("am", "米州"), ("eu", "欧州"), ("as", "日本とアジア"), ("cm", "商品"), ("fx", "為替"),
          ("rt", "米国の金利"), ("cr", "暗号資産"), ("vx", "恐怖指数")]
# 現地通貨 → 円の為替（円で持っていたら の計算に使う）
FX_JPY = {"USD": "JPY=X", "EUR": "EURJPY=X", "GBP": "GBPJPY=X", "HKD": "HKDJPY=X", "CNY": "CNYJPY=X",
          "KRW": "KRWJPY=X", "TWD": "TWDJPY=X", "INR": "INRJPY=X", "AUD": "AUDJPY=X", "BRL": "BRLJPY=X"}
PERIODS = [("d1", 1), ("d5", 5), ("d20", 20), ("ytd", None)]
PERIOD_JA = {"d1": "1日", "d5": "5日", "d20": "20日", "ytd": "年初来"}
STALE_DAYS = 10      # 最終の取引日がこれより古ければ「取得できず」
FLOOR = 25           # これ未満しか取れなければ書かない（全体の約7割）
BANNED = ["リアルタイム", "リスクオン", "リスクオフ", "お金が流れ", "資金が流れ", "買い", "売り", "推奨", "おすすめ",
          "狙い目", "チャンス", "割安", "割高"]


def check_words(text, where):
    for b in BANNED:
        if b in text:
            raise SystemExit(f"使わない語『{b}』が {where} に出ました: {text[:160]}")


def edition_of(now):
    h = now.hour
    if 5 <= h < 11:
        return "morning", "7時の確定版（昨夜の米国・欧州の引け、前日のアジアの引け）"
    if 11 <= h < 20:
        return "asia", "16時の回（日本とアジアの引け後）"
    return "night", "23時の回（欧州の途中・米国の寄り後の途中経過）"


def series(df, t):
    try:
        s = df[t]["Close"].dropna()
    except Exception:
        return None
    s = s[s > 0] if len(s) else s
    return s if len(s) >= 2 else None


def base_index(s, n):
    """n 営業日前（行で数える）。年初来は前年最後の終値。足りなければ None"""
    if n is None:
        y0 = s.index[-1].year
        prev = s[s.index < f"{y0}-01-01"]
        return prev.index[-1] if len(prev) else None
    return s.index[-1 - n] if len(s) > n else None


def asof(fx, ts):
    """ts 以前で最後の為替（同じ日が無ければ直前の日）"""
    sub = fx[fx.index <= ts]
    return float(sub.iloc[-1]) if len(sub) else None


def rnd(v, d=2):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, d)


def one(df, t, name, g, k, cur, extra, today):
    row = {"t": t, "n": name, "g": g, "k": k, "cur": cur, "ok": 0}
    row.update({x: v for x, v in extra.items() if x in ("sub", "yen")})
    s = series(df, t)
    if s is None and extra.get("alt"):
        s = series(df, extra["alt"])
        if s is not None:
            row["t"] = extra["alt"]
            row["note"] = "TOPIXは指数そのものが取れないため、TOPIX連動のETF（1306）の値で代用"
    if s is None:
        row["note"] = "取得できず"
        return row
    last_ts = s.index[-1]
    d = last_ts.strftime("%Y-%m-%d")
    row["d"] = d
    if (today - date.fromisoformat(d)).days > STALE_DAYS:
        row["note"] = f"取得できず（最終 {d}）"
        return row
    p = float(s.iloc[-1])
    row["p"] = rnd(p, 4 if p < 10 else 2)
    r, y = {}, {}
    fx = None
    if k in ("idx", "cmd", "crypto") and cur != "JPY":
        fx = series(df, FX_JPY[cur])
    for key, n in PERIODS:
        b = base_index(s, n)
        if b is None:
            r[key] = None
            y[key] = None
            continue
        p0 = float(s.loc[b])
        if k == "rate":
            r[key] = rnd(p - p0, 3)          # %pt
            y[key] = None
            continue
        r[key] = rnd((p / p0 - 1) * 100)
        if k in ("idx", "cmd", "crypto"):
            if cur == "JPY":
                y[key] = r[key]
            elif fx is not None:
                f1, f0 = asof(fx, last_ts), asof(fx, b)
                y[key] = rnd(((p * f1) / (p0 * f0) - 1) * 100) if f1 and f0 else None
            else:
                y[key] = None
        else:
            y[key] = None
    row["r"] = r
    row["y"] = y if any(v is not None for v in y.values()) else None
    row["ok"] = 1
    return row


def fmt_pct(v, d=1):
    return f"{v:+.{d}f}%"


def summary_line(items, key):
    """まとめの1行（機械的・事実だけ）。例: 世界の株は17指数中10が上昇。米10年金利 +0.06%pt。円は対ドルで0.4%安。金 +1.2%、WTI原油 -2.1%"""
    by = {i["t"]: i for i in items if i.get("ok")}
    idx = [i for i in items if i.get("ok") and i["k"] == "idx" and not i.get("sub") and i["r"].get(key) is not None]
    up = sum(1 for i in idx if i["r"][key] > 0)
    head = "" if key == "d1" else f"{PERIOD_JA[key]}で見ると、"
    parts = [f"{head}世界の株は{len(idx)}指数中{up}が上昇"]
    tnx = by.get("^TNX")
    if tnx and tnx["r"].get(key) is not None:
        parts.append(f"米10年金利 {tnx['r'][key]:+.2f}%pt")
    uj = by.get("JPY=X")
    if uj and uj["r"].get(key) is not None:
        v = uj["r"][key]
        # ドル円が上がる＝円安。円の対ドルの変化は 1/(1+v)−1
        yen = (1 / (1 + v / 100) - 1) * 100
        parts.append("円は対ドルでほぼ変わらず" if abs(yen) < 0.05 else f"円は対ドルで{abs(yen):.1f}%{'安' if yen < 0 else '高'}")
    cm = [f"{by[t]['n']} {fmt_pct(by[t]['r'][key])}" for t in ("GC=F", "CL=F") if by.get(t) and by[t]["r"].get(key) is not None]
    s = "。".join(parts) + ("。" + "、".join(cm) if cm else "") + "。"
    check_words(s, f"まとめの1行（{key}）")
    return s, {"up": up, "n": len(idx)}


def save_day(out):
    """7時の確定版を日付アーカイブに積む。S&P500 の取引日が前の記録と同じなら積まない"""
    spx = next((i.get("d") for i in out["items"] if i["t"] == "^GSPC" and i.get("ok")), None)
    if not spx:
        print("S&P500 が取れていないので、日付アーカイブには積まない")
        return
    DAYS.mkdir(parents=True, exist_ok=True)
    ix_p = DAYS / "index.json"
    try:
        ix = json.loads(ix_p.read_text(encoding="utf-8"))
    except Exception:
        ix = {"dates": [], "spx": {}}
    day = out["updated"][:10]
    spx_map = ix.get("spx") or {}
    prev = [d for d in sorted(ix.get("dates") or []) if d < day]
    if prev and spx_map.get(prev[-1]) == spx:
        print(f"S&P500 の取引日（{spx}）が前の記録 {prev[-1]} と同じなので、日付アーカイブには積まない（週末・米国の休場明けでない朝）")
        return
    rec = dict(out)
    rec["date"] = day
    (DAYS / f"{day}.json").write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    spx_map[day] = spx
    ds = sorted(set(ix.get("dates") or []) | {day})
    ix_p.write_text(json.dumps({"updated": out["updated"], "dates": ds, "spx": spx_map}, ensure_ascii=False,
                               separators=(",", ":")), encoding="utf-8")
    print(f"アーカイブ: world_days/{day}.json（{len(ds)}日分・S&P500 は {spx} の引け）")


def main():
    import yfinance as yf
    now = datetime.now(JST)
    force_final = "--final" in sys.argv
    tickers = sorted({t for t, *_ in ITEMS} | {x[5]["alt"] for x in ITEMS if x[5].get("alt")} | set(FX_JPY.values()))
    df = yf.download(tickers, period="420d", progress=False, auto_adjust=False, group_by="ticker", threads=True)
    items = [one(df, t, n, g, k, cur, ex, now.date()) for t, n, g, k, cur, ex in ITEMS]
    ok = sum(i["ok"] for i in items)
    if ok < FLOOR:
        print(f"取れた銘柄が少なすぎる: {ok}/{len(items)}（{FLOOR}未満）。書かない", file=sys.stderr)
        sys.exit(1)
    ed, ed_label = edition_of(now)
    if force_final:
        ed, ed_label = "morning", edition_of(now.replace(hour=7))[1]
    summ, counts = {}, {}
    for key, _ in PERIODS:
        summ[key], counts[key] = summary_line(items, key)
    out = {"updated": now.isoformat(timespec="seconds"), "edition": ed, "edition_label": ed_label,
           "final": ed == "morning", "groups": [{"k": k, "n": n} for k, n in GROUPS],
           "periods": [{"k": k, "n": PERIOD_JA[k]} for k, _ in PERIODS],
           "summary": summ, "counts": counts, "count": len(items), "ok": ok,
           "source": "Yahoo!ファイナンスの配信値（日足）", "items": items}
    DOCS.mkdir(exist_ok=True)
    DST.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    miss = [i["n"] for i in items if not i["ok"]]
    print(f"world.json: {ok}/{len(items)}銘柄 回={ed} 取れなかった={miss or 'なし'}")
    print("まとめ:", summ["d1"])
    if ed == "morning":
        save_day(out)


if __name__ == "__main__":
    main()
