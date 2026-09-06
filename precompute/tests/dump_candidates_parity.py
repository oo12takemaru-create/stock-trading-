# -*- coding: utf-8 -*-
"""SQL 関数 backtest_trades() と Python の resolve_candidates() を突き合わせる材料。

本番と同じパイプライン（run_pipeline）で作った panel を使い、いくつかの条件で
「1シグナル1件」の約定を Python 側で解決して JSON に落とす。
Supabase に同じ panel を投入したあと、ruletrade-app の
`npm run test:backtest-sql` が同じ条件で RPC を叩き、1件ずつ突き合わせる。

    python precompute/tests/dump_candidates_parity.py
出力: ruletrade-app/scripts/fixtures/sql_parity.json
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import metrics  # noqa: E402
from backtest_engine import (  # noqa: E402
    ExitRule, PriceBook, build_signals, resolve_candidates,
)
from pipeline import UNIVERSE_VERSION, run_pipeline  # noqa: E402

OUT = os.path.join(
    r"D:\マイドキュメント\Claude\Projects\ruletrade-app", "scripts", "fixtures",
    "sql_parity.json")

FROM = "2019-01-01"
TO = "2026-08-31"

# 規定値ルール3本と同じ形の条件を混ぜて、比率列・複数条件・環境フィルタを通す
CASES = [
    dict(name="BNF逆張り（規定値）",
         conditions=[{"column": "dev_25", "op": "lte", "value": -15}],
         exit=dict(hold_days=10, stop_pct=-4.0, take_pct=8.0), regimes=None),
    dict(name="モメンタム（20日高値ブレイク）",
         conditions=[{"column": "high_20_ratio", "op": "gte", "value": 100},
                     {"column": "vol_ratio_20", "op": "gte", "value": 1.5},
                     {"column": "dev_200", "op": "gte", "value": 0},
                     {"column": "high_52w_ratio", "op": "gte", "value": 95}],
         exit=dict(hold_days=10, stop_pct=-8.0, take_pct=None), regimes=None),
    dict(name="ミネルヴィニ・テンプレート",
         conditions=[{"column": "dev_200", "op": "gt", "value": 0},
                     {"column": "ma_150_over_200", "op": "gt", "value": 0},
                     {"column": "high_52w_ratio", "op": "gte", "value": 75},
                     {"column": "ret_126", "op": "gte", "value": 5}],
         exit=dict(hold_days=20, stop_pct=-8.0, take_pct=None), regimes=None),
    dict(name="環境フィルタあり（弱気・パニックのみ）",
         conditions=[{"column": "dev_25", "op": "lte", "value": -18}],
         exit=dict(hold_days=5, stop_pct=-8.0, take_pct=None),
         regimes=["BEARISH", "PANIC"]),
    dict(name="損切りなし・利確なし（時間切れだけ）",
         conditions=[{"column": "dev_25", "op": "lte", "value": -20},
                     {"column": "vol_ratio_20", "op": "gte", "value": 1.1}],
         exit=dict(hold_days=3, stop_pct=None, take_pct=None), regimes=None),
]


def main():
    panel, market, _ = run_pipeline()
    panel["date"] = pd.to_datetime(panel["date"])
    market["date"] = pd.to_datetime(market["date"])

    tickers = list(dict.fromkeys(panel["ticker"]))
    cal = pd.DatetimeIndex(sorted(market["date"].unique()))
    book = PriceBook.from_panel(panel, tickers=tickers, calendar=cal)

    regime_by_i = market.set_index("date")["regime"].reindex(book.dates).to_numpy()

    out_cases = []
    for case in CASES:
        rule = ExitRule(hold_days=case["exit"]["hold_days"],
                        stop_pct=case["exit"]["stop_pct"],
                        take_pct=case["exit"]["take_pct"],
                        cost_pct=0.0)
        sig = build_signals(panel, book, case["conditions"])
        allow = None
        if case["regimes"]:
            allow = pd.Series(regime_by_i).isin(case["regimes"]).to_numpy()
        cand = resolve_candidates(book, sig, rule, allow_entry=allow,
                                  start=FROM, end=TO)
        out_cases.append(dict(name=case["name"], conditions=case["conditions"],
                              exit=case["exit"], regimes=case["regimes"],
                              candidates=cand))
        print("%-34s 候補 %6d 件" % (case["name"], len(cand)))

    payload = dict(
        note="resolve_candidates() の出力。SQL 関数 backtest_trades() と1件ずつ一致すること。",
        universe=UNIVERSE_VERSION,
        # ★取得に失敗した銘柄があると Supabase 側と母集団がズレるので、
        #   この材料を作ったときの銘柄一覧を必ず残し、SQL 側も同じ集合で叩く。
        tickers=tickers,
        period=dict(**{"from": FROM, "to": TO}),
        cases=out_cases,
    )
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("→ %s (%.1f MB)" % (OUT, os.path.getsize(OUT) / 1024 / 1024))
    _ = metrics  # 参照だけ（列定義がこのファイル経由であることを示す）
    return 0


if __name__ == "__main__":
    sys.exit(main())
