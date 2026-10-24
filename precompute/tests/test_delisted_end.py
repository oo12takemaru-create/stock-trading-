# -*- coding: utf-8 -*-
"""上場廃止した銘柄は最終売買日で履歴を終える（2026-10-24・Fable 判断）の試験。

    python precompute/tests/test_delisted_end.py

本番には触れない（取得は差し替え、正本は読まない）。数字は作り物。

確かめること
  1. 最終売買日は docs/delisted.json の廃止日の前の東証の営業日。JPX の一覧の行だけを使う
  2. 前計算（日次の経路）は、表にある銘柄の最終売買日より後を取りに行かない。
     短くても止まらず、短い履歴の知らせにも MAX_SHORT_SKIP の数にも入らない
  3. 前計算（検証の材料の経路）は、今までどおり短ければ止まる。長ければ最終売買日で切る
  4. バックテストは、最終売買日に残った建玉をその日の終値で「期末強制決済」にする
     （黙って消さない・最終売買日より後の行で約定も決済もしない）
  5. ★対照★ 最終売買日を渡さなければ、同じ材料で答えが変わる（＝4 は意味のある比較）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import delisting  # noqa: E402
import pipeline  # noqa: E402
import portfolio_engine as pe  # noqa: E402
import portfolio_run  # noqa: E402
from universe import ACTIVE_TICKERS  # noqa: E402

PASS = FAIL = 0


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


# ── 1. 最終売買日の表 ─────────────────────────────────────
def test_table():
    print("― 1. 最終売買日の表 ―")
    jpx = delisting.JPX_SOURCE
    doc = {"updated": "x", "items": {
        "1001": {"code": "1001", "d": "2026-09-29", "source": jpx, "status": "delisted"},   # 火 → 月
        "1002": {"code": "1002", "d": "2026-10-13", "source": jpx, "status": "delisted"},   # 祝日明け → 前の金
        "1003": {"code": "1003", "d": "2026-10-20", "source": jpx, "status": "scheduled"},  # 予定のままでも日付で決める
        "1004": {"code": "1004", "d": None, "source": "上場一覧（2026-10-01版）から消えた",
                 "status": "delisted"},                                                     # 使わない
        "1005": {"code": "1005", "d": "2026/10/01", "source": jpx, "status": "delisted"},   # 形が違う日付は使わない
    }}
    tmp = os.path.join(tempfile.mkdtemp(), "delisted.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    got = {t: str(d.date()) for t, d in delisting.last_trading_days(path=tmp).items()}
    check(got.get("1001.T") == "2026-09-28", "廃止日の前の営業日（火→月）", str(got.get("1001.T")))
    check(got.get("1002.T") == "2026-10-09", "祝日（スポーツの日）をまたぐ", str(got.get("1002.T")))
    check(got.get("1003.T") == "2026-10-19", "status が scheduled でも日付で決める", str(got.get("1003.T")))
    check("1004.T" not in got and "1005.T" not in got, "JPX の一覧以外の行・形の違う日付は使わない")
    only = delisting.last_trading_days(["1001.T", "9999.T"], path=tmp)
    check(list(only) == ["1001.T"], "渡した銘柄のうち表にあるものだけ返す")
    real = {t: str(d.date()) for t, d in delisting.last_trading_days(["7240.T", "9508.T"]).items()}
    check(real == {"7240.T": "2026-09-28", "9508.T": "2026-09-28"},
          "本物の docs/delisted.json: 7240.T・9508.T の最終売買日は 9/28", json.dumps(real))


# ── 2〜3. 前計算 ─────────────────────────────────────────
DATES = pd.bdate_range("2015-05-01", "2017-01-31")


def ohlcv(seed, idx):
    rng = np.random.default_rng(seed)
    c = 1000 * np.cumprod(1 + rng.normal(0, 0.01, len(idx)))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Volume": rng.integers(1e5, 1e6, len(idx)).astype(float)}, index=idx)


def fake_prices(shape, calls):
    """shape: {ticker: "short" | "long"}。どちらも DATES の最後まで（最終売買日より後も）返す。
    呼ばれた (銘柄, end) を calls に残す"""
    def get_prices(tickers, start, end, auto_adjust=True, kind="prices", refresh=False, log=print, **kw):
        out = {}
        for k, t in enumerate(tickers):
            calls.append((t, pd.Timestamp(end), kind))
            idx = DATES[-59:] if shape.get(t) == "short" else DATES
            out[t] = ohlcv(k + (100 if kind == "global" else 0), idx)
        return out, []
    return get_prices


def run_pipe(tickers, shape, ends, calls, **kw):
    saved = (pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits,
             pipeline.delisting.last_trading_days)
    pipeline.get_prices = fake_prices(shape, calls)
    pipeline.fetching.fix_unadjusted_splits = lambda df, t, log=print, splits=None: df
    pipeline.delisting.last_trading_days = lambda tk=None, path=None: {
        t: d for t, d in ends.items() if tk is None or t in tk}
    try:
        return pipeline.run_pipeline(tickers=tickers, log=lambda m: None, **kw)
    finally:
        (pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits,
         pipeline.delisting.last_trading_days) = saved


def test_pipeline():
    tickers = list(ACTIVE_TICKERS)[:14]
    last = DATES[-30]
    n_end = pipeline.MAX_SHORT_SKIP + 1                 # 上限を超える数の廃止銘柄
    short_ended = tickers[:n_end]
    long_ended = tickers[n_end]
    ends = {t: last for t in short_ended + [long_ended]}
    shape = {t: "short" for t in short_ended}

    print("\n― 2. 日次の経路：廃止銘柄は短くても止まらず、数に入らない ―")
    calls, skipped, ended = [], [], []
    panel, market, _ = run_pipe(tickers, shape, ends, calls, short_ok=True,
                                skipped_report=skipped, ended_report=ended)
    used = set(panel["ticker"].unique())
    check(not skipped, "短い履歴の知らせ（skipped_report）に入らない", json.dumps(skipped))
    check(not (set(short_ended) & used), "%d 銘柄（上限 %d を超える）とも外れて止まらない"
          % (n_end, pipeline.MAX_SHORT_SKIP))
    check(len(used) == len(tickers) - n_end, "ほかの銘柄は計算に入る", "%d 銘柄" % len(used))
    rep = {e["ticker"]: e for e in ended}
    check(set(rep) == set(ends) and all(not rep[t]["used"] for t in short_ended)
          and rep[long_ended]["used"] and rep[long_ended]["last_trade"] == str(last.date()),
          "外した・使った廃止銘柄が ended_report に最終売買日つきで返る", json.dumps(ended))
    stock_calls = [(t, e) for t, e, k in calls if k == "prices"]
    over = [(t, str(e.date())) for t, e in stock_calls if t in ends and e > last + pd.Timedelta(days=1)]
    check(not over and any(t in ends for t, _ in stock_calls),
          "★廃止銘柄は最終売買日より後を取りに行かない（取得の end ＝最終売買日の翌日）★",
          json.dumps(over))
    live_end = {e for t, e in stock_calls if t not in ends}
    check(live_end and min(live_end) > DATES[-1], "ほかの銘柄の取得の窓は今までどおり")
    lmax = panel.loc[panel["ticker"] == long_ended, "date"].max()
    check(lmax == last, "長い履歴の廃止銘柄は最終売買日までで切る（後ろの行は入らない）", str(lmax.date()))
    others_max = panel.loc[panel["ticker"] != long_ended, "date"].max()
    check(others_max == DATES[-1], "ほかの銘柄は最後の日まである")
    check(len(market) > 0, "相場環境も計算できる")

    print("\n― 3. 検証の材料の経路：短ければ今までどおり止まる ―")
    try:
        run_pipe(tickers, shape, ends, [])
        check(False, "short_ok なしで止まる")
    except SystemExit as e:
        check("行未満" in str(e) and short_ended[0] in str(e), "short_ok なしで止まる（理由に銘柄が出る）")
    panel2, _, _ = run_pipe(tickers, {}, ends, [])
    check(panel2.loc[panel2["ticker"].isin(list(ends)), "date"].max() == last
          and panel2["ticker"].nunique() == len(tickers),
          "長い履歴なら母集団はそのまま・最終売買日で切る")

    import update_metrics as um
    summ = os.path.join(tempfile.mkdtemp(), "summary.md")
    os.environ["GITHUB_STEP_SUMMARY"] = summ
    try:
        um.report_ended(ended, log=lambda m: None)
        body = open(summ, encoding="utf-8").read() if os.path.exists(summ) else ""
    finally:
        os.environ.pop("GITHUB_STEP_SUMMARY", None)
    check("上場廃止で履歴を終えた銘柄" in body and short_ended[0] in body and str(last.date()) in body,
          "ジョブの要約に廃止銘柄と最終売買日が出る（短い履歴の知らせとは別の枠）")

    print("\n― 2'. 廃止銘柄が無ければ今までどおり（短い銘柄は短い履歴として数える）―")
    skipped = []
    run_pipe(tickers, {tickers[3]: "short"}, {}, [], short_ok=True, skipped_report=skipped)
    check(len(skipped) == 1 and skipped[0]["ticker"] == tickers[3], "表に無い短い銘柄は今までどおり知らせる")


# ── 4〜5. バックテスト ───────────────────────────────────
START, END = "2026-06-01", "2026-12-30"
LAST = pd.Timestamp("2026-09-28")


def material(tickers):
    """LAST より後も値が続く材料（埋めた行・後継の値に相当）。LAST の後は大きく動かす"""
    dates = pd.bdate_range(START, END)
    rng = np.random.default_rng(3)
    rows = []
    for k, t in enumerate(tickers):
        close = 1000.0 + 50 * k
        for d in dates:
            close *= 1.002 if d <= LAST else float(rng.choice([0.90, 1.12]))
            rows.append({"ticker": t, "date": d, "close": close, "low": close * 0.995,
                         "dev_25": -1.0, "dev_50": -1.0, "dev_75": -1.0, "dev_200": -1.0,
                         "vol_ratio_20": 1.0, "bb_pos_1_5": 0.0, "high_20_ratio": 0.9,
                         "high_52w_ratio": 0.9, "ret_5d": 0.0, "day_change": 0.0,
                         "knife_guard": False, "minervini_entry": False, "ema_50_pos": 101.0})
    panel = pd.DataFrame(rows)
    market = pd.DataFrame({"date": dates, "regime": "BULLISH", "sp500_change_1d": 0.0,
                           "sp500_change_3d": 0.0, "is_halt": False})
    return panel, market


SIGNAL_DAYS = {pd.Timestamp(d) for d in ("2026-08-20", "2026-09-24", "2026-10-05")}
RULES = [pe.PresetRule(rule_id="long-hold", name="試験・長期", regimes=("BULLISH",),
                       signal=lambda cols, i, sector, regime, mkt, date: date in SIGNAL_DAYS,
                       exit=pe.PresetExit(stop_pct=-50.0, calendar_days=90,
                                          time_exit_label="タイムストップ"))]


def run(panel, market, ends):
    saved = portfolio_run.PRESET_RULES, portfolio_run.delisting.last_trading_days
    portfolio_run.PRESET_RULES = RULES
    portfolio_run.delisting.last_trading_days = lambda tk=None, path=None: {
        t: d for t, d in ends.items() if tk is None or t in tk}
    try:
        return portfolio_run.run(panel, market, start=START, end=END, log=lambda m: None)
    finally:
        portfolio_run.PRESET_RULES, portfolio_run.delisting.last_trading_days = saved


def pick(n=3):
    import config
    from universe import JAPAN_STOCKS
    risk = config.SECTOR_RISK_MULTIPLIER
    out = []
    for t, (_, sec) in JAPAN_STOCKS.items():
        if risk.get(sec, risk["default"]) > 0 and sec not in {JAPAN_STOCKS[x][1] for x in out}:
            out.append(t)
        if len(out) == n:
            break
    return out


def test_backtest():
    print("\n― 4. バックテスト：最終売買日に残った建玉は、その日の終値で期末強制決済 ―")
    tk = pick()
    dead = tk[0]
    panel, market = material(tk)
    r, trades = run(panel, market, {dead: LAST})
    mine = [t for t in trades if t.ticker == dead]
    closes = panel.set_index(["ticker", "date"])["close"]
    forced = [t for t in mine if t.reason == "期末強制決済"]
    check(len(forced) == 1 and forced[0].exit_date == LAST
          and abs(forced[0].exit_price - closes[(dead, LAST)]) < 1e-9,
          "★最終売買日の日・終値で「期末強制決済」（黙って消さない）★",
          "; ".join("%s→%s %s" % (t.entry_date.date(), t.exit_date.date(), t.reason) for t in mine))
    check(all(t.entry_date <= LAST and t.exit_date <= LAST for t in mine),
          "最終売買日より後の行で約定も決済もしない")
    check(r["exit_reasons"] and sum(v for v in r["exit_reasons"].values()) == len(trades),
          "決済理由の内訳に入る", json.dumps(r["exit_reasons"], ensure_ascii=False))
    alive = [t for t in trades if t.ticker != dead]
    check(any(t.exit_date > LAST for t in alive), "表に無い銘柄は最終売買日の後も普通に回る")

    # 直接 simulate_ticker でも同じ（last_day が end より後なら何も変わらない）
    sub = panel[panel["ticker"] == dead].sort_values("date")
    cols = {c: sub[c].to_numpy().astype("float64") for c in ("close", "low")}
    dates = pd.DatetimeIndex(sub["date"].to_numpy())
    mkt, _, _ = portfolio_run.build_market(market)
    a = pe.simulate_ticker(dead, "", cols, dates, RULES, mkt, {"default": 1.0},
                           start=START, end="2026-09-04")
    b = pe.simulate_ticker(dead, "", cols, dates, RULES, mkt, {"default": 1.0},
                           start=START, end="2026-09-04", last_day=LAST)
    key = lambda ts: [(t.entry_date, t.exit_date, t.exit_price, t.reason) for t in ts]
    check(key(a) == key(b) and len(a) > 0,
          "★期間の末が最終売買日より前なら、最終売買日を渡しても1件も変わらない（公開の10年と同じ形）★")

    print("\n― 5. ★対照★ 最終売買日を渡さなければ答えが変わる ―")
    r0, t0 = run(panel, market, {})
    m0 = [t for t in t0 if t.ticker == dead]
    check(key(m0) != key(mine) and any(t.exit_date > LAST for t in m0),
          "★表が無ければ最終売買日の後の値で決済される（＝4 は意味のある比較）★")


def main():
    test_table()
    test_pipeline()
    test_backtest()
    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
