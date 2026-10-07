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

■ 結果と扱い（2026-10-07 実行・2026-10-08 Fable判断）
  勢いは20日後リターンと無関係だった（重ならない標本45,321件で最上位−最下位 +0.29pt p=0.31、
  順位相関0.007）。4指標を1つずつ見てもゼロ。よって銘柄エネルギー（戦闘力）は公開しない。
  定義を変えて測り直すこと、5日後だけ並んだ結果（p=0.038）を追うことは、しない
  （当たるまで検証を変えない）。報告: kaburadar/_報告_積上②.md

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
    # 報告の追加確認用（方向でなく大きさ／期間を変える）
    fwd5 = close.shift(-5) / close - 1
    fwd60 = close.shift(-60) / close - 1
    r = close.pct_change()
    vol20 = r[::-1].rolling(20, min_periods=15).std()[::-1].shift(-1)   # 翌日からの20日の日次ボラ
    return pd.DataFrame({"vr": vr, "c20": c20, "hi52": hi52, "g25": g25, "fwd": fwd,
                         "fwd5": fwd5, "fwd60": fwd60, "vol20": vol20})


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


def diagnostics(non):
    """報告に載せた追加確認3つ（重ならない標本）。新しい検定は足さない（Fable 2026-10-08）
      1. 4指標を1つずつ：5分位ごとの20日後平均と順位相関
      2. 方向でなく大きさ：帯ごとの20日後|騰落|と、次の20日の日次ボラ
      3. 期間を変える：5日後・60日後の帯ごとの平均"""
    out = {}
    for k in ("vr", "c20", "hi52", "g25"):
        q = pd.qcut(non[k + "_p"], 5, labels=["低", "2", "3", "4", "高"])
        g = non.groupby(q, observed=True)["fwd"].mean() * 100
        rho = stats.spearmanr(non[k + "_p"], non["fwd"])
        out[k] = {"quintile_mean_pct": [round(float(v), 2) for v in g.values],
                  "rho": round(float(rho.correlation), 4), "p": round(float(rho.pvalue), 4)}
    band = pd.cut(non["score"], [b[0] for b in BANDS] + [101], labels=BAND_LABEL, right=False)
    absf = non["fwd"].abs()
    base = absf.mean()
    g2 = non.assign(absfwd=absf).groupby(band, observed=True).agg(
        n=("fwd", "size"), abs_mean=("absfwd", "mean"), vol=("vol20", "mean"))
    rho2 = stats.spearmanr(non["score"], absf)
    out["magnitude"] = {
        "bands": {str(k): {"n": int(v.n), "abs_mean_pct": round(float(v.abs_mean) * 100, 2),
                           "ratio": round(float(v.abs_mean / base), 2),
                           "vol20_pct": round(float(v.vol) * 100, 2)} for k, v in g2.iterrows()},
        "rho": round(float(rho2.correlation), 4), "p": float(rho2.pvalue)}
    for h in (5, 60):
        g3 = non.groupby(band, observed=True)[f"fwd{h}"].mean() * 100
        rho3 = stats.spearmanr(non["score"], non[f"fwd{h}"], nan_policy="omit")
        out[f"fwd{h}"] = {"band_mean_pct": [round(float(v), 2) for v in g3.values],
                          "rho": round(float(rho3.correlation), 4), "p": round(float(rho3.pvalue), 4)}
    return out


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
    res["diagnostics"] = diagnostics(non)
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
    dg = res["diagnostics"]
    print("\n■ 追加確認（報告に載せたもの）")
    for k in ("vr", "c20", "hi52", "g25"):
        print(f"  {k:5s} 5分位 " + " ".join(f"{v:+.2f}" for v in dg[k]["quintile_mean_pct"])
              + f"  順位相関 {dg[k]['rho']:+.4f} p={dg[k]['p']:.4f}")
    print("  |騰落|の全体比 " + " ".join(f"{b}:{v['ratio']:.2f}" for b, v in dg["magnitude"]["bands"].items()))
    for h in (5, 60):
        print(f"  {h:2d}日後 " + " ".join(f"{v:+.2f}" for v in dg[f"fwd{h}"]["band_mean_pct"])
              + f"  順位相関 {dg[f'fwd{h}']['rho']:+.4f} p={dg[f'fwd{h}']['p']:.4f}")
    print(f"→ {OUT.name}")


if __name__ == "__main__":
    main()
