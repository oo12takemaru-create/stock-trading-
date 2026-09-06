# -*- coding: utf-8 -*-
"""受け入れ試験① 第1段: 移植したエンジンが BNF2検証 exp2 を再現するか。

BNF2検証の**キャッシュ済み株価をそのまま使う**ことで、
「エンジンの定義の差」と「元データの差」を切り分ける。
ここが合わなければ移植ミス、合ってから自前データで走らせて初めて
「元データ差」の話ができる（起動文 §4 の相談条件の切り分け）。

    python precompute/tests/verify_bnf2_exp2.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest_engine import (  # noqa: E402
    ExitRule, PriceBook, build_signals, run_backtest, summarize,
)

BNF2_DIR = r"D:\マイドキュメント\Claude\Projects\株式投資開発\BNF2検証"
CACHE_DIR = os.path.join(BNF2_DIR, "cache")
TICKERS_CSV = os.path.join(BNF2_DIR, "tickers_kawamura.csv")
EXPECTED_CSV = os.path.join(BNF2_DIR, "results", "exp2_threshold_era.csv")

INDEX_TICKER = "^N225"

# bnf2_verify.py の既定値（exp2 はこの設定で回っている）
EXIT = ExitRule(hold_days=10, stop_pct=-4.0, take_pct=8.0, cost_pct=0.2)
MAX_POS = 10

ERAS = [
    ("2005-2010", "2005-01-01", "2010-12-31"),
    ("2011-2017", "2011-01-01", "2017-12-31"),
    ("2018-2026", "2018-01-01", None),
]
THRESHOLDS = list(range(-8, -26, -2))


def load_cache(tickers):
    """BNF2検証の cache/*.csv を読む（bnf2_verify.fetch_prices のキャッシュ経路と同じ）。"""
    data = {}
    for t in tickers:
        fp = os.path.join(CACHE_DIR, t.replace("^", "_") + ".csv")
        if not os.path.exists(fp):
            continue
        df = pd.read_csv(fp, index_col=0, parse_dates=True)
        if len(df) > 50:
            data[t] = df
    return data


def to_panel(prices, tickers):
    """long 形式に直しつつ dev_25 を付ける（bnf2_verify.deviation と同じ定義）。"""
    frames = []
    for t in tickers:
        df = prices[t]
        ma25 = df["Close"].rolling(25).mean()
        frames.append(pd.DataFrame({
            "date": df.index,
            "ticker": t,
            "open": df["Open"].to_numpy(dtype="float64"),
            "high": df["High"].to_numpy(dtype="float64"),
            "low": df["Low"].to_numpy(dtype="float64"),
            "close": df["Close"].to_numpy(dtype="float64"),
            "dev_25": ((df["Close"] / ma25 - 1.0) * 100.0).to_numpy(dtype="float64"),
        }))
    return pd.concat(frames, ignore_index=True)


def main():
    tdf = pd.read_csv(TICKERS_CSV)
    tickers = list(tdf["ticker"])
    print("銘柄 %d / 期待値 %s" % (len(tickers), os.path.basename(EXPECTED_CSV)))

    prices = load_cache(tickers + [INDEX_TICKER])
    missing = [t for t in tickers if t not in prices]
    if missing:
        print("  ★キャッシュが無い銘柄: %s" % missing)
    tickers = [t for t in tickers if t in prices]
    cal = pd.DatetimeIndex(prices[INDEX_TICKER].index)
    print("  市場カレンダー: %s 〜 %s (%d 営業日)" % (cal[0].date(), cal[-1].date(), len(cal)))

    panel = to_panel(prices, tickers)
    book = PriceBook.from_panel(panel, tickers=tickers, calendar=cal)

    expected = pd.read_csv(EXPECTED_CSV)
    rows = []
    for era, s, e in ERAS:
        for th in THRESHOLDS:
            sig = build_signals(panel, book, [{"column": "dev_25", "op": "lte", "value": th}])
            res = run_backtest(book, sig, EXIT, max_positions=MAX_POS, start=s, end=e)
            era_cal = cal[cal >= pd.Timestamp(s)]
            if e:
                era_cal = era_cal[era_cal <= pd.Timestamp(e)]
            m = summarize(res, era_cal, capital_slots=MAX_POS)
            exp = expected[(expected.era == era) & (expected.th == th)].iloc[0]
            rows.append(dict(
                era=era, th=th,
                trades=m["trades"], exp_trades=int(exp.trades),
                win=m["win_rate"], exp_win=float(exp["win"]),
                pf=m["pf"], exp_pf=float(exp.pf),
                avg=m["avg_return"], exp_avg=float(exp.avg),
            ))

    out = pd.DataFrame(rows)
    for col in ("trades", "win", "pf", "avg"):
        base = out["exp_" + col].abs().replace(0, np.nan)
        out["d_" + col] = (out[col] - out["exp_" + col]).abs() / base * 100.0

    pd.set_option("display.width", 200)
    print()
    print(out[["era", "th", "trades", "exp_trades", "d_trades",
               "win", "exp_win", "d_win", "pf", "exp_pf", "d_pf"]]
          .to_string(index=False, float_format=lambda v: "%.2f" % v))

    worst = out[["d_trades", "d_win", "d_pf", "d_avg"]].max()
    print("\n最大ズレ(%%): 取引 %.2f / 勝率 %.2f / PF %.2f / 平均 %.2f"
          % (worst.d_trades, worst.d_win, worst.d_pf, worst.d_avg))
    ok = bool((worst[["d_trades", "d_win", "d_pf"]] <= 5.0).all())
    print("判定: %s (合格ライン ±5%%)" % ("合格" if ok else "不合格"))
    out.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "verify_bnf2_exp2_result.csv"), index=False)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
