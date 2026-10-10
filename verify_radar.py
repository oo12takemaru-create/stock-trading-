# -*- coding: utf-8 -*-
"""検証1：温度計（地合い判定）の遡及検証

温度計の判定は daily_scanner_v2_8_0.detect_market_regime() がそのまま出している。
再実装はせず、その関数を過去の日付で呼び直す。

- 公式の判定＝夕方（その日の終値まで使った判定）なので、測るのは翌営業日の始値から
- リターンは TOPIX の代わりに 1306.T（TOPIX連動ETF）
- 比較対象＝全期間平均
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from daily_scanner_v2_8_0 import detect_market_regime  # noqa: E402

YEARS = 5
ORDER = ["BULLISH", "NEUTRAL", "BEARISH", "PANIC"]
RANK = {"BULLISH": 3, "NEUTRAL": 2, "BEARISH": 1, "PANIC": 0}


def dl(ticker, period="8y", adjust=False):
    # 指数は分割が無いのでスキャナーと同じ auto_adjust=False。
    # 1306.T は株式分割があるので adjust=True（素の値だと分割日に-90%の偽の暴落が出る）
    df = yf.download(ticker, period=period, progress=False, auto_adjust=adjust, threads=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(subset=["Close"])


def main():
    print("指数を取得中…", file=sys.stderr)
    g = {t: dl(t) for t in ("^N225", "^GSPC", "^VIX")}
    topix = dl("1306.T", adjust=True)
    # 1306.T には前後と桁が違う足がある（2026-03-30・03-31 が約1/10の値）。
    # 分割の記録は無く、Yahoo側のデータ不良。前後11日の中央値から3割以上ずれた足を落とす
    med = topix["Close"].rolling(11, center=True, min_periods=3).median()
    bad = (topix["Close"] / med - 1).abs() > 0.30
    if bad.any():
        print("  1306.T の異常な足を除外: "
              + ", ".join(str(d.date()) for d in topix.index[bad]), file=sys.stderr)
        topix = topix[~bad]
    for t, df in list(g.items()) + [("1306.T", topix)]:
        print(f"  {t}: {len(df)}日 {df.index[0].date()} 〜 {df.index[-1].date()}", file=sys.stderr)

    # 判定する日＝日本の営業日（TOPIX ETF が動いた日）
    end = topix.index[-1]
    start = end - pd.Timedelta(days=int(365.25 * YEARS))
    days = [d for d in topix.index if d >= start]

    rows = []
    for d in days:
        regime, reason = detect_market_regime(g, d.date())
        rows.append({"d": d, "regime": regime, "reason": reason})
    df = pd.DataFrame(rows).set_index("d")

    # 翌営業日の始値から測る（その日の終値を使った判定なので先読みにならない）
    idx = topix.index
    o, c = topix["Open"], topix["Close"]
    for n in (1, 5, 20):
        vals = []
        for d in df.index:
            i = idx.get_loc(d)
            if i + n >= len(idx):
                vals.append(None); continue
            entry = float(o.iloc[i + 1])
            exit_ = float(c.iloc[i + n])
            vals.append((exit_ / entry - 1) * 100 if entry else None)
        df[f"r{n}"] = vals
    # 20営業日のあいだの最大下落（翌営業日の始値から終値ベースで測る）
    dd = []
    for d in df.index:
        i = idx.get_loc(d)
        if i + 20 >= len(idx):
            dd.append(None); continue
        entry = float(o.iloc[i + 1])
        lo = float(c.iloc[i + 1:i + 21].min())
        dd.append((lo / entry - 1) * 100 if entry else None)
    df["dd20"] = dd

    def agg(sub, label):
        s = sub.dropna(subset=["r20"])
        if not len(s):
            return None
        return {
            "label": label, "n": len(s),
            "r1_avg": s["r1"].mean(), "r5_avg": s["r5"].mean(),
            "r20_avg": s["r20"].mean(), "r20_med": s["r20"].median(),
            "up20": (s["r20"] > 0).mean() * 100,
            "dd20_avg": s["dd20"].mean(), "dd20_worst": s["dd20"].min(),
        }

    out = {"period": [str(df.index[0].date()), str(df.index[-1].date())],
           "days": len(df), "index": "1306.T（TOPIX連動ETF）", "by_regime": [], "all": None}
    out["all"] = agg(df, "全期間平均")
    for r in ORDER:
        a = agg(df[df["regime"] == r], r)
        if a:
            a["sankou"] = a["n"] < 30
            out["by_regime"].append(a)

    # 判定が悪化した日（ランクが下がった日）から20営業日の最大下落
    df["prev"] = df["regime"].shift(1)
    worse = df[df.apply(lambda x: x["prev"] in RANK and RANK[x["regime"]] < RANK[x["prev"]], axis=1)]
    out["worse"] = agg(worse, "判定が悪化した日")

    print(json.dumps(out, ensure_ascii=False, indent=1, default=float))

    # 出した判定との突き合わせ（直近60日）
    hist = json.loads((REPO / "docs" / "radar_history.json").read_text(encoding="utf-8"))
    hm = {x["d"]: x["regime"] for x in hist["items"]}
    same = diff = 0; diffs = []
    for d in list(df.index)[-80:]:
        k = str(d.date())
        if k in hm:
            if hm[k] == df.loc[d, "regime"]:
                same += 1
            else:
                diff += 1; diffs.append((k, hm[k], df.loc[d, "regime"], df.loc[d, "reason"]))
    print(f"\n■ 実際に出した判定との一致: {same}/{same+diff} 日"
          f"（{same/max(1,same+diff)*100:.1f}%）", file=sys.stderr)
    for x in diffs[:15]:
        print(f"   {x[0]} 公開={x[1]} 遡及={x[2]}  {x[3]}", file=sys.stderr)
    df.to_csv(REPO / "docs" / "radar_backtest.csv", encoding="utf-8-sig")


if __name__ == "__main__":
    main()
