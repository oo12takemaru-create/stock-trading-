# -*- coding: utf-8 -*-
"""前計算バッチ 1-A: daily_metrics / market_condition / sectors をローカルに出す。

Supabase へ入れるのは build_metrics.py（全期間）と update_metrics.py（日次差分）。
こちらは中身を目で見たいとき・受け入れ試験をオフラインで回したいとき用に
CSV / Parquet を書き出すだけのツール。計算そのものは pipeline.run_pipeline() に
一本化してあるので、本番バッチと必ず同じ数字になる。

使い方:
    python precompute/precompute_metrics.py --limit 5     # まず5銘柄で動作確認
    python precompute/precompute_metrics.py               # 全238銘柄 x 10年
    python precompute/precompute_metrics.py --refresh     # キャッシュを無視して再取得

出力（out/）:
    daily_metrics.parquet / daily_metrics.csv
    market_condition.parquet / market_condition.csv
    sectors.csv / precompute_log.txt

Parquet を書くので pyarrow が要る（**このツールだけ**。本番バッチと GitHub Actions は不要）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from pipeline import run_pipeline  # noqa: E402
from universe import JAPAN_STOCKS  # noqa: E402

LOG_LINES = []


def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    LOG_LINES.append(line)


def parse_args():
    p = argparse.ArgumentParser(description="ルールトレード 前計算バッチ 1-A")
    p.add_argument("--years", type=int, default=config.DEFAULT_YEARS, help="取得年数（既定10年）")
    p.add_argument("--limit", type=int, default=0, help="先頭N銘柄だけ処理（動作確認用）")
    p.add_argument("--refresh", action="store_true", help="キャッシュを無視して再ダウンロード")
    p.add_argument("--auto-adjust", dest="auto_adjust", default="true",
                   choices=["true", "false"],
                   help="true=既存エンジンと同じ調整済み / false=日次スキャナと同じ未調整")
    p.add_argument("--out-prefix", default="", help="出力ファイル名の接頭辞")
    p.add_argument("--no-csv", action="store_true", help="CSVを書かない（Parquetのみ）")
    return p.parse_args()


def main():
    args = parse_args()
    t0 = time.time()

    tickers = list(JAPAN_STOCKS.keys())
    if args.limit:
        tickers = tickers[:args.limit]

    log("=" * 70)
    log("前計算バッチ 1-A（ローカル出力）")
    log("=" * 70)
    panel, market, sectors = run_pipeline(
        tickers=tickers, years=args.years,
        auto_adjust=(args.auto_adjust == "true"),
        refresh=args.refresh, log=log)

    log("[出力]")
    pre, od = args.out_prefix, config.OUT_DIR
    os.makedirs(od, exist_ok=True)
    written = []
    for name, df in (("daily_metrics", panel), ("market_condition", market)):
        p = os.path.join(od, pre + name + ".parquet")
        df.to_parquet(p, index=False)
        written.append(p)
        if not args.no_csv:
            p = os.path.join(od, pre + name + ".csv")
            df.to_csv(p, index=False, float_format="%.6f", encoding="utf-8")
            written.append(p)
    p = os.path.join(od, pre + "sectors.csv")
    sectors.to_csv(p, index=False, encoding="utf-8")
    written.append(p)

    for p in written:
        log("  %s  (%.1f MB)" % (os.path.basename(p), os.path.getsize(p) / 1024 / 1024))
    log("期間: %s 〜 %s / 銘柄数 %d / 所要 %.1f 分" % (
        panel["date"].min().date(), panel["date"].max().date(),
        panel["ticker"].nunique(), (time.time() - t0) / 60))

    with open(os.path.join(od, pre + "precompute_log.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(LOG_LINES) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
