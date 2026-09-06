# -*- coding: utf-8 -*-
"""全期間の再計算 → Supabase へ投入（初回・月初）。

    python precompute/build_metrics.py --env-file ../ruletrade-app/.env.local
    python precompute/build_metrics.py --dry-run          # 投入せず件数だけ見る
    python precompute/build_metrics.py --limit 5          # 動作確認

GitHub Actions からは SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY を Secrets で渡す。
テーブルは ruletrade-app/supabase/schema.sql を Supabase の SQL Editor で
先に流しておくこと（DDL は service_role キーでは実行できない）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import metrics  # noqa: E402
import supabase_io  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from universe import JAPAN_STOCKS  # noqa: E402

LOG_LINES = []


def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG_LINES.append(line)


def parse_args():
    p = argparse.ArgumentParser(description="前計算バッチ（全期間・Supabase 投入）")
    p.add_argument("--years", type=int, default=config.DEFAULT_YEARS)
    p.add_argument("--limit", type=int, default=0, help="先頭N銘柄だけ（動作確認用）")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して再取得")
    p.add_argument("--dry-run", action="store_true", help="Supabase へ投入しない")
    p.add_argument("--env-file", default="", help="KEY=VALUE 形式の設定ファイル")
    p.add_argument("--chunk", type=int, default=supabase_io.CHUNK_ROWS)
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    t0 = time.time()

    tickers = list(JAPAN_STOCKS.keys())
    if args.limit:
        tickers = tickers[:args.limit]

    log("=" * 70)
    log("前計算バッチ（全期間）")
    log("=" * 70)
    panel, market, sectors = run_pipeline(tickers=tickers, years=args.years,
                                          refresh=args.refresh, log=log)
    log("期間: %s 〜 %s / 銘柄 %d"
        % (panel["date"].min().date(), panel["date"].max().date(), panel["ticker"].nunique()))

    if args.dry_run:
        log("--dry-run のため投入しません（daily_metrics %s 行 / market_condition %d 行）"
            % (f"{len(panel):,}", len(market)))
        return 0

    # BNF のセクター別閾値と相場環境の倍率（実装パラメータ。会員には見せない）
    log("[投入] sector_thresholds / regime_multipliers")
    supabase_io.upsert(
        "sector_thresholds",
        [{"sector": k, "threshold": v} for k, v in config.SECTOR_BNF_THRESHOLDS.items()],
        "sector", log=log)
    supabase_io.upsert(
        "regime_multipliers",
        [{"regime": k, "multiplier": v} for k, v in config.REGIME_BNF_MULTIPLIER.items()
         if k in ("BULLISH", "NEUTRAL", "BEARISH", "PANIC")],
        "regime", log=log)

    log("[投入] sectors")
    supabase_io.upsert("sectors", supabase_io.frame_to_records(sectors, date_cols=()),
                       "ticker", log=log, chunk=args.chunk)
    log("[投入] market_condition")
    supabase_io.upsert("market_condition", supabase_io.frame_to_records(market),
                       "date", log=log, chunk=args.chunk)
    # ★索引を付けたまま81.5万行を入れると途中で止まる（2026-09-05 実測）。
    #   空にして副索引を外してから入れ、あとで張り直す（バルクロードの定石）。
    log("[投入] daily_metrics（表を空にして副索引を外す）")
    supabase_io.truncate_metrics()
    supabase_io.drop_secondary_indexes(log=log)
    supabase_io.upsert("daily_metrics", supabase_io.frame_to_records(panel[metrics.DB_COLUMNS + ["universe_version"]]),
                       "date,ticker", log=log, chunk=args.chunk)

    log("[索引] 張り直し")
    supabase_io.rebuild_secondary_indexes(log=log)
    log("[統計情報] analyze_metrics()")
    supabase_io.analyze()

    log("確認: daily_metrics %s 行 / 最新日 %s"
        % (f"{supabase_io.count_rows('daily_metrics'):,}",
           supabase_io.max_value("daily_metrics", "date")))
    log("完了 (%.1f 分)" % ((time.time() - t0) / 60))

    os.makedirs(config.OUT_DIR, exist_ok=True)
    with open(os.path.join(config.OUT_DIR, "build_metrics_log.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
