# -*- coding: utf-8 -*-
"""`docs/indicator_verdict.json` を元CSVと書籍の原稿の両方と突き合わせる。

■ 何を見るか
  1. 26種そろっているか
  2. 数字が元CSVと1つずつ合うか（出口A・B の全項目 × 8区分）
  3. **「使える9個」が「8区分すべてで +0.3% 超」と一致するか**
     ── 判定と基準がずれたらここで落ちる。書籍の判定を正としつつ、
        その判定が数字で裏づけられていることを毎回確かめる
  4. 3段の内訳が 9 / 4 / 13 か、原稿の早見表と1つずつ一致するか
  5. `below_bar`（+0.3% に届かなかった区分）が正しいか
     ── 「使える」は空、それ以外は1つ以上
  6. **書籍本文の「一言」が混ざっていないか**
     ── 『新規学習は非推奨』のように推奨語を含み、統計でもない
  7. 銘柄コード・推奨語が無いか / 応答サイズ

    python mcp/tools/verify_indicator_verdict.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
BOOK = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                    "21_本当に使えるテクニカル指標", "結合原稿_第3稿.md")
SRC = os.path.join(r"D:\マイドキュメント\Claude\Projects\株式投資開発",
                   "physics_dex", "results", "summary.csv")
DOC = os.path.join(ROOT, "docs", "indicator_verdict.json")

BENCH = "ランダム(ベンチマーク)"
BAR = 0.3
REGIME_COLS = ["A_上昇局面超過%", "A_下落局面超過%", "A_前半超過%", "A_後半超過%",
               "B_上昇局面超過%", "B_下落局面超過%", "B_前半超過%", "B_後半超過%"]

NG_WORDS = ["推奨", "おすすめ", "お勧め", "買うべき", "売るべき", "買い時", "売り時",
            "儲か", "必勝", "狙い目", "勝てる"]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from legal_check import find_codes  # noqa: E402

fails = []
ok = lambda m: print("OK   %s" % m)


def ng(m):
    print("NG   %s" % m)
    fails.append(m)


def num(v):
    s = str(v).replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def main():
    for p in (BOOK, SRC):
        if not os.path.exists(p):
            print("元データが見つかりません（別の端末では検証をとばします）: %s" % p)
            return 0
    doc = json.load(io.open(DOC, encoding="utf-8"))
    items = doc["items"]
    rows = {r["指標"]: r for r in csv.DictReader(io.open(SRC, encoding="utf-8-sig"))}
    bench = rows.pop(BENCH)

    # 原稿の早見表（判定の正本）と、そこに書かれた「一言」
    book = {}
    phrases = []
    s = io.open(BOOK, encoding="utf-8").read()
    blk = s[s.index("# 全判定早見表"):][:4000]
    for m in re.finditer(
            r"^\|\s*[◎△×](使える|条件付き|捨てろ)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*$",
            blk, re.M):
        book[m.group(2)] = m.group(1)
        if m.group(4).strip():
            phrases.append(m.group(4).strip())

    # ---- 1. 本数
    if len(items) == 26 == len(rows) == len(book):
        ok("26種そろっている（JSON / 元CSV / 原稿の早見表）")
    else:
        ng("本数が違う: JSON %d / CSV %d / 原稿 %d" % (len(items), len(rows), len(book)))

    # ---- 2. 数字が元CSVと合うか
    bad = 0
    for it in items:
        r = rows.get(it["indicator"])
        if not r:
            ng("元CSVに無い指標: %s" % it["indicator"])
            continue
        if it["category"] != r["カテゴリ"]:
            ng("%s: カテゴリが違う" % it["indicator"]); bad += 1
        pairs = [
            (it["exit_a"]["trades"], r["A_トレード数"], "A取引数"),
            (it["exit_a"]["win_rate_pct"], r["A_勝率%"], "A勝率"),
            (it["exit_a"]["payoff"], r["A_ペイオフ"], "Aペイオフ"),
            (it["exit_a"]["expectancy_pct"], r["A_期待値%"], "A期待値"),
            (it["exit_a"]["excess_expectancy_pct"], r["A_超過期待値%"], "A超過"),
            (it["exit_b"]["trades"], r["B_トレード数"], "B取引数"),
            (it["exit_b"]["win_rate_pct"], r["B_勝率%"], "B勝率"),
            (it["exit_b"]["payoff"], r["B_ペイオフ"], "Bペイオフ"),
            (it["exit_b"]["expectancy_pct"], r["B_期待値%"], "B期待値"),
            (it["exit_b"]["excess_expectancy_pct"], r["B_超過期待値%"], "B超過"),
            (it["exit_b"]["pf"], r["B_PF"], "B_PF"),
        ]
        for got, want, label in pairs:
            w = num(want)
            if w is None or abs(w - got) > 0.005:
                ng("%s %s: %s ≠ CSV %s" % (it["indicator"], label, got, want)); bad += 1
        # 8区分
        for reg, col in zip(it["regimes"], REGIME_COLS):
            w = num(r[col])
            if w is None or abs(w - reg["excess_pct"]) > 0.005:
                ng("%s %s: 区分の超過が違う" % (it["indicator"], col)); bad += 1
    if bad == 0:
        ok("すべての数字が元CSVと一致（%d 種 × 19項目）" % len(items))

    # ベンチマークも元CSVと合っているか（超過収益の基準点なので外せない）
    b = doc["benchmark"]
    if (abs(num(bench["A_勝率%"]) - b["exit_a_win_rate_pct"]) < 0.005
            and abs(num(bench["B_PF"]) - b["exit_b_pf"]) < 0.005):
        ok("ベンチマーク（ランダムな売買）の数字も一致")
    else:
        ng("ベンチマークの数字が元CSVと違う")

    # ---- 3. ★「使える9個」が数字から再現できるか★
    calc = sorted(n for n, r in rows.items()
                  if all((num(r[c]) or -99) > BAR for c in REGIME_COLS))
    said = sorted(it["indicator"] for it in items if it["verdict"] == "usable")
    if calc == said and len(calc) == 9:
        ok("「使える」9個が「8区分すべて +%.1f%% 超」と完全に一致" % BAR)
    else:
        ng("使えるの顔ぶれが基準と合わない: 計算のみ %s / 判定のみ %s"
           % (sorted(set(calc) - set(said)), sorted(set(said) - set(calc))))

    # ---- 4. 3段の内訳と、原稿との一致
    want = {"使える": "usable", "条件付き": "conditional", "捨てろ": "not_usable"}
    miss = [it["indicator"] for it in items
            if want.get(book.get(it["indicator"], "")) != it["verdict"]]
    if miss:
        ng("原稿の判定と食い違う指標: %s" % miss)
    else:
        ok("26種すべての判定が原稿の早見表と一致")
    s2 = doc["summary"]
    if (s2["usable"], s2["conditional"], s2["not_usable"]) == (9, 4, 13):
        ok("内訳は 使える9 / 条件付き4 / 捨てろ13（書籍と一致）")
    else:
        ng("内訳が違う: %s" % s2)

    # ---- 5. below_bar（なぜ「使える」でないか）
    bad = 0
    for it in items:
        weak = [reg for reg in it["regimes"] if (reg["excess_pct"] or -99) <= BAR]
        if len(weak) != it["below_bar_count"] or len(weak) != len(it["below_bar"]):
            ng("%s: below_bar の数が合わない" % it["indicator"]); bad += 1
        if it["verdict"] == "usable" and weak:
            ng("%s: 「使える」なのに基準未達の区分がある" % it["indicator"]); bad += 1
        if it["verdict"] != "usable" and not weak:
            ng("%s: 基準は全部通っているのに「使える」でない" % it["indicator"]); bad += 1
    if bad == 0:
        ok("below_bar（+%.1f%% に届かなかった区分）が全26種で正しい" % BAR)

    # ---- 6. ★書籍本文の「一言」が混ざっていないか★
    blob = json.dumps(doc, ensure_ascii=False)
    leaked = [p for p in phrases if p and p in blob]
    if leaked:
        ng("書籍の一言が入っている（推奨語を含み統計でもない）: %s" % leaked[:3])
    else:
        ok("書籍の「一言」が1つも入っていない（%d 件と照合）" % len(phrases))

    # ---- 7. 法務の線
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
    # ★数値の中は見ない★（期待値 0.0045 のような小数が4桁コードに見える）
    codes = find_codes(doc)   # 共通の検査（legal_check.py）
    # TOPIX500 は指数名、490銘柄は母集団の数。4桁ではないので当たらない
    if codes:
        ng("銘柄コードらしき4桁が文字列に混ざっている: %s" % sorted(set(codes))[:10])
    else:
        ok("銘柄コードが無い（文字列中の4桁はすべて年号）")

    hit = [w for w in NG_WORDS if w in joined]
    if hit:
        ng("投資助言に読める語が入っている: %s" % hit)
    else:
        ok("推奨語が1つも無い（%d 語と照合）" % len(NG_WORDS))

    for key, label in (("disclaimer", "免責"), ("source_book", "出所の書籍"),
                       ("headline", "結論の1行"), ("criteria", "判定の基準"),
                       ("benchmark", "ランダムとの比較基準")):
        if doc.get(key):
            ok("%s がある" % label)
        else:
            ng("%s が無い" % label)

    # ---- 8. 応答サイズ
    size = len(blob.encode("utf-8"))
    print("")
    print("参考: ファイル全体 %s バイト" % format(size, ","))
    light = dict(doc)
    light["items"] = [{k: v for k, v in it.items()
                       if k not in ("regimes", "below_bar", "aliases")}
                      for it in doc["items"]]
    n_light = len(json.dumps(light, ensure_ascii=False).encode("utf-8"))
    n_one = len(json.dumps(doc["items"][0], ensure_ascii=False).encode("utf-8"))
    for label, n in (("一覧（既定）", n_light), ("1件の詳細", n_one)):
        if n <= 51200:
            ok("%s %s バイト（50KB 以内）" % (label, format(n, ",")))
        else:
            ng("%s %s バイト。50KB を超える" % (label, format(n, ",")))

    print("")
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
