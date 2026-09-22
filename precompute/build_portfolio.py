# -*- coding: utf-8 -*-
"""規定値ポートフォリオの成績を計算して Supabase の portfolio_results に入れる。

■ なぜ「結果」を保存するのか
分割利確・サーキットブレーカー・同セクター上限は日付順の状態遷移なので、
SQL に書き写すと計算が3箇所（Python・SQL・TypeScript）に散る。
公開数字と同じ計算は Python 1つだけに置き、結果だけを配る。
会員が値を動かしたときの「ルール単独」は SQL 側の backtest_trades() が即時に返す。
（引継ぎ.md §19 相談③ Fable決定(4)）

    python precompute/build_portfolio.py --env-file ../ruletrade-app/.env.local
    python precompute/build_portfolio.py --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import panel_from_db  # noqa: E402
import portfolio_run  # noqa: E402
import supabase_io  # noqa: E402
from pipeline import UNIVERSE_VERSION, run_pipeline  # noqa: E402

RESULT_ID = "preset_v2_8_0"


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def parse_args():
    p = argparse.ArgumentParser(description="規定値ポートフォリオの成績を作る")
    p.add_argument("--max-positions", type=int, default=10)
    p.add_argument("--max-per-sector", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--env-file", default="")
    p.add_argument("--save-trades", default="", help="明細をCSVに出す（任意）")
    # ★公開数字は正本から作る★（2026-09-22・Fable 案2）
    #   過去は変わらないものなので、毎回 yfinance に取りに行くこと自体が
    #   間違いだった。取得が一過性に失敗すると母集団が変わり、同じ10年でも
    #   別物の数字になる。正本（daily_metrics）から読めば毎回同じになる。
    #   --from-yfinance は、正本を作り直すとき（pipeline を流す側）だけに使う。
    p.add_argument("--from-yfinance", action="store_true",
                   help="正本ではなく yfinance から取り直して計算する（再現しない）")
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    t0 = time.time()

    log("=" * 70)
    log("規定値ポートフォリオ（同時保有%d・同セクター%d）"
        % (args.max_positions, args.max_per_sector))
    log("=" * 70)
    if args.from_yfinance:
        log("★ yfinance から取り直します（この結果は再現しません）")
        panel, market, _ = run_pipeline(log=log)
    else:
        log("正本（daily_metrics / market_condition）から読みます")
        panel, market = panel_from_db.load_panel(log=log)
    result, trades = portfolio_run.run(
        panel, market, max_positions=args.max_positions,
        max_per_sector=args.max_per_sector, log=log)

    s = result["summary"]
    log("合算: %s件 / 勝率%s%% / PF%s / 最大DD%s%% / 累積%s%% / CAGR%s%%"
        % (s["trades"], s["win_rate"], s["pf"], s["max_dd"],
           s["total_return"], s["cagr"]))
    for r in result["by_rule"]:
        log("  %-20s %5d件 勝率%s%% PF%s" % (r["rule_id"], r["trades"], r["win_rate"], r["pf"]))

    if args.save_trades:
        import pandas as pd
        pd.DataFrame([t.__dict__ for t in trades]).to_csv(
            args.save_trades, index=False, encoding="utf-8")
        log("明細を書き出し: %s" % args.save_trades)

    if args.dry_run:
        log("--dry-run のため保存しません")
        print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
        return 0

    data_through = str(panel["date"].max())[:10]
    supabase_io.upsert("portfolio_results", [{
        "id": RESULT_ID,
        "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "data_through": data_through,
        "params": {
            "universe_version": UNIVERSE_VERSION,
            "max_positions": args.max_positions,
            "max_per_sector": args.max_per_sector,
            "period": result["period"],
        },
        "result": result,
    }], "id", log=log)
    log("保存しました（id=%s / data_through=%s）" % (RESULT_ID, data_through))
    log("完了 (%.1f 分)" % ((time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
