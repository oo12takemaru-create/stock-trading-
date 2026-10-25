# -*- coding: utf-8 -*-
"""実データでの地合い判定の一致（手で回す・ネットワークを使う。CI には入れない）。

  python tests/check_regime_parity_realdata.py [--out 判定の表.csv]

^N225・^GSPC・^VIX を前計算と同じ取り方（precompute/fetching.get_prices・同じ期間）で1回取り、
同じ入力を3つに渡して、検証期間の頭（config.MATERIAL_START）から最新までの東証の全営業日で比べる。
  1. 前計算 metrics.compute_market_condition の regime   と 検証エンジン detect_market_regime
  2. 前計算の nikkei_1m_change                           と 検証エンジンの signals["n225_1m_change"]（値の完全一致）
  3. 前計算の is_halt                                    と 検証エンジン precompute_halt_only_states
  4. 本番スキャナー daily_scanner_v2_8_0.detect_market_regime と 検証エンジン
どれか1日でも違えば exit 1。
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "precompute"))
import config                                 # noqa: E402
import daily_scanner_v2_8_0 as SCAN           # noqa: E402
import integrated_backtest_v2_8_0 as ENGINE   # noqa: E402
import metrics                                # noqa: E402
import trading_calendar as tc                 # noqa: E402
from fetching import get_prices               # noqa: E402
from pipeline import default_window           # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--out", default="")
a = p.parse_args()

start, end = default_window()
g, failed = get_prices(list(config.GLOBAL_TICKERS), start, end, auto_adjust=config.DEFAULT_AUTO_ADJUST,
                       kind="global", refresh=True)
if failed:
    raise SystemExit("取得できなかった指数: %s" % failed)
last = min(g["^N225"].index.max(), tc.table_last_day())
dates = tc.sessions(config.MATERIAL_START, last)
print("指数: %s / 比べる日: %d 営業日（%s〜%s）" % ({k: len(v) for k, v in g.items()}, len(dates),
                                            dates[0].date(), dates[-1].date()))
md = metrics.compute_market_condition(g, dates).set_index("date")
halt = ENGINE.precompute_halt_only_states(g, list(dates), {"halt_vix": float(config.HALT_RULES["halt_vix"]),
                                                          "halt_n225_drop": float(config.HALT_RULES["halt_n225_drop"]),
                                                          "halt_cooldown": int(config.HALT_RULES["halt_cooldown"])})
rows, ng = [], {"regime": 0, "chg": 0, "halt": 0, "scanner": 0}
for d in dates:
    e, sig = ENGINE.detect_market_regime(g, d)
    s = SCAN.detect_market_regime(g, d.date())[0]
    pre = md.at[d, "regime"]
    ce = sig.get("n225_1m_change")
    cp = md.at[d, "nikkei_1m_change"]
    he = halt[d][0] == "HALT"
    hp = bool(md.at[d, "is_halt"])
    ng["regime"] += pre != e
    ng["chg"] += ce is not None and not (cp == ce)
    ng["halt"] += hp != he
    ng["scanner"] += s != e
    rows.append({"date": d.date(), "precompute": pre, "engine": e, "scanner": s, "chg_pre": cp, "chg_engine": ce,
                 "halt_pre": hp, "halt_engine": he, "regime_scanner_old": md.at[d, "regime_scanner"]})
tab = pd.DataFrame(rows)
if a.out:
    tab.to_csv(a.out, index=False)
print("地合いの内訳（前計算）: %s" % tab["precompute"].value_counts().to_dict())
print("不一致: 前計算×エンジンの地合い %d 日 / 1ヶ月騰落の値 %d 日 / HALT %d 日 / 本番スキャナー×エンジン %d 日"
      % (ng["regime"], ng["chg"], ng["halt"], ng["scanner"]))
print("参考: 旧スキャナー版（regime_scanner）とエンジンが違う日 %d 日" % int((tab["regime_scanner_old"] != tab["engine"]).sum()))
bad = sum(ng.values())
print("OK" if not bad else "NG")
sys.exit(1 if bad else 0)
