# -*- coding: utf-8 -*-
"""銘柄エネルギー「勢い」の遡及検証（積上②・公開の前提）→ 標準出力 ＋ energy_backtest.json

■ 問い
  勢いの帯（20点刻み）が上がるほど、20営業日後のリターンの分布が右に寄るか。
  寄らなければ、勢いは公開しない（起動文 B）。

■ 勢いの定義は本番と同じ
  movers_daily.py の metrics() と同じ式で、毎営業日・全銘柄について4つを計算する。
    vr   = 当日の出来高 ÷ 直近20日（当日を除く・0を除く）の平均   ※5日以上ある日だけ
    c20  = 20営業日前の終値からの騰落率
    hi52 = 直近250日の高値からの距離（0以下。0に近いほど高値圏）
    g25  = 25日移動平均からの乖離率
  その日のユニバース内でそれぞれをパーセンタイル（0〜1）にし、4つの平均×100 を勢い（0〜100）とする。
  本番は全銘柄（約3,700）内のパーセンタイル、ここは340銘柄内のパーセンタイル。母集団が違う点は注記する。

■ 気をつけていること
  - 生存者バイアス: ユニバースは「今も上場している大型・中型340銘柄」。上場廃止した銘柄は入っていない
  - 毎日のデータは20日後リターンが重なる（隣の日とほぼ同じ期間）。p値を水増ししないよう、
    20営業日おきに間引いた「重ならない標本」でも同じ表を作り、判断はそちらで行う
  - 日経平均そのものが上がっている期間なので、帯ごとの平均は「全体平均との差」でも見る

使い方:
  python -X utf8 energy_backtest.py                 # precompute/cache の価格を使う（無ければ yfinance）
"""
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "precompute"))
from fetching import get_prices          # noqa: E402
from universe import ACTIVE_TICKERS      # noqa: E402

OUT = HERE / "energy_backtest.json"      # docs/ ではない（公開しない検証結果）
START, END = "2015-06-01", datetime.now().strftime("%Y-%m-%d")
HORIZON = 20
BANDS = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 101)]
BAND_LABEL = ["0-19", "20-39", "40-59", "60-79", "80-100"]


def metrics_frame(df):
    """1銘柄の日足 → 日ごとの vr, c20, hi52, g25（movers_daily.metrics と同じ式）"""
    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    vol = df["Volume"].astype(float).fillna(0)
    vpos = vol.where(vol > 0)
    prev20 = vpos.shift(1).rolling(20, min_periods=5)
    v20 = prev20.mean()
    vr = vol / v20
    c20 = close / close.shift(HORIZON) - 1
    hi = high.rolling(250, min_periods=1).max()
    hi52 = close / hi - 1
    g25 = close / close.rolling(25, min_periods=25).mean() - 1
    fwd = close.shift(-HORIZON) / close - 1
    return pd.DataFrame({"vr": vr, "c20": c20, "hi52": hi52, "g25": g25, "fwd": fwd})


def table(rows):
    """帯ごとの n・平均・中央値・上昇率・全体との差"""
    allm = rows["fwd"].mean()
    out = []
    for (lo, hi), lab in zip(BANDS, BAND_LABEL):
        s = rows.loc[(rows["score"] >= lo) & (rows["score"] < hi), "fwd"].dropna()
        if len(s) == 0:
            out.append({"band": lab, "n": 0})
            continue
        out.append({
            "band": lab, "n": int(len(s)),
            "mean_pct": round(float(s.mean()) * 100, 2),
            "median_pct": round(float(s.median()) * 100, 2),
            "up_rate": round(float((s > 0).mean()) * 100, 1),
            "diff_vs_all_pt": round(float(s.mean() - allm) * 100, 2),
        })
    return out, round(float(allm) * 100, 2)


