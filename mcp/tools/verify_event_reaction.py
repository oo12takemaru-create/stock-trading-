# -*- coding: utf-8 -*-
"""`docs/event_reaction.json` が元CSVと一致するかを機械で確かめる。

■ なぜ機械で見るか
勝率や p値 は**手で書き写すと必ずずれる**。ずれても JSON は普通に読めるので
気づけない。しかも「検証結果を返す場所」と名乗っている以上、数字がずれるのは
ただのバグより悪い。

あわせて**法務の線**も見る。銘柄コード・銘柄名が1つでも混ざっていないか。
`baskets.csv`（code と name を持つ499行）が同じフォルダにあるので、
取り違えると「出来事 → 買う銘柄」を返す道具になってしまう。

    python mcp/tools/verify_event_reaction.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                   "36_株の連想大全", "検証", "out", "summary_main.csv")
DOC = os.path.join(os.path.dirname(os.path.dirname(HERE)), "docs", "event_reaction.json")
BASKETS = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                       "36_株の連想大全", "検証", "baskets.csv")

TAGS = {0: "t0", 1: "t1", 5: "t5", 21: "t21", 63: "t63"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import find_codes  # noqa: E402

fails = []


def ok(msg):
    print("OK   %s" % msg)


def ng(msg):
    print("NG   %s" % msg)
    fails.append(msg)


def num(v):
    s = str(v).strip()
    try:
        return float(s)
    except ValueError:
        return None


def main():
    if not os.path.exists(SRC):
        print("元CSVが見つかりません（別の端末では検証をとばします）: %s" % SRC)
        return 0
    rows = {r["event_type"]: r for r in csv.DictReader(io.open(SRC, encoding="utf-8-sig"))}
    doc = json.load(io.open(DOC, encoding="utf-8"))
    events = doc["events"]

    # 1. 件数
    if len(events) == len(rows):
        ok("出来事の数が一致（%d 件）" % len(events))
    else:
        ng("件数が違う: JSON %d / CSV %d" % (len(events), len(rows)))

    # 2. 数字が1つずつ元CSVと合うか
    bad = 0
    for e in events:
        r = rows.get(e["event_type"])
        if not r:
            ng("CSV に無い出来事: %s" % e["event_type"]); continue
        if e["name"] != r["name"]:
            ng("%s: 名前が違う" % e["event_type"]); bad += 1
        if e["samples"] != int(num(r["n"]) or 0):
            ng("%s: 標本数が違う" % e["event_type"]); bad += 1
        if e["verdict"] != (r["judge1"] or "").strip():
            ng("%s: 判定が違う" % e["event_type"]); bad += 1
        for w in e["windows"]:
            tag = TAGS[w["days"]]
            want = num(r["sp1_%s" % tag])
            got = w["excess_pct"]
            if want is None or abs(want * 100 - got) > 0.005:
                ng("%s t%s: 超過収益が違う（JSON %s / CSV %s）"
                   % (e["event_type"], w["days"], got, None if want is None else want * 100))
                bad += 1
            wantw = num(r["sp1_win_%s" % tag])
            if wantw is not None and abs(wantw * 100 - w["win_rate_pct"]) > 0.05:
                ng("%s t%s: 勝率が違う" % (e["event_type"], w["days"])); bad += 1
    if bad == 0:
        ok("すべての数字が元CSVと一致（%d 件 × 窓）" % len(events))

    # 2-b. 判定の注記が全部埋まっているか
    # ★注記が空だと「初動型」とだけ返る★ エージェントは言葉の意味を知らない
    blank = sorted({e["verdict"] for e in events if not e.get("verdict_note")})
    if blank:
        ng("判定に説明が無いものがある（JUDGE_NOTE に追加すること）: %s" % blank)
    else:
        ok("判定6種すべてに説明が付いている")

    # 3. ★法務の線★ 銘柄が混ざっていないか
    blob = json.dumps(doc, ensure_ascii=False)

    # ★数値の中を探さない★
    # p値 0.0045 のような小数を正規表現で拾うと「0045」が銘柄コードに見える。
    # 銘柄コードが紛れ込むとしたら**文字列の値**（名前・別名・説明）なので、
    # 文字列だけを集めて調べる。
    texts = []

    def walk(v):
        if isinstance(v, str):
            texts.append(v)
        elif isinstance(v, dict):
            for k, x in v.items():
                texts.append(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(doc)
    joined = " ".join(texts)
    codes = find_codes(doc)   # 共通の検査（legal_check.py）
    if codes:
        ng("銘柄コードらしき4桁が文字列に混ざっている: %s" % sorted(set(codes))[:10])
    else:
        ok("銘柄コードが無い（文字列中の4桁はすべて年号）")

    if os.path.exists(BASKETS):
        names = set()
        for r in csv.DictReader(io.open(BASKETS, encoding="utf-8-sig")):
            n = (r.get("name") or "").strip()
            if len(n) >= 3:
                names.add(n)
        leaked = sorted(n for n in names if n in blob)
        if leaked:
            ng("バスケットの銘柄名が漏れている: %s" % leaked[:5])
        else:
            ok("バスケットの銘柄名が1つも入っていない（%d 件と照合）" % len(names))

    # 4. 返すべきものが揃っているか
    for key, label in (("disclaimer", "免責"), ("source_book", "出所の書籍"),
                       ("key_finding", "初動型の注意")):
        if doc.get(key):
            ok("%s がある" % label)
        else:
            ng("%s が無い" % label)

    # 5. 応答サイズ（起動文 §9: 1ツール 50KB を超えたら分ける）
    size = len(blob.encode("utf-8"))
    if size <= 51200:
        ok("全体サイズ %s バイト（50KB 以内）" % f"{size:,}")
    else:
        ng("全体サイズ %s バイト。50KB を超える＝要約版と detail に分ける" % f"{size:,}")

    print()
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
