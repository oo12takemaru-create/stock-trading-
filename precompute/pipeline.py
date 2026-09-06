# -*- coding: utf-8 -*-
"""前計算パイプラインの本体（取得 → 指標計算 → 相場環境）。

precompute_metrics.py（ローカルに CSV/Parquet を出す 1-A のツール）と
build_metrics.py / update_metrics.py（Supabase へ入れる本番バッチ）が
同じ計算を通るように、ここに1本化してある。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import config
import metrics
from fetching import get_prices
from universe import JAPAN_STOCKS

# 引継ぎ.md §16-4 判断2（2026-09-05 Fable）: ユニバースの正は daily_scanner_v2_8_0.py の
# STOCKS（341銘柄）。238（integrated_backtest 側）はその部分集合。
UNIVERSE_VERSION = "jp340_v2_8_0"


def default_window(years: int) -> tuple[dt.date, dt.date]:
    """取得期間。移動平均200日・52週高値のウォームアップに約1.2年ぶん余分に取る。"""
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(years * 365.25) + 460)
    return start, end


def run_pipeline(tickers=None, years: int = config.DEFAULT_YEARS,
                 auto_adjust: bool = config.DEFAULT_AUTO_ADJUST,
                 refresh: bool = False, log=print):
    """(daily_metrics, market_condition, sectors) を返す。

    daily_metrics はウォームアップぶんを落とし、直近 years 年ぶんだけにする。
    """
    config.ensure_dirs()
    tickers = list(JAPAN_STOCKS.keys()) if tickers is None else list(tickers)
    start, end = default_window(years)

    log("ユニバース %d 銘柄 / 取得期間 %s 〜 %s / auto_adjust=%s"
        % (len(tickers), start, end, auto_adjust))

    log("[1/4] グローバル指数を取得（^N225 / ^GSPC / ^VIX）")
    global_data, gfail = get_prices(list(config.GLOBAL_TICKERS.keys()), start, end,
                                    auto_adjust=auto_adjust, kind="global",
                                    refresh=refresh, log=log)
    if gfail:
        log("  ★グローバル指数の取得に失敗: %s（相場環境の判定精度が落ちる）" % gfail)

    log("[2/4] 個別株を取得")
    prices, failed = get_prices(tickers, start, end, auto_adjust=auto_adjust,
                                kind="prices", refresh=refresh, log=log)
    log("  取得できた銘柄: %d / %d" % (len(prices), len(tickers)))

    log("[3/4] 前計算列を算出")
    frames, skipped = [], []
    for i, t in enumerate(tickers, 1):
        df = prices.get(t)
        if df is None or len(df) < 200:
            skipped.append(t)
            continue
        frames.append(metrics.compute_stock_metrics(df, t))
        if i % 50 == 0 or i == len(tickers):
            log("  %d/%d 銘柄" % (i, len(tickers)))
    if skipped:
        log("  ★データ不足でスキップ %d 銘柄: %s" % (len(skipped), ", ".join(skipped[:20])))
    if not frames:
        raise RuntimeError("処理できる銘柄がありませんでした")

    panel = pd.concat(frames)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["date"] = pd.to_datetime(panel["date"])
    cutoff = pd.Timestamp(end) - pd.Timedelta(days=int(years * 365.25))
    panel = panel[panel["date"] >= cutoff]
    panel = metrics.add_rs_rank(panel)
    panel = panel.sort_values(["date", "ticker"]).reset_index(drop=True)
    panel = panel[metrics.ALL_COLUMNS]
    # DB に real で入る列は、ここで同じ精度に丸めておく。
    # 投入側と検証側で丸め方が違うと、境目の比較で判定が食い違う（metrics.REAL_COLUMNS 参照）。
    panel = metrics.round_to_db_precision(panel)
    panel["universe_version"] = UNIVERSE_VERSION
    log("  daily_metrics: %s 行 / %s 列" % (f"{len(panel):,}", len(panel.columns)))

    log("[4/4] market_condition を算出")
    jp_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    market = metrics.compute_market_condition(global_data, jp_dates, panel=panel)
    market = market[[c for c in metrics.MARKET_COLUMNS if c in market.columns]].copy()
    # 件数の列は Postgres 側が integer。float のまま送ると "162.0" で弾かれる
    for c in ("advancing", "declining", "new_high", "new_low"):
        if c in market.columns:
            market[c] = market[c].round().astype("Int64")
    log("  market_condition: %d 行  regime内訳=%s"
        % (len(market), market["regime"].value_counts().to_dict()))

    sectors = pd.DataFrame(
        [{"ticker": t, "name": v[0], "sector": v[1], "market": "プライム",
          "is_active": t in prices} for t, v in JAPAN_STOCKS.items()])

    return panel, market, sectors
