# -*- coding: utf-8 -*-
"""東証の休場日の行を計算に使わないこと（2026-10-17・Fable）の試験。

    python precompute/tests/test_holiday_rows.py

本番には触れない（正本も yfinance も使わない）。

確かめること
  1. 営業日の表（trading_calendar・exchange_calendars の XTKS）が、分かっている日を正しく言う
     （2018-12-24 振替休日・2020-10-01 システム障害の終日停止は休場、2018-12-25 は営業日）
  2. 表の範囲より後の日は落とさない（新しい日が丸ごと消えないこと）
  3. ★休場日の行があっても無くても、ポートフォリオの結果が完全に同じ★
     （正本に入っている形＝前の営業日の終値・出来高0 の行を差し込んで、読み込み直後に落とす）
  4. ★対照★ 落とさなければ結果が変わる（＝3 は意味のある比較）
  5. 営業日なのに無い日は、落とさず・埋めずに知らせる
  6. 日次の経路（pipeline）でも同じ表で落とす
  7. 日次の経路を丸ごと通しても、作った材料に休場日の行が残らない
"""
from __future__ import annotations

import hashlib
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import panel_from_db  # noqa: E402
import pipeline  # noqa: E402
import portfolio_engine as pe  # noqa: E402
import portfolio_run  # noqa: E402
import trading_calendar as tc  # noqa: E402
from universe import JAPAN_STOCKS  # noqa: E402

PASS = FAIL = 0
START, END = "2018-11-01", "2019-01-31"
HOLIDAYS = [pd.Timestamp(d) for d in ("2018-11-23", "2018-12-24", "2018-12-31", "2019-01-01")]


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def fingerprint(trades):
    h = hashlib.sha256()
    for t in trades:
        h.update(("%s|%s|%s|%.6f|%.6f|%d|%s\n" % (
            t.ticker, t.entry_date, t.exit_date,
            t.entry_price, t.exit_price, t.shares, t.reason)).encode())
    return h.hexdigest()[:16]


def pick_tickers(n=3):
    risk = config.SECTOR_RISK_MULTIPLIER
    out = []
    for t, (_, sec) in JAPAN_STOCKS.items():
        if risk.get(sec, risk["default"]) > 0 and sec not in {JAPAN_STOCKS[x][1] for x in out}:
            out.append(t)
        if len(out) == n:
            break
    return out


def material(with_holidays: bool):
    """営業日だけの材料。with_holidays なら、正本と同じ形の休場日の行を差し込む。"""
    days = tc.sessions(START, END)
    rows, mrows = [], []
    for k, t in enumerate(pick_tickers()):
        close = 1000.0 + 100 * k
        prev = None
        for i, d in enumerate(days):
            close *= (1.004 if (i + k) % 3 else 0.985)
            r = {"ticker": t, "date": d, "close": close, "low": close * 0.99,
                 "dev_25": -1.0, "dev_50": -1.0, "dev_75": -1.0, "dev_200": -1.0,
                 "vol_ratio_20": 1.0, "bb_pos_1_5": 0.0, "high_20_ratio": 0.9,
                 "high_52w_ratio": 0.9, "ret_5d": 0.0, "day_change": 0.0,
                 "knife_guard": False, "minervini_entry": False, "ema_50_pos": 101.0}
            if with_holidays and prev is not None:
                for h in HOLIDAYS:
                    if prev["date"] < h < d:
                        # 正本の休場日の行の形: 四本値＝前の営業日の終値・出来高0
                        rows.append({**prev, "date": h, "low": prev["close"], "vol_ratio_20": 0.0})
            rows.append(r)
            prev = r
    for d in sorted({r["date"] for r in rows}):
        mrows.append({"date": d, "regime": "BULLISH", "sp500_change_1d": 0.0,
                      "sp500_change_3d": 0.0, "is_halt": False})
    return pd.DataFrame(rows), pd.DataFrame(mrows)


# 休場日の前の営業日にシグナル → 「翌営業日」が休場日の行になるかどうかで約定値が変わる
SIGNAL_DAYS = {pd.Timestamp(d) for d in ("2018-11-22", "2018-12-21", "2018-12-28", "2019-01-10")}
RULES = [pe.PresetRule(rule_id="test", name="試験", regimes=("BULLISH",),
                       signal=lambda cols, i, sector, regime, mkt, date: date in SIGNAL_DAYS,
                       exit=pe.PresetExit(stop_pct=-5.0, calendar_days=10))]


def run(panel, market):
    saved = portfolio_run.PRESET_RULES
    portfolio_run.PRESET_RULES = RULES
    try:
        return portfolio_run.run(panel, market, start=START, end=END, log=lambda m: None)
    finally:
        portfolio_run.PRESET_RULES = saved


