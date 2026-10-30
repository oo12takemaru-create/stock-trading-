# -*- coding: utf-8 -*-
"""世界の値動きの地図の「答え合わせ」→ docs/world_stats.json（株レーダー・2026-10-30）

「過去に同じくらい動いた翌営業日、日経平均はどうだったか」を条件ごとに集計する。
  元: yfinance の日足（約10年）。JPX のデータは使わない。
  翌営業日の日経平均 = その条件の日（各銘柄の日足の日付 t）より後で、最初に東京が開いた日の終値の前日比。
    例: 米国の木曜（t）に S&P500 が −2% → 日本の金曜の日経平均の前日比（金曜の終値 ÷ 木曜の終値 − 1）。
    東京が休みの日を挟むときは、休み明けの最初の日を見る（同じ東京の日に2つの t が当たることがある）。
  集計: 件数・平均・中央値・上昇した割合。比べる物差しとして「全部の日」の同じ4つも出す。
  効かない結果（全部の日とほとんど変わらない）もそのまま出す。並べ替えや取捨はしない。

■ 条件（起動文の初版）: S&P500 ±2%以上／ナスダック100 ±2.5%以上／米10年金利 ±0.15%pt以上／ドル円 ±1%以上／
  WTI原油 ±5%以上／金 ±2%以上／VIX +20%以上／SOX指数 ±3%以上（取れなければ省略）
■ 週1回の再計算で足りる。前回から6日たっていなければ何もしない（--force で必ず作る）。

使い方: python -X utf8 world_stats.py [--force]
依存: yfinance, pandas
"""
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
DST = HERE / "docs" / "world_stats.json"
YEARS = 10
NK = "^N225"

# (id, ティッカー, 名前, 単位, しきい値, 向き)
CONDS = [
    ("spx_up", "^GSPC", "S&P500", "%", 2.0, 1), ("spx_dn", "^GSPC", "S&P500", "%", 2.0, -1),
    ("ndx_up", "^NDX", "ナスダック100", "%", 2.5, 1), ("ndx_dn", "^NDX", "ナスダック100", "%", 2.5, -1),
    ("sox_up", "^SOX", "SOX指数（半導体）", "%", 3.0, 1), ("sox_dn", "^SOX", "SOX指数（半導体）", "%", 3.0, -1),
    ("tnx_up", "^TNX", "米10年金利", "%pt", 0.15, 1), ("tnx_dn", "^TNX", "米10年金利", "%pt", 0.15, -1),
    ("usdjpy_up", "JPY=X", "ドル円", "%", 1.0, 1), ("usdjpy_dn", "JPY=X", "ドル円", "%", 1.0, -1),
    ("wti_up", "CL=F", "WTI原油", "%", 5.0, 1), ("wti_dn", "CL=F", "WTI原油", "%", 5.0, -1),
    ("gold_up", "GC=F", "金", "%", 2.0, 1), ("gold_dn", "GC=F", "金", "%", 2.0, -1),
    ("vix_up", "^VIX", "VIX（恐怖指数）", "%", 20.0, 1),
]


def cond_text(name, unit, th, sgn):
    if unit == "%pt":
        return f"{name}が{'+' if sgn > 0 else '−'}{th:g}%pt以上{'上昇' if sgn > 0 else '低下'}"
    if name == "ドル円":
        return f"ドル円が{'+' if sgn > 0 else '−'}{th:g}%以上（{'円安' if sgn > 0 else '円高'}）"
    return f"{name}が{'+' if sgn > 0 else '−'}{th:g}%以上{'上昇' if sgn > 0 else '下落'}"


def stats(xs):
    if not xs:
        return {"n": 0, "mean": None, "median": None, "up": None}
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 3), "median": round(statistics.median(xs), 3),
            "up": round(sum(1 for x in xs if x > 0) / len(xs) * 100, 1)}


def main():
    import yfinance as yf
    if DST.exists() and "--force" not in sys.argv:
        try:
            old = json.loads(DST.read_text(encoding="utf-8"))
            age = datetime.now(JST) - datetime.fromisoformat(old["updated"])
            if age < timedelta(days=6):
                print(f"world_stats.json は {age.days}日前に作ったので今回は作り直さない（週1回）")
                return
        except Exception:
            pass
    tickers = sorted({c[1] for c in CONDS} | {NK})
    df = yf.download(tickers, period=f"{YEARS + 1}y", progress=False, auto_adjust=False, group_by="ticker", threads=True)
    nk = df[NK]["Close"].dropna()
    nk = nk[nk.index >= nk.index[-1] - timedelta(days=365 * YEARS)]
    if len(nk) < 2000:
        print(f"日経平均の日足が少なすぎる: {len(nk)}", file=sys.stderr)
        sys.exit(1)
    nk_ret = (nk / nk.shift(1) - 1).dropna() * 100     # 東京の各日の前日比
    nk_dates = list(nk_ret.index)
    start = nk.index[0]

    def next_tokyo(t):
        """t より後で最初に東京が開いた日（前日比が出せる日）"""
        lo, hi = 0, len(nk_dates)
        while lo < hi:
            m = (lo + hi) // 2
            if nk_dates[m] <= t:
                lo = m + 1
            else:
                hi = m
        return nk_dates[lo] if lo < len(nk_dates) else None

    rows = []
    for cid, t, name, unit, th, sgn in CONDS:
        try:
            s = df[t]["Close"].dropna()
        except Exception:
            s = None
        if s is None or len(s) < 500:
            print(f"{name}（{t}）が取れないので省略")
            continue
        s = s[s.index >= start - timedelta(days=7)]
        if unit == "%pt":
            ch = (s - s.shift(1)).dropna()
        else:
            prev = s.shift(1)
            ok = (s > 0) & (prev > 0)          # WTI の 2020-04 のマイナス価格は除く
            ch = ((s / prev - 1) * 100)[ok].dropna()
        ch = ch[ch.index >= start]
        hit = ch[ch >= th] if sgn > 0 else ch[ch <= -th]
        xs, recent = [], []
        for d, v in hit.items():
            nd = next_tokyo(d)
            if nd is None:
                continue
            r = float(nk_ret.loc[nd])
            xs.append(r)
            recent.append({"d": d.strftime("%Y-%m-%d"), "x": round(float(v), 3), "nd": nd.strftime("%Y-%m-%d"),
                           "nk": round(r, 3)})
        rows.append({"id": cid, "t": t, "n": name, "unit": unit, "th": th, "dir": sgn,
                     "text": cond_text(name, unit, th, sgn), **stats(xs),
                     "last": recent[-1]["d"] if recent else None, "recent": recent[-5:][::-1]})
    base = stats([float(x) for x in nk_ret.values])
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "since": nk_ret.index[0].strftime("%Y-%m-%d"), "until": nk_ret.index[-1].strftime("%Y-%m-%d"),
           "years": YEARS, "base": base, "rows": rows,
           "note": "翌営業日の日経平均＝条件の日より後で最初に東京が開いた日の前日比（終値）。出所: Yahoo!ファイナンスの配信値（日足）"}
    DST.parent.mkdir(exist_ok=True)
    DST.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"world_stats.json: {len(rows)}条件 {out['since']}〜{out['until']} 全部の日 {base}")
    for r in rows:
        print(f"  {r['text']}: {r['n']}件 平均{r['mean']} 中央値{r['median']} 上昇{r['up']}%")


if __name__ == "__main__":
    main()
