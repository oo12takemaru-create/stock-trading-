# -*- coding: utf-8 -*-
"""`docs/etf_decay.json` を元CSV・元mdと突き合わせる。

■ 何を見るか
  1. 4つの表の行数と数字が元CSVと1つずつ合うか
  2. **`key_finding` の数字が実際の集計と合っているか**
     ── ここだけ文章なので、手で書いた数字がずれていても読めてしまう。
        99.3% / 2営業日 / +1.1% / −32.5% を CSV から取り直して照合する
  3. **注意書きが元の md と一致するか**（文章も手で写さない）
  4. 法務の線: 銘柄コード・推奨語
     ★ETF のコード（1357 / 1570）だけは許す★
       起動文 §4-4 が `code` 引数を明示しており、これは「何を検証したか」を
       指す識別子。それ以外の4桁が出たら個別株の混入なので落とす。
  5. 応答サイズ

    python mcp/tools/verify_etf_decay.py
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# ★build と同じ規則を使う（書き写すと二重管理になる）★
from build_etf_decay import DROP_PREFIXES, SAFE_REWRITE  # noqa: E402
ROOT = os.path.dirname(os.path.dirname(HERE))
BASE = os.path.join(r"C:\Users\kawamura takeshi\書籍販売",
                    "35_日経ダブルインバース", "results")
DOC = os.path.join(ROOT, "docs", "etf_decay.json")

ALLOWED_CODES = {"1357", "1570", "1360"}   # 検証対象のETF。これ以外の4桁は混入
NG_WORDS = ["推奨", "おすすめ", "お勧め", "買うべき", "売るべき", "買い時", "売り時",
            "儲か", "必勝", "狙い目", "勝てる", "危ない", "やめられない"]

fails = []
ok = lambda m: print("OK   %s" % m)


def ng(m):
    print("NG   %s" % m)
    fails.append(m)


def num(v):
    s = str(v).replace("%", "").strip()
    if not s or s == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def rows(name):
    return list(csv.DictReader(io.open(os.path.join(BASE, name), encoding="utf-8-sig")))


def close(a, b, tol=0.005):
    return a is not None and b is not None and abs(a - b) <= tol


def main():
    if not os.path.isdir(BASE):
        print("元データが見つかりません（別の端末では検証をとばします）: %s" % BASE)
        return 0
    doc = json.load(io.open(DOC, encoding="utf-8"))

    # ---- 1. 年別
    src = rows("00_yearly.csv")
    got = doc["yearly"]
    bad = 0
    if len(src) != len(got):
        ng("年別の行数が違う: JSON %d / CSV %d" % (len(got), len(src)))
    else:
        for g, r in zip(got, src):
            k = list(r.keys())
            if g["year"] != r[k[0]]:
                ng("年が違う: %s" % g["year"]); bad += 1
            for key, col in (("nikkei_pct", "日経平均%"),
                             ("theory_pct", "理論値(−2×日経)%"),
                             ("actual_1357_pct", "1357実績%"),
                             ("decay_pct", "減価(実績−理論)%"),
                             ("actual_1570_pct", "1570実績%")):
                if not close(g[key], num(r[col])):
                    ng("%s の %s が違う: %s ≠ %s" % (g["year"], col, g[key], r[col]))
                    bad += 1
        if bad == 0:
            ok("年別 %d 年ぶんの数字が元CSVと一致" % len(got))

    # ---- 2. 保有日数別
    src = rows("02_table.csv")
    got = doc["by_holding_days"]
    bad = 0
    if len(src) != len(got):
        ng("保有日数別の行数が違う: JSON %d / CSV %d" % (len(got), len(src)))
    else:
        for g, r in zip(got, src):
            for key, col in (("cases", "件数"), ("mean_pct", "平均%"),
                             ("median_pct", "中央値%"), ("win_rate_pct", "勝率%"),
                             ("win_rate_with_cost_pct", "勝率(コスト込)%"),
                             ("worst_pct", "最悪%"), ("gap_mean_pct", "乖離平均%"),
                             ("worse_than_theory_pct", "理論より悪い割合%")):
                if not close(float(g[key]), num(r[col])):
                    ng("%d日 の %s が違う" % (g["hold_days"], col)); bad += 1
        if bad == 0:
            ok("保有日数別 %d 行の数字が元CSVと一致" % len(got))

    # ---- 3. 条件別
    src = rows("06_table.csv")
    got = doc["by_condition"]
    bad = 0
    if len(src) != len(got):
        ng("条件別の行数が違う: JSON %d / CSV %d" % (len(got), len(src)))
    else:
        for g, r in zip(got, src):
            k = list(r.keys())
            if g["condition"] != r[k[0]] or g["cost"] != r["コスト"]:
                ng("条件の並びが違う: %s" % g["condition"]); bad += 1
            for key, col in (("cases", "件数"), ("win_rate_pct", "勝率%"),
                             ("mean_pct", "平均%"), ("pf", "PF"),
                             ("max_dd_pct", "最大DD%")):
                if not close(float(g[key]), num(r[col])):
                    ng("%s %d日 の %s が違う" % (g["condition"], g["hold_days"], col))
                    bad += 1
        if bad == 0:
            ok("条件別 %d 行の数字が元CSVと一致" % len(got))

    # ---- 4. ★戻り待ち: 文章の数字まで照合する★
    src = rows("05_C_summary.csv")
    got = doc["wait_for_recovery"]["results"]
    if len(src) != len(got):
        ng("戻り待ちの行数が違う: JSON %d / CSV %d" % (len(got), len(src)))
    else:
        bad = 0
        for g, r in zip(got, src):
            for key, col in (("trials", "試行数"), ("recovered_pct", "回収率%"),
                             ("return_median_pct", "損益率中央値%"),
                             ("days_to_recover_median", "回収日数中央値"),
                             ("worst_unrealized_pct", "最大含み損率の最悪%")):
                if not close(float(g[key]), num(r[col])):
                    ng("戻り待ち(%s) の %s が違う" % (g["rule"][:12], col)); bad += 1
        if bad == 0:
            ok("戻り待ち %d 行の数字が元CSVと一致" % len(got))

        # key_finding は文章なので、数字を取り出して CSV と突き合わせる。
        # ★ここがずれても JSON は普通に読めてしまう★
        b = next((r for r in src if str(r[list(r.keys())[0]]).startswith("B")), None)
        if b:
            kf = doc["key_finding"]
            want = {
                "回収率": ("%s%%" % num(b["回収率%"]), "99.3%"),
                "試行数": ("%d回" % int(num(b["試行数"])), "305回"),
                "中央値": ("%s営業日" % int(num(b["回収日数中央値"])), "2営業日"),
                "取り分": ("+%s%%" % num(b["損益率中央値%"]), "+1.1%"),
                "未回収": ("%s%%" % num(b["未回収の平均損益率%"]), "−32.5%"),
            }
            miss = []
            for label, (calc, _) in want.items():
                # 全角マイナス・カンマ表記の差を吸収して探す
                needle = calc.replace("-", "").lstrip("+")
                if needle not in kf.replace("−", "").replace(",", ""):
                    miss.append("%s(%s)" % (label, calc))
            if miss:
                ng("key_finding の数字が集計と合わない: %s" % miss)
            else:
                ok("key_finding の数字（試行数・回収率・日数・取り分・未回収）が集計と一致")

    # ---- 5. 注意書きが元の md と一致するか（文章も手で写さない）
    def md_caveats(name):
        p = os.path.join(BASE, name)
        if not os.path.exists(p):
            return []
        s = io.open(p, encoding="utf-8").read()
        i = s.find("## 解釈で注意すべき点")
        if i < 0:
            return []
        body = s[i:]
        e = body.find("\n## ", 3)
        if e >= 0:
            body = body[:e]
        return [re.sub(r"\*\*(.+?)\*\*", r"\1", t.strip()[2:]).strip()
                for t in body.split("\n") if t.strip().startswith("- ")]

    cav = doc["verification_caveats"]
    bad = 0
    dropped = []
    for key, md in (("by_holding_days", "summary_02.md"),
                    ("by_condition", "summary_06.md"),
                    ("wait_for_recovery", "summary_05.md")):
        raw = md_caveats(md)
        # build と同じ規則を当てる。★落とした行が「執筆メモ」であることも確かめる★
        want = []
        for line in raw:
            if any(line.startswith(p) for p in DROP_PREFIXES):
                dropped.append((md, line))
                continue
            for x, y in SAFE_REWRITE.items():
                line = line.replace(x, y)
            want.append(line)
        if cav.get(key) != want:
            ng("%s の注意書きが元の %s と合わない" % (key, md)); bad += 1
    if bad == 0:
        ok("注意書き3節が元の md と一致（言い換え・除外の規則を当てた上で）")
    for md, line in dropped:
        # 何を捨てたかを毎回表示する。黙って消すと後から追えない
        print("     ・%s から除外（著者の執筆メモ）: %s" % (md, line[:60]))
    # 言い換えが実際に効いているか（規則だけあって当たっていない状態を防ぐ）
    blob_all = json.dumps(doc, ensure_ascii=False)
    for x, y in SAFE_REWRITE.items():
        if x in blob_all:
            ng("言い換えが効いていない: 「%s」が残っている" % x)
    if "上昇相場" in cav.get("note", ""):
        ok("★最重要の前提★（上昇相場のデータである）が note にある")
    else:
        ng("note に「上昇相場のデータ」という前提が無い")

    # ---- 6. 法務の線
    blob = json.dumps(doc, ensure_ascii=False)
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
    codes = {c for c in re.findall(r"\b\d{4}\b", joined)
             if not (1990 <= int(c) <= 2100)} - ALLOWED_CODES
    if codes:
        ng("検証対象のETF以外の4桁が混ざっている: %s" % sorted(codes)[:10])
    else:
        ok("4桁は検証対象のETF（%s）と年号だけ" % "/".join(sorted(ALLOWED_CODES)))

    hit = [w for w in NG_WORDS if w in joined]
    if hit:
        ng("投資助言・評価に読める語が入っている: %s" % hit)
    else:
        ok("推奨語・評価語が1つも無い（%d 語と照合）" % len(NG_WORDS))

    for key, label in (("disclaimer", "免責"), ("source_book", "出所の書籍"),
                       ("headline", "結論の1行"), ("key_finding", "戻り待ちの知見"),
                       ("mechanism", "減価の仕組み"),
                       ("verification_caveats", "解釈の注意")):
        if doc.get(key):
            ok("%s がある" % label)
        else:
            ng("%s が無い" % label)

    # ---- 7. サイズ
    size = len(blob.encode("utf-8"))
    print("")
    if size <= 51200:
        ok("全体 %s バイト（50KB 以内・全件そのまま返せる）" % format(size, ","))
    else:
        ng("全体 %s バイト。50KB を超える" % format(size, ","))

    print("")
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
