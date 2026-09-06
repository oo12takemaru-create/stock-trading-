# -*- coding: utf-8 -*-
"""受け入れ試験（Phase 2-①拡張）: 公開数字を前計算列だけで再現できるか。

期待値は `集客サイト企画/13_公開数字の検証基準.md` §6:
  10年（2016-09-06〜2026-09-04）・340銘柄（データが揃う337）
  1,826トレード / 勝率52.8% / PF1.55 / 平均+1.49% / 最大DD−27.3%
  累積+369.6% / CAGR16.7% / 平均保有15.4日 / 最大連敗18
合格ライン: トレード数・勝率・PF が ±5% 以内。

計算そのものは portfolio_run.run() を通す（本番バッチ build_portfolio.py と同じ経路）。

    python precompute/tests/verify_published_numbers.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import portfolio_run  # noqa: E402
from pipeline import run_pipeline  # noqa: E402

EXPECTED = {
    "trades": 1826, "win_rate": 52.8, "pf": 1.55, "avg_return": 1.49,
    "max_dd": -27.3, "total_return": 369.6, "cagr": 16.7,
    "avg_hold_days": 15.4, "max_losing_streak": 18,
}
LABELS = [("trades", "トレード数"), ("win_rate", "勝率(%)"), ("pf", "PF"),
          ("avg_return", "平均損益(%)"), ("max_dd", "最大DD(%)"),
          ("total_return", "累積損益(%)"), ("cagr", "CAGR(%)"),
          ("avg_hold_days", "平均保有(日)"), ("max_losing_streak", "最大連敗")]
GATE = ("trades", "win_rate", "pf")


def main():
    panel, market, _ = run_pipeline()
    result, _ = portfolio_run.run(panel, market)
    stats = result["summary"]

    print("
%-18s %12s %12s %9s" % ("項目", "実測", "公開値", "ズレ"))
    ok = True
    for key, label in LABELS:
        got, want = stats.get(key), EXPECTED[key]
        d = abs((got - want) / want) * 100 if (got is not None and want) else float("nan")
        mark = ""
        if key in GATE:
            if not (d <= 5.0):
                ok = False
            mark = " ←合格ライン±5%"
        print("%-18s %12s %12s %8.1f%%%s" % (label, got, want, d, mark))

    print("
制約なしのシグナル: %s 件（参照実装は 6,715 件）"
          % f"{result['unconstrained_signals']:,}")
    print("出口の内訳:", result["exit_reasons"])
    print("期待（§6-2）: 保有期限988 / 損切り555 / 25日MA戻り98 / タイムストップ95 /"
          " +10%利確46 / 半分利確28 / 期末強制決済9 / 50EMA下抜け7")
    print("
戦略別:")
    for r in result["by_rule"]:
        print("  %-20s %5d件 勝率%s%% PF%s 平均%s%%"
              % (r["rule_id"], r["trades"], r["win_rate"], r["pf"], r["avg_return"]))

    print("
判定: %s" % ("合格" if ok else "不合格"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
