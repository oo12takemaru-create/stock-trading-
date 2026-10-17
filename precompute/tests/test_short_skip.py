# -*- coding: utf-8 -*-
"""履歴が短い銘柄を、日次の新しい日だけ飛ばして進める（Fable 案C・2026-10-17）の試験。

    python precompute/tests/test_short_skip.py

本番には触れない。取得（pipeline.get_prices）と正本の読み出し（supabase_io._request）を差し替える。

確かめること
  1. 検証の材料を作る経路（short_ok=False・既定）は、今までどおり止まる
  2. 日次の経路（short_ok=True）は止まらず、短い銘柄だけ外し、理由（行数・期間）を返す
  3. 短い銘柄が多すぎる（提供元全体の不調）なら、日次の経路でも止まる
  4. 書く行は END より後だけ。飛ばしていた銘柄が戻ったら、正本の抜けを埋める
  5. 5営業日続けて書けていなければ知らせの本文が出る。4営業日なら出ない。要約には必ず出る
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

import config  # noqa: E402
import pipeline  # noqa: E402
import supabase_io  # noqa: E402
import update_metrics as um  # noqa: E402
from portfolio_run import END  # noqa: E402
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


# ── 1〜3. pipeline ─────────────────────────────────────────
DATES = pd.bdate_range("2015-05-01", "2017-01-31")


def ohlcv(seed, n=None):
    rng = np.random.default_rng(seed)
    idx = DATES if n is None else DATES[-n:]
    c = 1000 * np.cumprod(1 + rng.normal(0, 0.01, len(idx)))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Volume": rng.integers(1e5, 1e6, len(idx)).astype(float)}, index=idx)


def fake_prices(short):
    """short に入れた銘柄だけ、末尾59行しか返さない取得"""
    def get_prices(tickers, start, end, auto_adjust=True, kind="prices", refresh=False, log=print, **kw):
        out = {}
        for k, t in enumerate(tickers):
            out[t] = ohlcv(k + (100 if kind == "global" else 0), 59 if t in short else None)
        return out, []
    return get_prices


def run_pipe(tickers, short, **kw):
    saved, saved_fix = pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits
    pipeline.get_prices = fake_prices(short)
    # 分割の記録を yfinance に聞きに行かせない（外に出ない試験にする）
    pipeline.fetching.fix_unadjusted_splits = lambda df, t, log=print, splits=None: df
    try:
        return pipeline.run_pipeline(tickers=tickers, log=lambda m: None, **kw)
    finally:
        pipeline.get_prices, pipeline.fetching.fix_unadjusted_splits = saved, saved_fix


def test_pipeline():
    tickers = list(ACTIVE_TICKERS)[:12]
    short = {tickers[3]}

    print("― 1. 検証の材料を作る経路（既定）は止まる ―")
    try:
        run_pipe(tickers, short)
        check(False, "short_ok なしで止まる")
    except SystemExit as e:
        check("行未満" in str(e) and tickers[3] in str(e), "short_ok なしで止まる（理由に銘柄が出る）")

    print("\n― 2. 日次の経路は短い銘柄だけ外して進む ―")
    rep = []
    panel, market, _ = run_pipe(tickers, short, short_ok=True, skipped_report=rep)
    used = set(panel["ticker"].unique())
    check(tickers[3] not in used and len(used) == len(tickers) - 1, "短い銘柄だけ外れる",
          "%d 銘柄" % len(used))
    check(len(rep) == 1 and rep[0]["ticker"] == tickers[3] and rep[0]["rows"] == 59,
          "外した理由（行数）が返る", json.dumps(rep, ensure_ascii=False))
    check(rep and rep[0]["first"] == str(DATES[-59].date()) and rep[0]["last"] == str(DATES[-1].date()),
          "外した理由（期間）が返る")
    check(len(market) > 0, "相場環境も計算できる")

    print("\n― 3. 短い銘柄が多すぎれば日次でも止まる ―")
    many = set(tickers[:pipeline.MAX_SHORT_SKIP + 1])
    try:
        run_pipe(tickers, many, short_ok=True, skipped_report=[])
        check(False, "上限（%d）を超えたら止まる" % pipeline.MAX_SHORT_SKIP)
    except SystemExit:
        check(True, "上限（%d）を超えたら止まる" % pipeline.MAX_SHORT_SKIP)


# ── 4〜5. update_metrics ───────────────────────────────────
def fake_store(last_dates):
    """正本の『最後の行』を返す偽物。before（date=lt.）が付けばそれより前で最後の日"""
    supabase_io.credentials = lambda: ("http://x", "k")

    def req(method, url, key, body=None, extra_headers=None):
        t = url.split("ticker=eq.")[1].split("&")[0]
        ds = sorted(d for d in last_dates.get(t, []))
        if "date=lt." in url:
            lt = pd.Timestamp(url.split("date=lt.")[1].split("&")[0])
            ds = [d for d in ds if d < lt]
        rows = [{"date": ds[-1].date().isoformat(), "close": 1000.0}] if ds else []
        return 200, json.dumps(rows).encode("utf-8")
    supabase_io._request = req


def test_rows_to_write():
    print("\n― 4. 書く行は END より後だけ・戻ったら抜けを埋める ―")
    end = pd.Timestamp(END)
    days = pd.bdate_range(end - pd.Timedelta(days=20), end + pd.Timedelta(days=30))
    panel = pd.DataFrame([{"ticker": t, "date": d, "close": 1000.0}
                          for t in ("A", "B", "C") for d in days])
    recent = list(days[-5:])
    stored = {
        "A": {"date": str(days[-6].date())},                  # 抜けなし
        "B": {"date": str(days[-9].date())},                  # 3営業日の抜け（END より後）
        "C": {"date": str((end - pd.Timedelta(days=10)).date())},  # 最後の日が END より前
    }
    out = um.rows_to_write(panel, recent, stored)
    by = out.groupby("ticker")["date"].agg(["min", "count"])
    check(by.loc["A", "count"] == 5, "抜けの無い銘柄は直近5日だけ", str(by.loc["A", "count"]))
    check(by.loc["B", "count"] == 8 and by.loc["B", "min"] == days[-8],
          "戻った銘柄は抜け（3日）ごと埋める", str(by.loc["B", "count"]))
    check((out["date"] > end).all(), "★END 以前の行は1つも書かない★",
          "最小 %s" % out["date"].min().date())
    check(by.loc["C", "min"] > end, "正本の最後の日が END より前でも、埋めるのは END の後から")


def test_report():
    print("\n― 5. 書けない日が続いたら知らせる ―")
    all_dates = list(pd.bdate_range("2026-10-01", "2026-10-23"))
    tmp = tempfile.mkdtemp()
    alert = os.path.join(tmp, "skip_alert.txt")
    summ = os.path.join(tmp, "summary.md")
    os.environ["SKIP_ALERT_FILE"] = alert
    os.environ["GITHUB_STEP_SUMMARY"] = summ
    try:
        # 正本の最後の日から4営業日書けていない → 知らせない
        fake_store({"9508.T": [all_dates[-5]]})
        rep = [{"ticker": "9508.T", "rows": 59, "first": "2026-07-17", "last": "2026-10-23"}]
        a = um.report_skipped(rep, all_dates, log=lambda m: None)
        check(not a and not os.path.exists(alert), "4営業日なら知らせない", "missed=%s" % rep[0].get("missed_days"))
        check(os.path.exists(summ) and "9508.T" in open(summ, encoding="utf-8").read(),
              "要約には必ず出る")
        # 5営業日
        fake_store({"9508.T": [all_dates[-6]]})
        rep = [{"ticker": "9508.T", "rows": 59, "first": "2026-07-17", "last": "2026-10-23"}]
        a = um.report_skipped(rep, all_dates, log=lambda m: None)
        body = open(alert, encoding="utf-8").read() if os.path.exists(alert) else ""
        check(a and "9508.T" in body and "59 行" in body, "5営業日なら知らせの本文が出る",
              "missed=%s" % rep[0].get("missed_days"))
        check(body.splitlines()[0].startswith("[ルールトレード]") if body else False,
              "1行目が件名の形")
        # 飛ばした銘柄が無ければ何もしない
        os.remove(alert)
        check(um.report_skipped([], all_dates, log=lambda m: None) == [] and not os.path.exists(alert),
              "飛ばした銘柄が無ければ何も出さない")
    finally:
        os.environ.pop("SKIP_ALERT_FILE", None)
        os.environ.pop("GITHUB_STEP_SUMMARY", None)


def main():
    test_pipeline()
    test_rows_to_write()
    test_report()
    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
