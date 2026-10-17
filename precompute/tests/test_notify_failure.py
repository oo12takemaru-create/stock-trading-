# -*- coding: utf-8 -*-
"""前計算の失敗通知（notify_failure.build_message）の試験。送信はしない。

    python precompute/tests/test_notify_failure.py

確かめること:
  - 失敗した段の名前と「まず見るところ」が本文に出る
  - ワークフローの step id と STEP_INFO の鍵が食い違っていない（食い違うと段の名前が出ない）
  - テストを選んでも、本当に失敗した段があれば「失敗の知らせ」として送る
  - 失敗した段が分からないときも黙らない
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import notify_failure as nf  # noqa: E402

PASS = FAIL = 0
URL = "https://github.com/x/y/actions/runs/1"


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def main():
    print("― 1. 取得の段で落ちた（10/14〜10/16 の形）―")
    steps = {"guard": {"outcome": "success"}, "metrics": {"outcome": "failure"},
             "rebuild": {"outcome": "skipped"}}
    mode, subj, body = nf.build_message(steps, URL, False, "2026-10-16")
    check(mode == "failure", "失敗の知らせになる")
    check("2026-10-16" in subj, "件名に日付が入る")
    check(nf.STEP_INFO["metrics"][0] in body, "失敗した段の名前が出る")
    check(nf.STEP_INFO["rebuild"][0] not in body, "skipped の段は出さない")
    check(URL in body, "実行ログの URL が出る")

    print("\n― 2. 公開の検査で落ちた（10/13 の形）―")
    mode, _, body = nf.build_message({"publish": {"outcome": "failure"}}, URL, False, "2026-10-13")
    check(nf.STEP_INFO["publish"][0] in body, "公開の段の名前が出る")

    print("\n― 3. テストを選んだが、本当に失敗した段がある ―")
    mode, subj, _ = nf.build_message({"metrics": {"outcome": "failure"}}, URL, True, "2026-10-16")
    check(mode == "failure" and "テスト" not in subj, "失敗の知らせを優先する")

    print("\n― 4. テストだけ ―")
    mode, subj, _ = nf.build_message({}, URL, True, "2026-10-16")
    check(mode == "test" and "テスト" in subj, "テストの件名になる")

    print("\n― 5. 段を特定できない（checkout などで落ちた）でも黙らない ―")
    mode, _, body = nf.build_message({}, URL, False, "2026-10-16")
    check(mode == "failure" and "特定できませんでした" in body, "失敗として送る")

    print("\n― 6. ワークフローの step id と STEP_INFO が揃っている ―")
    wf = os.path.join(HERE, "..", "..", ".github", "workflows", "precompute-daily.yml")
    with open(wf, encoding="utf-8") as f:
        text = f.read()
    # update ジョブの段だけを見る（通知の段 today/mark/mail は対象外）
    ids = set(re.findall(r"^\s+id:\s*([A-Za-z0-9_-]+)\s*$", text, flags=re.M))
    ids -= {"today", "mark", "mail"}
    check(len(ids) > 0, "step id を1つ以上読めた", str(sorted(ids)))   # 0件を成功と見なさない
    missing = sorted(ids - set(nf.STEP_INFO))
    extra = sorted(set(nf.STEP_INFO) - ids)
    check(not missing, "全ての step id に名前がある", "足りない: %s" % missing if missing else "")
    check(not extra, "STEP_INFO に存在しない id が無い", "余分: %s" % extra if extra else "")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
