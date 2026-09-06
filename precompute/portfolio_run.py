# -*- coding: utf-8 -*-
"""規定値ポートフォリオを1回まわして、公開数字と同じ形の結果を作る。

build_portfolio.py（Supabaseへ保存）と
tests/verify_published_numbers.py（受け入れ試験）の両方がここを通る。
＝ 検証したものと本番に載るものが必ず同じ計算になる。
"""
from __future__ import annotations

import pandas as pd

import config
import portfolio_engine as pe
from preset_rules import PRESET_RULES
from universe import JAPAN_STOCKS

START, END = "2016-09-06", "2026-09-04"

NEEDED_COLUMNS = [
    "close", "low", "dev_25", "dev_50", "dev_75", "dev_200", "vol_ratio_20",
    "bb_pos_1_5", "high_20_ratio", "high_52w_ratio", "ret_5d", "day_change",
    "knife_guard", "minervini_entry", "ema_50_pos",
]


def build_market(market: pd.DataFrame):
    """相場環境・S&P の変化率・HALT・PANIC突入からの日数を日付で引ける形にする。"""
    m = market.copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values("date")
    mkt = {
        "regime": dict(zip(m["date"], m["regime"])),
        "sp1": dict(zip(m["date"], m["sp500_change_1d"].fillna(0.0))),
        "sp3": dict(zip(m["date"], m["sp500_change_3d"].fillna(0.0))),
    }
    halt = dict(zip(m["date"], m["is_halt"].fillna(False).astype(bool)))
    panic_days, streak = {}, -1
    for d, r in zip(m["date"], m["regime"]):
        streak = streak + 1 if r == "PANIC" else -1
        panic_days[d] = streak if streak >= 0 else -1
    return mkt, halt, panic_days


def run(panel: pd.DataFrame, market: pd.DataFrame,
        max_positions=10, max_per_sector=3, start=START, end=END, log=print):
    """(合算の結果 dict, 採用されたトレード) を返す。"""
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    mkt, halt, panic_days = build_market(market)
    sector_risk = config.SECTOR_RISK_MULTIPLIER

    all_trades = []
    tickers = sorted(panel["ticker"].unique())
    for k, t in enumerate(tickers, 1):
        sub = panel[panel["ticker"] == t].sort_values("date")
        dates = pd.DatetimeIndex(sub["date"].to_numpy())
        cols = {}
        for c in NEEDED_COLUMNS:
            v = sub[c].to_numpy()
            cols[c] = v.astype(bool) if v.dtype == bool else v.astype("float64")
        sector = JAPAN_STOCKS.get(t, ("", ""))[1]
        all_trades += pe.simulate_ticker(t, sector, cols, dates, PRESET_RULES,
                                         mkt, sector_risk, start=start, end=end)
        if k % 100 == 0 or k == len(tickers):
            log("  %d/%d 銘柄  候補 %s 件" % (k, len(tickers), f"{len(all_trades):,}"))

    after_cb = pe.apply_circuit_breaker(all_trades, halt, panic_days)
    final = pe.apply_concurrent_limit(after_cb, max_positions=max_positions,
                                      max_per_sector=max_per_sector)
    log("  制約なし %s → サーキットブレーカー後 %s → 同時保有%d・同セクター%d 後 %s"
        % (f"{len(all_trades):,}", f"{len(after_cb):,}", max_positions,
           max_per_sector, f"{len(final):,}"))

    by_rule = {}
    for t in final:
        by_rule.setdefault(t.strategy, []).append(t)

    yearly = {}
    for t in final:
        yearly.setdefault(t.exit_date.year, []).append(t)

    result = {
        "summary": pe.calc_stats(final),
        "unconstrained_signals": len(all_trades),
        "after_circuit_breaker": len(after_cb),
        "by_rule": [
            {"rule_id": k, **pe.calc_stats(v)} for k, v in sorted(by_rule.items())
        ],
        "yearly": [
            {"year": y, **pe.calc_stats(v)} for y, v in sorted(yearly.items())
        ],
        "exit_reasons": pe.exit_reason_counts(final),
        "period": {"from": start, "to": end},
        "basis": {
            "universe": "daily_scanner_v2_8_0.py の STOCKS（340銘柄。データが揃うぶんで計算）",
            "tickers_used": len(tickers),
            "regime": "日次スキャナ版",
            "price": "判定・約定とも終値（当日終値で条件成立→翌営業日の終値で約定）",
            "stop": "安値が水準に触れた日の終値",
            "position": "現物・同時保有%d銘柄まで・1トレードのリスク1%%・複利" % max_positions,
            "cost": "手数料・スリッページ・税は未考慮",
        },
    }
    return result, final
