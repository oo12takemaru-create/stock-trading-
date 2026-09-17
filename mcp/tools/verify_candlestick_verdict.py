# -*- coding: utf-8 -*-
"""`docs/candlestick_verdict.json` を**元CSVまで遡って**確かめる。

■ なぜ元CSVまで遡るか
中間の `ruletrade_sakata_rules.json` と突き合わせるだけでは、その中間ファイルが
ずれていた場合に両方そろって間違う。検証を回した生の出力
（`sakata_all_periods.csv` 3,372行）と照合すれば、間に何段あっても効く。

■ 何を見るか
  1. 12本そろっているか
  2. 数字が元CSVと1つずつ合うか（取引回数・勝率・PF・平均超過・p値／3窓すべて）
  3. **verdict が全部 not_adopted か**（採用はゼロ本。1本でも adopted なら不合格）
  4. 別枠は逆三尊だけか
  5. **三空叩き込みの不採用理由が全文で入っているか**
     ── PF 3.04 でも落ちた理由がこのツールで最も価値のある答えなので、
        要約されていたら不合格にする
  6. **逆三尊の不採用理由が差し替わっているか**
     ── 元の文には「回避フィルターの候補」という検証者の設計メモが入っている。
        そのまま出すと行動の指示に読める
  7. 法務の線: 銘柄コード・銘柄名・推奨語が1つも無いか
  8. 応答サイズ（要約版と1件 detail の両方）

    python mcp/tools/verify_candlestick_verdict.py
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
BASE = os.path.join(r"C:\Users\kawamura takeshi\書籍販売", "32_本間宗久_酒田五法", "検証")
CSV_SRC = os.path.join(BASE, "sakata_all_periods.csv")
JSON_SRC = os.path.join(BASE, "ruletrade_sakata_rules.json")
DOC = os.path.join(ROOT, "docs", "candlestick_verdict.json")

# 出力の pattern_id → 元CSV の pattern 列（規定値の回＝ __main）
TO_CSV = {
    "sakata_sanzan": "sanzan__main",
    "sakata_sanzon": "sanzon__main",
    "sakata_sankawa_triple_bottom": "sankawaTB__main",
    "sakata_gyaku_sanzon": "gyakusanzon__main",
    "sakata_morning_star": "morning_star__main",
    "sakata_evening_star": "evening_star__main",
    "sakata_sanku_tatakikomi": "sanku_tataki__main",
    "sakata_sanku_fumiage": "sanku_fumiage__main",
    "sakata_aka_sanpei": "aka_sanpei__main",
    "sakata_kuro_sanpei": "kuro_sanpei__main",
    "sakata_age_sanpo": "age_sanpo__main",
    "sakata_sage_sanpo": "sage_sanpo__main",
}

# 出してはいけない語。投資助言に読める言い回し
NG_WORDS = [
    "推奨", "おすすめ", "お勧め", "買うべき", "売るべき", "買いましょう",
    "売りましょう", "買いです", "売りです", "買わない方", "狙い目", "必勝",
    "儲か", "利益が出ます", "有望",
]

fails = []
ok = lambda m: print("OK   %s" % m)


def ng(m):
    print("NG   %s" % m)
    fails.append(m)


def main():
    if not os.path.exists(CSV_SRC):
        print("元CSVが見つかりません（別の端末では検証をとばします）: %s" % CSV_SRC)
        return 0
    doc = json.load(io.open(DOC, encoding="utf-8"))
    pats = doc["patterns"]

    # 元CSV を (pattern, horizon) で引けるように。規定値・全期間・全局面の回だけ
    csv_rows = {}
    for r in csv.DictReader(io.open(CSV_SRC, encoding="utf-8-sig")):
        if r["period"] == "full" and r["context"] == "all":
            csv_rows[(r["pattern"], int(r["horizon"]))] = r

    # ---- 1. 本数
    if len(pats) == 12:
        ok("形が12本そろっている")
    else:
        ng("本数が違う: %d 本（12 のはず）" % len(pats))

    # ---- 2. 数字が元CSVと合うか（3窓すべて）
    bad = 0
    checked = 0
    for p in pats:
        key = TO_CSV.get(p["pattern_id"])
        if not key:
            ng("%s: 元CSVとの対応が TO_CSV に無い" % p["pattern_id"])
            continue
        for w in p["by_horizon"]:
            r = csv_rows.get((key, w["hold_days"]))
            if not r:
                ng("%s h=%d: 元CSVに行が無い" % (p["pattern_id"], w["hold_days"]))
                bad += 1
                continue
            checked += 1
            if w["trades"] != int(r["n"]):
                ng("%s h=%d: 取引回数 %s ≠ CSV %s"
                   % (p["name"], w["hold_days"], w["trades"], r["n"]))
                bad += 1
            if abs(float(r["win"]) * 100 - w["win_rate_pct"]) > 0.005:
                ng("%s h=%d: 勝率が違う" % (p["name"], w["hold_days"]))
                bad += 1
            if abs(float(r["pf"]) - w["pf"]) > 0.0005:
                ng("%s h=%d: PF %s ≠ CSV %s"
                   % (p["name"], w["hold_days"], w["pf"], round(float(r["pf"]), 3)))
                bad += 1
            if abs(float(r["excess"]) * 100 - w["mean_excess_pct"]) > 0.0005:
                ng("%s h=%d: 平均超過 %s ≠ CSV %s"
                   % (p["name"], w["hold_days"], w["mean_excess_pct"],
                      round(float(r["excess"]) * 100, 3)))
                bad += 1
            if abs(float(r["p"]) - w["p_value"]) > 0.00005:
                ng("%s h=%d: p値が違う" % (p["name"], w["hold_days"]))
                bad += 1
    if bad == 0:
        ok("すべての数字が元CSVと一致（%d 窓）" % checked)

    # primary が by_horizon のどれかと同じであること
    for p in pats:
        hs = {w["hold_days"]: w for w in p["by_horizon"]}
        pr = p["primary"]
        if hs.get(pr["hold_days"]) != pr:
            ng("%s: primary が by_horizon の同じ窓と食い違う" % p["name"])
            break
    else:
        ok("primary が by_horizon の該当窓と一致")

    # ---- 3. ★採用はゼロ本★
    verdicts = {}
    for p in pats:
        verdicts[p["verdict"]] = verdicts.get(p["verdict"], 0) + 1
    if verdicts == {"not_adopted": 12}:
        ok("verdict は12本すべて not_adopted（採用ゼロ本）")
    else:
        ng("verdict の内訳が想定と違う: %s" % verdicts)
    if doc["summary"]["adopted"] == 0:
        ok("summary.adopted が 0")
    else:
        ng("summary.adopted が %s（0 のはず）" % doc["summary"]["adopted"])

    # 元JSONの adoption が全部 rejected であることも見る（元が変わったら気づく）
    if os.path.exists(JSON_SRC):
        src = json.load(io.open(JSON_SRC, encoding="utf-8"))
        adoptions = set(r.get("adoption") for r in src["rules"])
        if adoptions == {"rejected"}:
            ok("元データ側も全12本 adoption=rejected")
        else:
            ng("元データの adoption が変わっている: %s → verdict を見直すこと"
               % sorted(adoptions))

    # ---- 4. 別枠は逆三尊だけか
    filters = [p["pattern_id"] for p in pats if p.get("filter_candidate")]
    if filters == ["sakata_gyaku_sanzon"]:
        ok("別枠で保持は逆三尊のみ")
    else:
        ng("別枠の顔ぶれが違う: %s" % filters)

    # ---- 5. ★三空叩き込みの理由が全文か★
    tataki = next(p for p in pats if p["pattern_id"] == "sakata_sanku_tatakikomi")
    reason = tataki["rejection_reason"]
    for phrase in ("発見期", "確認期", "符号が反転", "全期間の平均だけが良く見える"):
        if phrase not in reason:
            ng("三空叩き込みの理由から「%s」が落ちている（要約せず全文で返すこと）" % phrase)
            break
    else:
        ok("三空叩き込みの不採用理由が全文で入っている（%d 文字）" % len(reason))
    if tataki["primary"]["pf"] == 3.036:
        ok("三空叩き込みの PF 3.036 が出ている（高PFでも不採用の例）")
    else:
        ng("三空叩き込みの PF が %s" % tataki["primary"]["pf"])
    if tataki["primary"]["checks_failed"]:
        ok("どの基準で落ちたかが出ている: %s" % tataki["primary"]["checks_failed"])
    else:
        ng("三空叩き込みの checks_failed が空")

    # ---- 6. ★逆三尊の理由が差し替わっているか★
    gyaku = next(p for p in pats if p["pattern_id"] == "sakata_gyaku_sanzon")
    g_reason = gyaku["rejection_reason"]
    if os.path.exists(JSON_SRC):
        orig = next(r for r in src["rules"] if r["rule_id"] == "sakata_gyaku_sanzon")
        if g_reason == orig["rejection_reason"]:
            ng("逆三尊の理由が元のまま（設計メモを含むので差し替えること）")
        else:
            ok("逆三尊の理由は差し替え済み")
    for word in ("回避フィルター", "買わない", "ビルダー側"):
        if word in g_reason:
            ng("逆三尊の理由に社内向けの語が残っている: %s" % word)
            break
    else:
        ok("逆三尊の理由に社内向けの語が無い")

    # ---- 7. ★法務の線★
    blob = json.dumps(doc, ensure_ascii=False)

    # ★数値の中を探さない★ p値 0.0135 の小数部が銘柄コードに見えるため、
    # 文字列の値とキーだけを集めて調べる
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
    codes = [c for c in re.findall(r"\b\d{4}\b", joined)
             if not (1990 <= int(c) <= 2100)]
    # 「東証プライム1,550銘柄」「3,642銘柄」はカンマ入りなので \b\d{4}\b に当たらない
    if codes:
        ng("銘柄コードらしき4桁が文字列に混ざっている: %s" % sorted(set(codes))[:10])
    else:
        ok("銘柄コードが無い（文字列中の4桁はすべて年号）")

    hit = [w for w in NG_WORDS if w in joined]
    if hit:
        ng("投資助言に読める語が入っている: %s" % hit)
    else:
        ok("推奨語が1つも無い（%d 語と照合）" % len(NG_WORDS))

    # ---- 8. 返すべきものが揃っているか
    for key, label in (("disclaimer", "免責"), ("source_book", "出所の書籍"),
                       ("headline", "結論の1行"), ("key_finding", "高PFでも不採用の理由"),
                       ("criteria", "採用基準"), ("policy", "不採用も開示する方針")):
        if doc.get(key):
            ok("%s がある" % label)
        else:
            ng("%s が無い" % label)

    # ---- 9. 応答サイズ（起動文 §9: 1ツール 50KB）
    size = len(blob.encode("utf-8"))
    print("")
    print("参考: ファイル全体 %s バイト" % f"{size:,}")

    # サーバが返す2つの形を実際に組んで測る
    light = dict(doc)
    light["patterns"] = [{k: v for k, v in p.items()
                          if k not in ("by_horizon", "regime", "size_reference",
                                       "params_tested")} for p in doc["patterns"]]
    n_light = len(json.dumps(light, ensure_ascii=False).encode("utf-8"))
    n_one = len(json.dumps(doc["patterns"][0], ensure_ascii=False).encode("utf-8"))
    for label, n in (("一覧（既定）", n_light), ("1件の詳細", n_one)):
        if n <= 51200:
            ok("%s %s バイト（50KB 以内）" % (label, f"{n:,}"))
        else:
            ng("%s %s バイト。50KB を超える" % (label, f"{n:,}"))

    print("")
    print("判定: %s" % ("合格" if not fails else "不合格（%d 件）" % len(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
