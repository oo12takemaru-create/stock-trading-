# -*- coding: utf-8 -*-
"""受け入れ試験① 第3段の材料作り。

SQL 関数 backtest_trades() と同じ処理（resolve_candidates）を Python で回し、
その出力を JSON に落とす。これを TypeScript 側（applyPortfolioRules + summarize）
に食わせて exp2 と一致すれば、
  「SQL が返す形 → TypeScript の集計」の経路が Python と同じ
ことが（Supabase を立てる前に）確認できる。

    python precompute/tests/dump_candidates_exp2.py
出力: ruletrade-app/scripts/fixtures/exp2_candidates.json
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_engine import (  # noqa: E402
    ExitRule, PriceBook, build_signals, resolve_candidates,
)
from verify_bnf2_exp2 import (  # noqa: E402
    EXIT, INDEX_TICKER, MAX_POS, TICKERS_CSV, load_cache, to_panel,
)

ERA = ("2018-2026", "2018-01-01", None)
THRESHOLDS = [-12, -18, -24]

OUT = os.path.join(
    r"D:\マイドキュメント\Claude\Projects\ruletrade-app", "scripts", "fixtures",
    "exp2_candidates.json")


def main():
    tickers = list(pd.read_csv(TICKERS_CSV)["ticker"])
    prices = load_cache(tickers + [INDEX_TICKER])
    tickers = [t for t in tickers if t in prices]
    cal = pd.DatetimeIndex(prices[INDEX_TICKER].index)
    panel = to_panel(prices, tickers)
    book = PriceBook.from_panel(panel, tickers=tickers, calendar=cal)

    era, s, e = ERA
    cases = []
    for th in THRESHOLDS:
        sig = build_signals(panel, book, [{"column": "dev_25", "op": "lte", "value": th}])
        cand = resolve_candidates(book, sig, EXIT, start=s, end=e)
        cases.append(dict(era=era, th=th, candidates=cand))
        print("th=%d 候補 %d 件" % (th, len(cand)))

    era_cal = cal[cal >= pd.Timestamp(s)]
    payload = dict(
        note="resolve_candidates() の出力。SQL 関数 backtest_trades() と同じ形。",
        exit=dict(hold_days=EXIT.hold_days, stop_pct=EXIT.stop_pct,
                  take_pct=EXIT.take_pct, cost_pct=EXIT.cost_pct),
        max_positions=MAX_POS,
        ticker_order=tickers,
        calendar_from=str(era_cal[0].date()),
        calendar_to=str(era_cal[-1].date()),
        cases=cases,
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("→ %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1024 / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
