# -*- coding: utf-8 -*-
"""公開の今日のシグナル（daily_scanner_v2_8_0.scan）が、上場廃止した銘柄を対象から外し、
「取得失敗」に数えないことの試験（2026-10-24・Fable 判断）。数字は作り物（外に取りに行かない）。

    python -X utf8 tests/test_delisted_scan.py

  1. 表にある銘柄（最終売買日が今日より前）は取りに行かず、ended に名前と最終売買日が入る
  2. 「取得失敗」は本当に取れなかった銘柄だけを数える
  3. レポートに「上場廃止で対象外」の行が出る。表を読めなかったときは理由が出る（スキャンは止めない）
  4. 最終売買日が今日以降（まだ売買できる）なら今までどおり取りに行く
  5. 本物の docs/delisted.json から 7240.T・9508.T の最終売買日 9/28 が引ける
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_scanner_v2_8_0 as SCAN  # noqa: E402

NG = 0
N = 0


def ok(cond, label, extra=""):
    global NG, N
    N += 1
    print(("  OK " if cond else "  NG ") + label + (("  " + extra) if extra else ""))
    if not cond:
        NG += 1


def frame(seed, n=320):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    n = len(idx)                     # 今日が休日だと1本少なく返る版がある
    c = 1000 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({"Open": c, "High": c * 1.01, "Low": c * 0.99, "Close": c,
                         "Adj Close": c, "Volume": rng.integers(1e5, 1e6, n).astype(float)}, index=idx)


def run_scan(last_days, error=None):
    stocks = {"1111.T": ("取れる", "自動車"), "2222.T": ("取れない", "自動車"),
              "3333.T": ("廃止済み", "電力"), "4444.T": ("明日廃止", "電力")}
    calls = []

    def fetch_stock(ticker, days_back=400):
        calls.append(ticker)
        return frame(1) if ticker in ("1111.T", "4444.T") else None

    saved = (SCAN.STOCKS, SCAN.fetch_global_data, SCAN.fetch_stock_data, SCAN.load_delisted_last_days)
    SCAN.STOCKS = stocks
    SCAN.fetch_global_data = lambda days_back=400: {t: frame(7 + k) for k, t in enumerate(SCAN.GLOBAL_TICKERS)}
    SCAN.fetch_stock_data = fetch_stock
    SCAN.load_delisted_last_days = lambda: (last_days, error)
    try:
        return SCAN.scan(), calls
    finally:
        (SCAN.STOCKS, SCAN.fetch_global_data, SCAN.fetch_stock_data, SCAN.load_delisted_last_days) = saved


def main():
    today = date.today()
    print("― 1〜4. 表にある銘柄は取りに行かず、取得失敗に数えない ―")
    res, calls = run_scan({"3333.T": date(2026, 9, 28), "4444.T": today})
    ok("3333.T" not in calls, "廃止済みの銘柄は取りに行かない", str(calls))
    ok([e["ticker"] for e in res["ended"]] == ["3333.T"]
       and res["ended"][0]["last_trade"] == "2026-09-28", "ended に名前と最終売買日が入る", str(res["ended"]))
    ok(res["failed"] == 1, "★取得失敗は本当に取れなかった1銘柄だけ★", "failed=%s" % res["failed"])
    ok(res["scanned"] == 2 and "4444.T" in calls, "最終売買日が今日なら今までどおり取りに行く",
       "scanned=%s" % res["scanned"])
    md = SCAN.generate_markdown_report(res)
    ok("取得失敗: 1 銘柄" in md and "上場廃止で対象外: 1 銘柄" in md and "最終売買日2026-09-28" in md,
       "レポートに「上場廃止で対象外」が出る")

    print("\n― 3'. 表を読めなくても止めない（理由を出す） ―")
    res2, calls2 = run_scan({}, error="FileNotFoundError: docs/delisted.json")
    ok("3333.T" in calls2 and res2["failed"] == 2, "表が無ければ今までどおり（取得失敗に入る）",
       "failed=%s" % res2["failed"])
    ok("上場廃止の表（docs/delisted.json）を読めませんでした" in SCAN.generate_markdown_report(res2),
       "読めなかった理由がレポートに出る")

    print("\n― 5. 本物の表 ―")
    real, err = SCAN.load_delisted_last_days()
    ok(err is None and real.get("7240.T") == date(2026, 9, 28) and real.get("9508.T") == date(2026, 9, 28),
       "7240.T・9508.T の最終売買日は 2026-09-28", "err=%s" % err)
    ok(all(t in SCAN.STOCKS for t in real), "スキャナの銘柄だけが返る")
    ok(all(d < today + timedelta(days=400) for d in real.values()), "日付の形が date")

    print("\n%d 件中 失敗 %d" % (N, NG))
    return 1 if NG else 0


if __name__ == "__main__":
    sys.exit(main())
