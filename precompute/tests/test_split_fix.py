# -*- coding: utf-8 -*-
"""分割の取りこぼしを直す処理（fetching.fix_unadjusted_splits）の試験。

    python precompute/tests/test_split_fix.py

2026-10-11 に 8227.T（しまむら）で見つけた形を再現する:
  分割 1:3 の記録は 2026-02-19、調整済みの終値は 02-17 10,806円 → 02-18 3,634円。
記録の無い急変は直さないこと（本物の暴落かもしれない）も確かめる。
"""
from __future__ import annotations

import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import fetching  # noqa: E402

PASS = FAIL = 0
IDX = pd.to_datetime(["2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20"])


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def mk(closes, vol=1000):
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                         "Close": closes, "Volume": [vol] * len(closes)}, index=IDX)


def quiet(_):
    pass


def main():
    print("― 1. しまむらと同じ形（1:3・記録は値が動いた翌日）―")
    d = fetching.fix_unadjusted_splits(
        mk([10797.0, 10806.8, 3634.9, 3585.1, 3495.2]), "8227.T", quiet,
        splits={pd.Timestamp("2026-02-19"): 3.0})
    r = d["Close"].iloc[2] / d["Close"].iloc[1]
    check(abs(r - 1) < 0.02, "★偽の −66% が消えた★", "2/17→2/18 の比 %.3f" % r)
    check(abs(d["Volume"].iloc[0] - 3000) < 1e-6, "分割より前の出来高は分割比倍に直る")
    check(d["Close"].iloc[3] == 3585.1, "分割より後の値は触らない")

    print("\n― 2. ★対照★ 記録の無い急変は直さない（本物の暴落かもしれない）―")
    d = fetching.fix_unadjusted_splits(
        mk([1000.0, 1000.0, 330.0, 320.0, 310.0]), "XXXX.T", quiet, splits={})
    check(d["Close"].iloc[1] == 1000.0, "★記録が無ければ触らない★")

    print("\n― 3. ★対照★ 記録はあるが倍率が合わない（1:3 の記録なのに 0.5倍）―")
    d = fetching.fix_unadjusted_splits(
        mk([1000.0, 1000.0, 500.0, 500.0, 500.0]), "YYYY.T", quiet,
        splits={pd.Timestamp("2026-02-19"): 3.0})
    check(d["Close"].iloc[1] == 1000.0, "倍率が合わなければ触らない")

    print("\n― 4. ★対照★ 記録が遠い（10日以上離れている）なら触らない ―")
    d = fetching.fix_unadjusted_splits(
        mk([1000.0, 1000.0, 333.0, 333.0, 333.0]), "WWWW.T", quiet,
        splits={pd.Timestamp("2026-03-10"): 3.0})
    check(d["Close"].iloc[1] == 1000.0, "記録の日と離れていれば触らない")

    print("\n― 5. ★対照★ 正しく調整済み（急変なし）なら何もしない ―")
    src = mk([1000.0, 1005.0, 998.0, 1002.0, 1001.0])
    d = fetching.fix_unadjusted_splits(src, "ZZZZ.T", quiet,
                                       splits={pd.Timestamp("2026-02-19"): 3.0})
    check(d.equals(src), "急変が無ければ触らない")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