def main():
    print(f"ユニバース {len(ACTIVE_TICKERS)}銘柄 / {START}〜{END}")
    px, failed = get_prices(list(ACTIVE_TICKERS), START, END, auto_adjust=True)
    print(f"価格を取れた銘柄 {len(px)}（失敗 {len(failed)}）")

    frames = []
    for t, df in px.items():
        if df is None or len(df) < 300:
            continue
        m = metrics_frame(df)
        m["ticker"] = t
        frames.append(m)
    panel = pd.concat(frames).reset_index().rename(columns={"index": "date", "Date": "date"})
    panel = panel.dropna(subset=["vr", "c20", "hi52", "g25"])
    # 日ごとのパーセンタイル（同じ日の銘柄の中での順位）
    for k in ("vr", "c20", "hi52", "g25"):
        panel[k + "_p"] = panel.groupby("date")[k].rank(pct=True)
    # 1日に20銘柄未満しか無い日は順位が粗いので外す
    cnt = panel.groupby("date")["ticker"].transform("count")
    panel = panel[cnt >= 20]
    panel["score"] = panel[["vr_p", "c20_p", "hi52_p", "g25_p"]].mean(axis=1) * 100
    panel = panel.dropna(subset=["fwd"])
    days = sorted(panel["date"].unique())
    print(f"観測 {len(panel):,}件（{len(days)}営業日 × 銘柄）")

    # 全日（重なりあり）と、20営業日おきの重ならない標本
    every = set(days[::HORIZON])
    non = panel[panel["date"].isin(every)]
    t_all, m_all = table(panel)
    t_non, m_non = table(non)

    # 単調性: 帯の平均が上がっていく順に並んでいるか、帯の番号と平均の順位相関
    def mono(tb):
        ms = [r["mean_pct"] for r in tb if r.get("n")]
        rho = stats.spearmanr(range(len(ms)), ms).correlation if len(ms) >= 3 else None
        inc = all(b >= a for a, b in zip(ms, ms[1:]))
        return {"monotonic": inc, "spearman": None if rho is None else round(float(rho), 3)}

    # 最上位帯と最下位帯の差（重ならない標本でWelch）
    top = non.loc[non["score"] >= 80, "fwd"]
    bot = non.loc[non["score"] < 20, "fwd"]
    p_tb = float(stats.ttest_ind(top, bot, equal_var=False).pvalue) if len(top) > 3 and len(bot) > 3 else None
    # 連続値での順位相関（重ならない標本）
    rho_c = stats.spearmanr(non["score"], non["fwd"])

    res = {
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "universe": f"precompute/universe.py の ACTIVE_TICKERS（{len(ACTIVE_TICKERS)}銘柄・現在も上場している銘柄のみ）",
        "period": {"from": str(pd.Timestamp(days[0]).date()), "to": str(pd.Timestamp(days[-1]).date())},
        "horizon_days": HORIZON,
        "definition": "vr, c20, hi52, g25 の日次パーセンタイル（ユニバース内）の平均×100。式は movers_daily.metrics と同じ",
        "all_days": {"overall_mean_pct": m_all, "bands": t_all, **mono(t_all)},
        "non_overlap": {"overall_mean_pct": m_non, "bands": t_non, **mono(t_non),
                        "n_days": len(every),
                        "top_vs_bottom": {"diff_pt": round(float(top.mean() - bot.mean()) * 100, 2),
                                          "p_welch": None if p_tb is None else round(p_tb, 4)},
                        "spearman_continuous": {"rho": round(float(rho_c.correlation), 4),
                                                "p": round(float(rho_c.pvalue), 4)}},
        "caveats": [
            "現在も上場している340銘柄だけで計算している（上場廃止した銘柄は入っていない＝生存者バイアス）",
            "本番は全銘柄（約3,700）の中の順位、ここは340銘柄の中の順位",
            "毎日の標本は20日後リターンの期間が重なるため、判断は20営業日おきの重ならない標本で行う",
            "過去にこうだったという記録であって、次にどうなるかを示すものではない",
        ],
    }
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")

    def show(lab, tb, mean, mo):
        print(f"\n■ {lab}（全体平均 {mean:+.2f}%）  単調={mo['monotonic']}  順位相関={mo['spearman']}")
        print(f"  {'帯':8s} {'n':>9s} {'平均%':>8s} {'中央%':>8s} {'上昇率':>7s} {'全体との差':>10s}")
        for r in tb:
            if not r.get("n"):
                print(f"  {r['band']:8s} {0:9d}")
                continue
            print(f"  {r['band']:8s} {r['n']:9,d} {r['mean_pct']:+8.2f} {r['median_pct']:+8.2f} "
                  f"{r['up_rate']:6.1f}% {r['diff_vs_all_pt']:+9.2f}pt")
    show("全日（期間が重なる・参考）", t_all, m_all, mono(t_all))
    show(f"20営業日おき・重ならない標本（{len(every)}日・判断に使う）", t_non, m_non, mono(t_non))
    nt = res["non_overlap"]
    print(f"\n  最上位帯(80-100) − 最下位帯(0-19): {nt['top_vs_bottom']['diff_pt']:+.2f}pt "
          f"p={nt['top_vs_bottom']['p_welch']}")
    print(f"  勢い（連続値）と20日後リターンの順位相関: rho={nt['spearman_continuous']['rho']} "
          f"p={nt['spearman_continuous']['p']}")
    print(f"→ {OUT.name}")


if __name__ == "__main__":
    main()
