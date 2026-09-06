# -*- coding: utf-8 -*-
"""日次差分 → Supabase へ投入（平日の夕方）。

直近 --days 営業日ぶんだけ upsert する。祝日・取得失敗に耐えるため:
  - 新しい行が1行も無くても異常終了しない（祝日は「何もしない」が正しい）
  - 前計算列は移動平均200日などを含むので、計算自体は全期間ぶん回す
    （キャッシュが効くので実時間は短い）

    python precompute/update_metrics.py --env-file ../ruletrade-app/.env.local
    python precompute/update_metrics.py --days 5 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import metrics  # noqa: E402
import supabase_io  # noqa: E402
from pipeline import run_pipeline  # noqa: E402


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def parse_args():
    p = argparse.ArgumentParser(description="前計算バッチ（日次差分）")
    p.add_argument("--days", type=int, default=5, help="直近N営業日ぶんを upsert（既定5）")
    p.add_argument("--years", type=int, default=config.DEFAULT_YEARS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--env-file", default="")
    p.add_argument("--chunk", type=int, default=supabase_io.CHUNK_ROWS)
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    t0 = time.time()

    log("=" * 70)
    log("前計算バッチ（日次差分・直近 %d 営業日）" % args.days)
    log("=" * 70)
    # 日次は必ず最新を取りに行く（キャッシュが古いと1日ぶん増えない）
    panel, market, _ = run_pipeline(years=args.years, refresh=True, log=log)

    all_dates = sorted(pd.DatetimeIndex(panel["date"].unique()))
    recent = all_dates[-args.days:]
    if not recent:
        log("対象日がありません。何もせず終了します。")
        return 0
    log("対象日: %s 〜 %s (%d 営業日)"
        % (recent[0].date(), recent[-1].date(), len(recent)))

    panel_d = panel[panel["date"].isin(recent)]
    market_d = market[pd.to_datetime(market["date"]).isin(recent)]
    log("daily_metrics %s 行 / market_condition %d 行"
        % (f"{len(panel_d):,}", len(market_d)))

    if args.dry_run:
        log("--dry-run のため投入しません")
        return 0

    supabase_io.upsert("market_condition", supabase_io.frame_to_records(market_d),
                       "date", log=log, chunk=args.chunk)
    supabase_io.upsert("daily_metrics", supabase_io.frame_to_records(panel_d[metrics.DB_COLUMNS + ["universe_version"]]),
                       "date,ticker", log=log, chunk=args.chunk)

    supabase_io.analyze()

    latest = supabase_io.max_value("daily_metrics", "date")
    log("確認: daily_metrics の最新日 = %s / 総行数 %s"
        % (latest, f"{supabase_io.count_rows('daily_metrics'):,}"))
    log("完了 (%.1f 分)" % ((time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