def main():
    print("― 1. 営業日の表 ―")
    s = tc.sessions("2018-12-20", "2019-01-08")
    check(pd.Timestamp("2018-12-24") not in s, "2018-12-24（振替休日）は休場")
    check(pd.Timestamp("2018-12-25") in s, "2018-12-25 は営業日")
    check(pd.Timestamp("2019-01-04") in s and pd.Timestamp("2019-01-03") not in s,
          "年始は 1/4 から（1/1〜1/3 は休場）")
    check(pd.Timestamp("2020-10-01") in tc.non_sessions(["2020-09-30", "2020-10-01", "2020-10-02"]),
          "2020-10-01（システム障害の終日停止）は休場")

    print("\n― 2. 表の範囲より後は落とさない ―")
    far = tc.table_last_day() + pd.Timedelta(days=40)
    check(len(tc.non_sessions([far])) == 0, "範囲外の日は営業日でないと言わない", str(far.date()))

    print("\n― 3. 休場日の行があっても無くても同じ ―")
    p0, m0 = material(False)
    p1, m1 = material(True)
    n_hol = int(p1["date"].isin(HOLIDAYS).sum())
    check(n_hol > 0, "差し込んだ休場日の行がある", "%d 行" % n_hol)
    logs = []
    p1d = panel_from_db.drop_holiday_rows(p1, "daily_metrics", logs.append)
    m1d = panel_from_db.drop_holiday_rows(m1, "market_condition", logs.append)
    check(any("休場日の行 %d 日分" % len(HOLIDAYS) in s for s in logs), "落とした日数をログに出す",
          logs[0] if logs else "")
    r0, t0 = run(p0, m0)
    r1, t1 = run(p1d, m1d)
    check(len(t0) > 0, "トレードが1件以上ある", str(len(t0)))
    same = r0["summary"] == r1["summary"] and fingerprint(t0) == fingerprint(t1) \
        and r0["equity_curve"] == r1["equity_curve"]
    check(same, "★summary・採用トレードの並び・カーブが完全に一致★")
    check(not any(t.entry_date in HOLIDAYS or t.exit_date in HOLIDAYS for t in t1),
          "休場日に約定・決済したトレードが無い")

    print("\n― 4. ★対照★ 落とさなければ結果が変わる ―")
    r2, t2 = run(p1, m1)
    on_hol = [t for t in t2 if t.entry_date in HOLIDAYS or t.exit_date in HOLIDAYS]
    check(len(on_hol) > 0 and fingerprint(t2) != fingerprint(t0),
          "★落とさないと休場日の行で約定・決済する★", "%d 件" % len(on_hol))

    print("\n― 5. 営業日なのに無い日は知らせるだけ ―")
    gap = pd.Timestamp("2018-12-12")
    p3 = p0[p0["date"] != gap]
    logs = []
    p3d = panel_from_db.drop_holiday_rows(p3, "daily_metrics", logs.append)
    check(len(p3d) == len(p3), "落とさない・埋めない")
    check(any("行が無い日が 1 日" in s and "2018-12-12" in s for s in logs), "欠けた日をログに出す",
          " / ".join(logs))

    print("\n― 6. 日次の経路（pipeline）でも同じ表で落とす ―")
    idx = pd.DatetimeIndex(["2018-12-21", "2018-12-24", "2018-12-25"])
    frames = {"X": pd.DataFrame({"Close": [1.0, 1.0, 2.0]}, index=idx), "Y": None}
    logs = []
    pipeline._drop_holidays(frames, logs.append, "個別株")
    check(list(frames["X"].index) == [idx[0], idx[2]], "2018-12-24 の行が落ちる")
    check(logs and "2018-12-24" in logs[0], "落とした日をログに出す", logs[0] if logs else "")

    print("\n― 7. 日次の経路を丸ごと通しても、休場日の行が材料に残らない ―")
    import numpy as np
    bdays = pd.bdate_range("2015-05-01", "2017-01-31")   # 平日すべて＝休場日も入っている

    def fake_get_prices(tks, start, end, auto_adjust=True, kind="prices", refresh=False, log=print, **kw):
        out = {}
        for k, t in enumerate(tks):
            c = 1000 * np.cumprod(1 + np.random.default_rng(k).normal(0, 0.01, len(bdays)))
            out[t] = pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                                   "Volume": np.full(len(bdays), 1e5)}, index=bdays)
        return out, []

    saved, saved_fix = pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits
    pipeline.get_prices = fake_get_prices
    pipeline.fetching.fix_unadjusted_splits = lambda df, t, log=print, splits=None: df
    logs = []
    try:
        panel, market, _ = pipeline.run_pipeline(tickers=pick_tickers(), log=logs.append)
    finally:
        pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits = saved, saved_fix
    bad_p = tc.non_sessions(panel["date"])
    bad_m = tc.non_sessions(market["date"])
    check(len(bad_p) == 0 and len(bad_m) == 0, "daily_metrics・market_condition に休場日の行が無い",
          "%d / %d" % (len(bad_p), len(bad_m)))
    check(any("個別株: 東証の休場日の行を落としました" in s for s in logs), "落としたことをログに出す")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
