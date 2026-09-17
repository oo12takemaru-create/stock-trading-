# -*- coding: utf-8 -*-
"""`get_event_reaction` が読む2つの JSON を元CSVと突き合わせる。

決算後ドリフト（31）と需給イベント（29）は**同じツールに統合される**ので、
検証も1本にまとめる。連想27種（36）は `verify_event_reaction.py` を参照。

■ 何を見るか
  1. 4つ／1つの集計表の数字が元CSVと1つずつ合うか
  2. **headline と key_finding の数字が集計と合っているか**
     ── ここだけ文章なので、ずれていても読めてしまう
  3. **元CSVの行を1つも落としていないか**（需給25行すべてが分類されたか）
  4. 法務の線
     ★明細を読んでいないことの裏取り★
       PEAD の `events_raw.csv`、需給の `buyback_*.csv` は銘柄コード・企業名つき。
       JSON に4桁コードが1つも無いことで、混入していないことを確かめる。
  5. 応答サイズ

    python mcp/tools/verify_event_datasets.py
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
from legal_check import ng_words, walk_strings, find_codes  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))
DEV = r"D:\マイドキュメント\Claude\Projects\株式投資開発"
PEAD_DIR = os.path.join(DEV, "PEAD検証", "results")
SD_CSV = os.path.join(DEV, "需給イベント検証", "results", "00_マスター検定結果.csv")

fails = []
ok = lambda m: print("OK   %s" % m)


def ng(m):
    print("NG   %s" % m)
    fails.append(m)


def num(v):
    s = str(v).strip()
    if not s or s == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def close(a, b, tol=0.005):
    return a is not None and b is not None and abs(a - b) <= tol


def rows(path):
    return list(csv.DictReader(io.open(path, encoding="utf-8-sig")))


def has_number(text, value, nd=2):
    """文章の中にその数字があるか。全角マイナス・符号の揺れを吸収する。"""
    flat = text.replace("−", "-").replace(",", "")
    for cand in ("%.*f" % (nd, value), "%+.*f" % (nd, value),
                 "%.*f" % (nd - 1, value), "%.*f" % (1, value)):
        if cand.lstrip("+") in flat:
            return True
    return False


def check_legal(doc, label, allowed_codes=()):
    """銘柄コード・推奨語・免責。★明細の混入をここで捕まえる★"""
    texts = [t for _, t in walk_strings(doc)]
    joined = " ".join(texts)
    # ★数値の中は見ない★（p値 0.0007 の小数部が銘柄コードに見える）
    codes = find_codes(doc, allowed=allowed_codes)   # 共通の検査（legal_check.py）
    if codes:
        ng("%s: 銘柄コードらしき4桁がある（明細が混ざった可能性）: %s"
           % (label, sorted(codes)[:10]))
    else:
        ok("%s: 4桁は年号だけ（銘柄の明細は混ざっていない）" % label)

    hit = [w for w in ng_words() if w.lower() in joined.lower()]
    if hit:
        ng("%s: 推奨語が入っている: %s" % (label, hit))
    else:
        ok("%s: 推奨語が1つも無い" % label)

    for key, name in (("disclaimer", "免責"), ("source_book", "出所の書籍"),
                      ("headline", "結論の1行"), ("key_finding", "いちばん大事な点"),
                      ("not_included", "返さないものの説明")):
        if not doc.get(key):
            ng("%s: %s が無い" % (label, name))
    size = len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    if size <= 51200:
        ok("%s: %s バイト（50KB 以内）" % (label, format(size, ",")))
    else:
        ng("%s: %s バイト。50KB を超える" % (label, format(size, ",")))


# ---------------------------------------------------------------- 決算後ドリフト
def check_earnings():
    doc = json.load(io.open(os.path.join(ROOT, "docs", "earnings_drift.json"),
                            encoding="utf-8"))
    print("── 決算後ドリフト（31）")

    # 1. 分位別
    src = rows(os.path.join(PEAD_DIR, "01_分位別ドリフト.csv"))
    got = doc["by_quantile"]
    bad = 0
    if len(src) != len(got):
        ng("分位の数が違う: JSON %d / CSV %d" % (len(got), len(src)))
    else:
        for g, r in zip(got, src):
            if g["cases"] != int(num(r["件数"])):
                ng("分位%d: 件数が違う" % g["quantile"]); bad += 1
            if not close(g["initial_move_pct"], num(r["初動r0"]) * 100):
                ng("分位%d: 初動が違う" % g["quantile"]); bad += 1
            for w in g["windows"]:
                if not close(w["car_pct"], num(r["CAR%d" % w["days"]]) * 100):
                    ng("分位%d CAR%d が違う" % (g["quantile"], w["days"])); bad += 1
                if not close(w["p_value"], num(r["p%d" % w["days"]]), 0.0001):
                    ng("分位%d p%d が違う" % (g["quantile"], w["days"])); bad += 1
        if bad == 0:
            ok("分位別 %d × 5窓の数字が元CSVと一致" % len(got))

    # 2. ロングショート / 3. 頑健性 / 4. バックテスト
    for key, fname, cols in (
        ("long_short", "02_ロングショート.csv",
         (("spread_pct", "スプレッド", 100), ("p_value", "p", 1),
          ("win_rate_pct", "勝率", 100))),
        ("robustness", "05_頑健性_CAR5.csv",
         (("cases", "件数", 1), ("spread_pct", "スプレッド", 100),
          ("p_value", "p", 1))),
        ("rule_backtest", "07_バックテスト.csv",
         (("cases", "件数", 1), ("before_cost_pct", "コスト前", 100),
          ("after_cost_pct", "コスト後", 100), ("p_value", "p", 1))),
    ):
        src = rows(os.path.join(PEAD_DIR, fname))
        got = doc[key]
        bad = 0
        if len(src) != len(got):
            ng("%s の行数が違う: JSON %d / CSV %d" % (key, len(got), len(src)))
            continue
        for g, r in zip(got, src):
            for jk, ck, mul in cols:
                tol = (0.0001 if jk == "p_value"
                       else 0.05 if "win_rate" in jk   # 小数1桁で保持している
                       else 0.005)
                if not close(float(g[jk]), num(r[ck]) * mul, tol):
                    ng("%s %s: %s が違う" % (key, list(g.values())[0], ck)); bad += 1
        if bad == 0:
            ok("%s %d 行の数字が元CSVと一致" % (key, len(got)))

    # ★文章の数字★
    q5 = next(q for q in doc["by_quantile"] if q["quantile"] == 5)
    q1 = next(q for q in doc["by_quantile"] if q["quantile"] == 1)
    w5 = lambda q: next(w for w in q["windows"] if w["days"] == 5)
    ls5 = next(x for x in doc["long_short"] if x["window"].startswith("+1〜+5"))
    miss = []
    if not has_number(doc["headline"], ls5["spread_pct"]):
        miss.append("headline のスプレッド")
    for label, v in (("分位5の+5日", w5(q5)["car_pct"]),
                     ("分位1の+5日", w5(q1)["car_pct"])):
        if not has_number(doc["key_finding"], v):
            miss.append("key_finding の" + label)
    if miss:
        ng("文章の数字が集計と合わない: %s" % miss)
    else:
        ok("headline と key_finding の数字が集計と一致（+5日のスプレッドと分位1・5）")

    # ★好反応が有意でないことを落としていないか★
    # ここが消えると「好決算を買えば取れる」と読まれる
    if w5(q5)["significant"]:
        ng("分位5の+5日が有意になっている（元データでは p=0.91）")
    else:
        ok("分位5の+5日が「有意でない」と出ている（好反応側は取れていない）")

    check_legal(doc, "決算後ドリフト")


# ------------------------------------------------------------------ 需給イベント
def check_supply_demand():
    doc = json.load(io.open(os.path.join(ROOT, "docs", "supply_demand_events.json"),
                            encoding="utf-8"))
    print("")
    print("── 需給イベント（29）")
    src = rows(SD_CSV)
    k = list(src[0].keys())

    got = [m for e in doc["events"] for m in e["measurements"]]
    if len(got) == len(src):
        ok("元CSV %d 行をすべて分類している（落としていない）" % len(src))
    else:
        ng("行数が違う: JSON %d / CSV %d（分類から漏れた行がある）"
           % (len(got), len(src)))

    # 元CSVの各行が JSON のどこかに1対1であるか
    bad = 0
    for r in src:
        hit = [m for m in got
               if m["category"] == r[k[0]] and m["target"] == r[k[1]]
               and m["window"] == r[k[2]]]
        if len(hit) != 1:
            ng("元CSVの行が見つからない/重複: %s %s %s" % (r[k[0]], r[k[1]], r[k[2]]))
            bad += 1
            continue
        m = hit[0]
        if not close(m["mean_car_pct"], num(r[k[3]])):
            ng("%s %s: 平均CARが違う" % (r[k[1]], r[k[2]])); bad += 1
        if not close(m["p_value"], num(r[k[5]]), 0.0001):
            ng("%s %s: p値が違う" % (r[k[1]], r[k[2]])); bad += 1
        if m["note"] != r[k[6]]:
            ng("%s %s: 備考が違う" % (r[k[1]], r[k[2]])); bad += 1
    if bad == 0:
        ok("全 %d 行の数字と備考が元CSVと一致" % len(src))

    def pick(eid, t, w):
        ms = next(e for e in doc["events"] if e["event_id"] == eid)["measurements"]
        return next(m for m in ms if t in m["target"] and w in m["window"])

    miss = []
    for text, label, v in (
        (doc["headline"], "除外の直前3日", pick("topix_exclusion", "低減10回", "(-3,-1)")["mean_car_pct"]),
        (doc["headline"], "除外の実施後20日", pick("topix_exclusion", "低減10回", "(0,+20)")["mean_car_pct"]),
        (doc["key_finding"], "採用の前20日", pick("topix_addition", "指定日", "(-20,-1)")["mean_car_pct"]),
        (doc["key_finding"], "組入れ後5日", pick("topix_addition", "組入れ日", "(+1,+5)")["mean_car_pct"]),
    ):
        if not has_number(text, v):
            miss.append(label)
    if miss:
        ng("文章の数字が集計と合わない: %s" % miss)
    else:
        ok("headline と key_finding の数字が集計と一致（除外・採用の4つ）")

    # ★「p=0」を注記なしで返していないか★
    # 元CSVに 0.0 の行がある。そのまま返すと「有り得ない値」を読ませることになる
    bare = [m for m in got if m["p_value"] == 0 and not m.get("p_note")]
    if bare:
        ng("p=0 を注記なしで返している: %s" % [m["window"] for m in bare])
    else:
        n0 = len([m for m in got if m["p_value"] == 0])
        ok("p=0 の %d 行すべてに「厳密なゼロではない」の注記がある" % n0)

    # ★自社株買いを含まないことを明記しているか★
    # 起動文が挙げた質問の1つに答えられないので、黙って落とさない
    if "自社株買い" in doc.get("not_included", ""):
        ok("自社株買いを含まない理由が書いてある（明細しか無いため）")
    else:
        ng("自社株買いが無いことの説明が not_included に無い")

    # 日経225 は有意でないものばかり。そう読めるようになっているか
    n225 = next(e for e in doc["events"] if e["event_id"] == "nikkei225_addition")
    if any(m["significant"] for m in n225["measurements"]):
        ng("日経225 に有意な項目がある（元データでは無い）")
    else:
        ok("日経225 はすべて「有意でない」と出ている")

    check_legal(doc, "需給イベント", allowed_codes=("1306",))


def main():
    if not os.path.isdir(PEAD_DIR) or not os.path.exists(SD_CSV):
        print("元データが見つかりません（別の端末では検証をとばします）")
        return 0
    check_earnings()
    check_supply_demand()
    print("")
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
