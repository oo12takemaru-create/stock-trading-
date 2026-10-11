# -*- coding: utf-8 -*-
"""日次で正本に足す前の検査（update_metrics.check_incoming）の試験。

    python precompute/tests/test_incoming_check.py

本番には触れない。正本の読み出し（supabase_io._request）を差し替えて試す。
2026-10-02 と同じ壊れ方（調整係数が跳ねて、終値が 1/2000 になる）を止めること、
配当くらいのずれ（数%）は止めないこと、を確かめる。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import supabase_io  # noqa: E402
import update_metrics as um  # noqa: E402

PASS = FAIL = 0


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


DAYS = pd.to_datetime(["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"])
LAST_STORED = pd.Timestamp("2026-10-02")


def make_panel(closes):
    """{銘柄: [保存済みの最後の日の値, 足す5日の値…]} からパネルを作る"""
    rows = []
    for t, cs in closes.items():
        rows.append({"ticker": t, "date": LAST_STORED, "close": cs[0]})
        for d, c in zip(DAYS, cs[1:]):
            rows.append({"ticker": t, "date": d, "close": c})
    return pd.DataFrame(rows)


def fake_store(stored):
    """正本の『保存済みの最後の行』を返す偽物"""
    supabase_io.credentials = lambda: ("http://x", "k")

    def req(method, url, key, body=None, extra_headers=None):
        t = url.split("ticker=eq.")[1].split("&")[0]
        rows = [{"date": LAST_STORED.date().isoformat(), "close": stored[t]}] if t in stored else []
        return 200, json.dumps(rows).encode("utf-8")
    supabase_io._request = req


def run(panel, stored):
    fake_store(stored)
    try:
        um.check_incoming(panel, list(DAYS), log=lambda m: None)
        return None
    except SystemExit as e:
        return str(e)


def main():
    print("― 1. 正常 ―")
    p = make_panel({"7203.T": [3000, 3010, 2990, 3005, 3020, 3015]})
    check(run(p, {"7203.T": 3000}) is None, "（対照）ふつうの値動きは通す")

    print("\n― 2. 配当くらいのずれ（保存済みと今回で 2%）―")
    p = make_panel({"8316.T": [2940, 2950, 2960, 2955, 2970, 2965]})
    check(run(p, {"8316.T": 3000}) is None, "（対照）配当の調整くらいは通す", "比 0.98")

    print("\n― 3. ★10/02 と同じ壊れ方★ 保存済み 1,350円 → 今回 0.6円 ―")
    p = make_panel({"8766.T": [0.607, 0.61, 0.62, 0.61, 0.60, 0.61]})
    msg = run(p, {"8766.T": 1350.13})
    check(msg is not None and "8766.T" in msg and "食い違う" in msg,
          "★提供元が過去を作り直した（壊れた値）を止める★")

    print("\n― 4. 分割（記録なし）で保存済みと今回が 1/3 ―")
    p = make_panel({"8035.T": [1400, 1410, 1420, 1415, 1430, 1425]})
    msg = run(p, {"8035.T": 4200})
    check(msg is not None and "8035.T" in msg, "★分割で過去が作り直されたのを止める★")

    print("\n― 5. 足す日どうしで1日に +80% ―")
    p = make_panel({"9984.T": [9000, 9010, 16200, 16300, 16250, 16280]})
    msg = run(p, {"9984.T": 9000})
    check(msg is not None and "1日で" in msg and "9984.T" in msg, "★1日で ±50% 以上を止める★")

    print("\n― 6. 固定した検証期間に書こうとした ―")
    p = make_panel({"7203.T": [3000, 3010, 2990, 3005, 3020, 3015]})
    fake_store({"7203.T": 3000})
    try:
        um.check_incoming(p, [pd.Timestamp("2026-09-01")] + list(DAYS), log=lambda m: None)
        check(False, "★検証期間への書き込みを止める★", "止まらなかった")
    except SystemExit as e:
        check("検証期間" in str(e), "★検証期間への書き込みを止める★")

    print("\n― 7. 止めたら1件も書かない（メッセージにそう書いてある）―")
    p = make_panel({"8766.T": [0.607, 0.61, 0.62, 0.61, 0.60, 0.61]})
    msg = run(p, {"8766.T": 1350.13}) or ""
    check("何も書いていません" in msg, "何も書いていないと明示する")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
