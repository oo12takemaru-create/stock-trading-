# -*- coding: utf-8 -*-
"""受け入れ試験① 第2段: 自前の前計算パイプラインで exp2 を再現できるか。

第1段（verify_bnf2_exp2.py）は BNF2検証のキャッシュ株価をそのまま使って
エンジンの移植が正しいことを確認した。第2段では株価取得と指標計算を
**本番と同じ経路**（fetching.get_prices → metrics.compute_stock_metrics）に
差し替える。ここで出るズレは「元データ差」だけになる。

比較できるのは exp2 の era 2018-2026 だけ（前計算は10年ぶんしか持たないため）。
終了日は BNF2検証のキャッシュ最終日（2026-07-14）に揃える。

    python precompute/tests/verify_bnf2_own_data.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import metrics  # noqa: E402
from backtest_engine import (  # noqa: E402
    ExitRule, PriceBook, build_signals, run_backtest, summarize,
)
from fetching import get_prices  # noqa: E402

BNF2_DIR = r"D:\マイドキュメント\Claude\Projects\株式投資開発\BNF2検証"
TICKERS_CSV = os.path.join(BNF2_DIR, "tickers_kawamura.csv")
EXPECTED_CSV = os.path.join(BNF2_DIR, "results", "exp2_threshold_era.csv")

ERA = "2018-2026"
ERA_START = "2018-01-01"
ERA_END = "2026-07-14"      # BNF2検証のキャッシュ最終日に揃える
THRESHOLDS = list(range(-8, -26, -2))

EXIT = ExitRule(hold_days=10, stop_pct=-4.0, take_pct=8.0, cost_pct=0.2)
MAX_POS = 10


def main():
    config.ensure_dirs()
    tickers = list(pd.read_csv(TICKERS_CSV)["ticker"])
    # 移動平均のウォームアップぶんを余分に取る
    start = dt.date(2016, 1, 1)
    end = dt.date(2026, 7, 20)

    print("自前パイプラインで %d 銘柄を取得（%s 〜 %s）" % (len(tickers), start, end))
    prices, failed = get_prices(tickers, start, end, auto_adjust=config.DEFAULT_AUTO_ADJUST)
    gdata, _ = get_prices(["^N225"], start, end, auto_adjust=config.DEFAULT_AUTO_ADJUST,
                          kind="global")
    if failed:
        print("  ★取得できなかった銘柄: %s" % failed)

    have = [t for t in tickers if t in prices and len(prices[t]) > 200]
    print("  使える銘柄: %d / %d" % (len(have), len(tickers)))

    frames = []
    for t in have:
        m = metrics.compute_stock_metrics(prices[t], t)
        frames.append(m)
    panel = pd.concat(frames)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["date"] = pd.to_datetime(panel["date"])

    cal = pd.DatetimeIndex(gdata["^N225"].index)
    book = PriceBook.from_panel(panel, tickers=have, calendar=cal)
    era_cal = cal[(cal >= pd.Timestamp(ERA_START)) & (cal <= pd.Timestamp(ERA_END))]
    print("  市場カレンダー: %s 〜 %s (%d 営業日)"
          % (era_cal[0].date(), era_cal[-1].date(), len(era_cal)))

    expected = pd.read_csv(EXPECTED_CSV)
    rows = []
    for th in THRESHOLDS:
        sig = build_signals(panel, book, [{"column": "dev_25", "op": "lte", "value": th}])
        res = run_backtest(book, sig, EXIT, max_positions=MAX_POS,
                           start=ERA_START, end=ERA_END)
        m = summarize(res, era_cal, capital_slots=MAX_POS)
        exp = expected[(expected.era == ERA) & (expected.th == th)].iloc[0]
        rows.append(dict(th=th,
                         trades=m["trades"], exp_trades=int(exp.trades),
                         win=m["win_rate"], exp_win=float(exp["win"]),
                         pf=m["pf"], exp_pf=float(exp.pf),
                         avg=m["avg_return"], exp_avg=float(exp.avg)))

    out = pd.DataFrame(rows)
    for col in ("trades", "win", "pf", "avg"):
        base = out["exp_" + col].abs().replace(0, np.nan)
        out["d_" + col] = (out[col] - out["exp_" + col]).abs() / base * 100.0

    pd.set_option("display.width", 200)
    print()
    print(out[["th", "trades", "exp_trades", "d_trades", "win", "exp_win", "d_win",
               "pf", "exp_pf", "d_pf"]]
          .to_string(index=False, float_format=lambda v: "%.2f" % v))

    worst = out[["d_trades", "d_win", "d_pf"]].max()
    print("\n最大ズレ(%%): 取引 %.2f / 勝率 %.2f / PF %.2f"
          % (worst.d_trades, worst.d_win, worst.d_pf))
    ok = bool((worst <= 5.0).all())
    print("判定: %s (合格ライン ±5%%)" % ("合格" if ok else "不合格"))
    out.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "verify_bnf2_own_data_result.csv"), index=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
