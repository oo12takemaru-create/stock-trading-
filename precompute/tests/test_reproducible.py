# -*- coding: utf-8 -*-
"""公開数字が再現することの受け入れ試験（2026-09-22）。

    python precompute/tests/test_reproducible.py --env-file ../ruletrade-app/.env.local

■ 何を守るか
公開している「10年の検証結果」が、同じ期間を計算し直すたびに違う答えになっていた。

  公開17件（asof 2026-09-08〜09-21）の実測
    trades 1,796〜1,820 / max_dd −24.2〜−27.1 / cum 333.9〜352.5
    9/10 は同じ日に3回走って 1,799 / 1,810 / 1,814

原因はコードではなく母集団だった（下の「対照」を参照）。
この試験は **2回続けて同じ答えが出ること** を機械で確かめる。

■ 見るもの
  1. 正本の母集団が ACTIVE_TICKERS と完全に一致する（欠けも余りも無い）
  2. 2回読んで2回まわし、summary が一致する
  3. **採用トレードの並び（指紋）まで一致する**
     summary だけ見ると、別の建玉で偶然同じ丸め値になった場合に見逃す
  4. universe_effective（実際に使えた銘柄数）が 337 で安定する
  5. ★対照★ 1銘柄抜くと答えが変わる
     ここが「変わらない」なら、この試験は何も見ていないことになる
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import panel_from_db  # noqa: E402
import portfolio_run  # noqa: E402
import supabase_io  # noqa: E402
from universe import ACTIVE_TICKERS, EXCLUDED_TICKERS  # noqa: E402

PASS = FAIL = 0


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def fingerprint(trades):
    """採用されたトレードを順番ごと1つの文字列にまとめる"""
    h = hashlib.sha256()
    for t in trades:
        h.update(("%s|%s|%s|%.6f|%.6f|%d|%s\n" % (
            t.ticker, t.entry_date, t.exit_date,
            t.entry_price, t.exit_price, t.shares, t.reason)).encode())
    return h.hexdigest()[:16]


def check_no_raw_universe():
    """★銘柄一覧を JAPAN_STOCKS から直接組んでいないか★（2026-09-22）

    run_pipeline の既定を ACTIVE_TICKERS にしたが、build_metrics.py と
    precompute_metrics.py が `tickers=list(JAPAN_STOCKS.keys())` で
    上書きしていたため効いていなかった。**既定だけ直しても、渡す側が
    古いままなら意味がない。** 全期間の再構築が、除外したはずの3銘柄で
    止まって初めて気づいた。

    セクター表のように「340銘柄すべて」を意図して使う場所はあるので、
    一覧そのものの参照は禁止しない。禁じるのは**取得する銘柄を組む形**だけ。
    """
    import glob
    import re

    bad = []
    pat = re.compile(r"tickers\s*=\s*list\(\s*JAPAN_STOCKS")
    root = os.path.dirname(HERE)
    for f in sorted(glob.glob(os.path.join(root, "*.py"))):
        src = open(f, encoding="utf-8").read()
        for i, line in enumerate(src.split("\n"), 1):
            if pat.search(line):
                bad.append("%s:%d" % (os.path.basename(f), i))
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default="")
    args = ap.parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)

    print("― 0. 取得する銘柄を一覧から直接組んでいないか ―")
    bad = check_no_raw_universe()
    check(not bad, "★tickers = list(JAPAN_STOCKS...) が無い★",
          "、".join(bad) if bad else "ACTIVE_TICKERS に揃っている")

    print("\n― 1. 正本の母集団 ―")
    print("   一覧 %d 銘柄（除外 %d: %s）"
          % (len(ACTIVE_TICKERS), len(EXCLUDED_TICKERS), ", ".join(sorted(EXCLUDED_TICKERS))))

    runs = []
    for i in (1, 2):
        print("\n― %d回目：正本から読んで計算する ―" % i)
        # ★毎回読み直す★ 同じ DataFrame を使い回すと、読み出しの揺れを見逃す
        panel, market = panel_from_db.load_panel(log=lambda m: None)
        used = sorted(panel["ticker"].unique())
        result, trades = portfolio_run.run(panel, market, log=lambda m: None)
        s = result["summary"]
        fp = fingerprint(trades)
        runs.append((s, fp, used, result))
        print("   trades %s / win %s / pf %s / maxDD %s / cum %s"
              % (s["trades"], s["win_rate"], s["pf"], s["max_dd"], s["total_return"]))
        print("   指紋 %s / 使えた銘柄 %d" % (fp, len(used)))

    a, b = runs[0], runs[1]

    print("\n― 2. 2回が一致するか ―")
    check(a[0] == b[0], "★summary が一致★",
          "" if a[0] == b[0] else json.dumps(
              {k: [a[0].get(k), b[0].get(k)] for k in a[0] if a[0].get(k) != b[0].get(k)},
              ensure_ascii=False))
    check(a[1] == b[1], "★採用トレードの並び（指紋）が一致★", "%s / %s" % (a[1], b[1]))
    check(a[2] == b[2], "使えた銘柄が一致", "%d / %d" % (len(a[2]), len(b[2])))

    print("\n― 3. 母集団が一覧どおりか ―")
    check(a[2] == list(ACTIVE_TICKERS), "★使えた銘柄 == ACTIVE_TICKERS★",
          "%d / %d" % (len(a[2]), len(ACTIVE_TICKERS)))
    check(len(a[2]) == 337, "universe_effective が 337", str(len(a[2])))

    print("\n― 4. ★対照★ 1銘柄抜けば答えは変わるか ―")
    # ここが「変わらない」なら、上の一致は何も証明していない
    panel, market = panel_from_db.load_panel(log=lambda m: None)
    drop = "6532.T" if "6532.T" in set(panel["ticker"]) else a[2][0]
    r2, t2 = portfolio_run.run(panel[panel["ticker"] != drop], market, log=lambda m: None)
    changed = (r2["summary"] != a[0]) or (fingerprint(t2) != a[1])
    check(changed, "★%s を抜くと答えが変わる★" % drop,
          "maxDD %s → %s" % (a[0]["max_dd"], r2["summary"]["max_dd"]))

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
