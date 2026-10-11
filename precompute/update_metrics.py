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


#: 受け取った値を疑う閾値。分割の記録が無いのに1日でこれ以上動いたら止める
MAX_JUMP = 0.5


def check_incoming(panel, recent, log=print):
    """★正本に足す前に、受け取った値を疑う★（2026-10-11）

    提供元（yfinance）は黙って壊れた値を返すことがある。
    2026-10-02 には10銘柄の調整係数が期間の途中で跳ね、東京海上の
    2018-09-20 の終値が 1,350円ではなく 0.6円で返ってきた。それが正本を上書きし、
    公開数字の最大DDが4ポイント良く見えた。過去の材料は固定したので、
    **足す側で止めないと、固定した材料の続きに壊れた値が積み上がる**。

    見るもの（1つでも当たれば止める。何も足さない）
      1. 足す日が、固定した検証期間（portfolio_run.END まで）に入っていない
      2. 保存済みの最後の日について、今回取った終値と保存済みの終値が
         ±MAX_JUMP 以上ずれていない（ずれる＝提供元が過去を作り直した。
         分割か、10/02 のような壊れ方。どちらも人が見るまで足さない）
      3. 足す日どうしで、終値が1日に ±MAX_JUMP 以上動いていない
    """
    from concurrent.futures import ThreadPoolExecutor

    import json
    from portfolio_run import END

    errs = []
    first_new = min(recent)

    # 1. 固定した検証期間に書かない
    if first_new <= pd.Timestamp(END):
        errs.append("足す日 %s が、固定した検証期間（〜%s）に入っています。"
                    % (first_new.date(), END))

    # 2. 保存済みの最後の日と、今回取った値が食い違っていないか
    url_base, key = supabase_io.credentials()

    def last_stored(ticker):
        ep = ("%s/rest/v1/daily_metrics?select=date,close&ticker=eq.%s"
              "&date=lt.%s&order=date.desc&limit=1"
              % (url_base, ticker, first_new.date().isoformat()))
        _, body = supabase_io._request("GET", ep, key)
        rows = json.loads(body.decode("utf-8"))
        return ticker, (rows[0] if rows else None)

    fresh = panel.set_index(["ticker", "date"])["close"]
    tickers = sorted(panel["ticker"].unique())
    with ThreadPoolExecutor(max_workers=6) as ex:
        stored = dict(ex.map(last_stored, tickers))

    shifted = []
    for t in tickers:
        row = stored.get(t)
        if not row or row.get("close") in (None, 0):
            continue
        d = pd.Timestamp(row["date"])
        new = fresh.get((t, d))
        if new is None or pd.isna(new):
            continue
        r = float(new) / float(row["close"])
        if abs(r - 1) >= MAX_JUMP:
            shifted.append("%s %s 保存済み %.2f / 今回 %.2f（比 %.4f）"
                           % (t, d.date(), row["close"], new, r))
    if shifted:
        errs.append("保存済みの値と今回取った値が大きく食い違う銘柄が %d あります"
                    "（提供元が過去を作り直した＝分割か、壊れた値）:\n    %s"
                    % (len(shifted), "\n    ".join(shifted[:10])))

    # 3. 足す日どうしの1日の動き
    jumps = []
    for t, g in panel[panel["date"] >= first_new - pd.Timedelta(days=10)].groupby("ticker"):
        c = g.sort_values("date").set_index("date")["close"]
        ch = c.pct_change().dropna()
        ch = ch[ch.index >= first_new]
        big = ch[ch.abs() >= MAX_JUMP]
        for day, v in big.items():
            jumps.append("%s %s %+.1f%%" % (t, day.date(), v * 100))
    if jumps:
        errs.append("1日で ±%d%% 以上動いた値があります（分割の記録が無い限り疑う）:\n    %s"
                    % (MAX_JUMP * 100, "\n    ".join(jumps[:10])))

    if errs:
        raise SystemExit(
            "★正本に足すのを止めました（何も書いていません）★\n  "
            + "\n  ".join(errs)
            + "\n  人が確かめてから足してください。提供元の一時的な壊れ方なら、"
              "時間をおいて再実行すると直ることがあります。"
        )
    log("  受け取った値の検査: 異常なし（%d 銘柄）" % len(tickers))


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

    # ★書く前に受け取った値を疑う★ 当たれば何も書かずに止まる（--dry-run でも見る）
    check_incoming(panel, recent, log=log)

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
