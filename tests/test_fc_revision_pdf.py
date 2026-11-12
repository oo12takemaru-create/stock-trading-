# -*- coding: utf-8 -*-
"""業績予想・配当予想の修正の PDF の表の読み取り（fc_revision_pdf.py）のテスト

fixtures/fc_revision_pdf_words.json は 2026-11-12 に TDnet で開示された実物の PDF から、表のまわりの
「語＋位置」だけを抜き出したもの（PyMuPDF の get_text("words")）。数字は PDF を目で開いて照合した。
PyMuPDF が無くても動く（語の位置から先だけを試す）。
  6695 キャストリコ   売上高 3,780→5,800・営業 225→367（上方）。売上高の数字が見出しの「百万円」の欄に入る型
  2201 森永製菓       「売 上 高 営 業 利 益」の1文字ずつ離れた見出し。営業 22,800→21,000（下方）・売上の増減は「－」
  9791 ビケンテクノ   配当。見出しが上寄せ・数字が下寄せ。年間 36→40円（中間は実績 20円を足して検算）
  5142 アキレス       業績の表は経常利益と純利益の列がつながる（読まない）。配当 50→60円
  628A カドー         単位が千円。純利益 247,529→370,588千円（上方・ほかは据置）
  7090 リグア         営業 71→85（上方）・売上高と純利益は下方＝まちまち
  1431 リブワーク     第2四半期（累計）だけの修正 → 中間期
  3521 テルマー湯     売上・営業・経常は据置（増減額「－」）、純利益 180→△50（下方）
  485A パワーエックス レンジ予想と増減額の行が読めない → 採用しない
  1853 森組           配当 14→0円（無配）
"""
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fc_revision_pdf as P  # noqa: E402
from fc_revision import is_rv_title  # noqa: E402

NG = 0
FX = json.loads((Path(__file__).parent / "fixtures" / "fc_revision_pdf_words.json").read_text(encoding="utf-8"))


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


def run(code, pages=None):
    f = FX[code]
    return P.parse_words([[tuple(w) for w in p] for p in (pages or f["pages"])], f["title"])


# ---- 業績の表
r = run("66950")
eq((r["dir"], r["per"], r["by"], r["src"]), ("上方", "通期", "営業利益", "pdf"), "6695 向き")
eq(r["prev"], {"sales": 3780.0, "op": 225.0, "ord": 218.0, "np": 150.0}, "6695 修正前")
eq(r["cur"], {"sales": 5800.0, "op": 367.0, "ord": 362.0, "np": 250.0}, "6695 修正後")

r = run("22010")
eq((r["dir"], r["by"], r.get("mix")), ("下方", "営業利益", None), "2201 1文字ずつの見出し")
eq((r["prev"]["op"], r["cur"]["op"], r["prev"]["sales"], r["cur"]["sales"]), (22800.0, 21000.0, 257000.0, 257000.0), "2201 数字")

r = run("628A0")
eq((r["dir"], r["by"]), ("上方", "純利益"), "628A 千円")
eq((r["prev"]["np"], r["cur"]["np"], r["prev"]["sales"]), (247.5, 370.6, 3614.5), "628A 千円→百万円")

r = run("70900")
eq((r["dir"], r["mix"], r.get("mixed")), ("上方", "売上高は下方・純利益は下方", True), "7090 まちまち")

r = run("14310")
eq((r["dir"], r["per"]), ("上方", "中間期"), "1431 中間期だけの修正")

r = run("35210")
eq((r["dir"], r["by"], r["cur"]["np"], r["cur"]["op"]), ("下方", "純利益", -50.0, 320.0), "3521 純利益だけの修正")

r = run("485A0")
eq((r["dir"], "表の型に合わない" in r["why"]), (None, True), "485A 採用しない")

# ---- 配当の表
r = run("97910")
eq(r.get("div"), {"part": "年間", "prev": 36.0, "cur": 40.0, "dir": "増配"}, "9791 配当（上寄せの見出し）")
eq("dir" in r, False, "9791 配当だけ（業績の向きは書かない）")

r = run("51420")
eq(r.get("div"), {"part": "年間", "prev": 50.0, "cur": 60.0, "dir": "増配"}, "5142 配当")
eq(r.get("prev"), None, "5142 列がつながった業績の表は読まない")

r = run("18530")
eq(r["div"]["dir"], "減配", "1853 無配")

# ---- 検算が合わなければ採用しない: 6695 の今回修正予想の営業利益 367 → 377 に書き換える
pages = copy.deepcopy(FX["66950"]["pages"])
hit = 0
for p in pages:
    for w in p:
        if w[4] == "367":
            w[4] = "377"
            hit += 1
eq(hit, 1, "改ざん用の語が1つ")
r = run("66950", pages)
eq((r["dir"], "合わない" in r["why"]), (None, True), "B−A が増減額と合わない → 不採用")

# ---- 子会社の業績予想・画像だけ
eq(P.parse_words([[]], "子会社における業績予想の修正に関するお知らせ")["why"].startswith("子会社"), True, "子会社")
eq(P.parse_words([[]], "通期業績予想の修正に関するお知らせ")["why"], "PDF に文字が無い（画像だけ）", "画像だけ")

# ---- 細かい部品
eq(P.parse_cell("△1,570"), (-1570.0, -1570.0), "△")
eq(P.parse_cell("0～50"), (0.0, 50.0), "レンジ")
eq(P.parse_cell("－"), P.DASH, "横線")
eq(P.parse_cell("+202.8％", pct=True), (202.8, 202.8), "率")
eq(P.parse_yen("55円00"), 55.0, "円00（銭が外れた形）")
eq(P.parse_yen("３円00銭"), 3.0, "全角")
eq(P.parse_yen("70.86"), 70.86, "小数")
eq(P.check((100, 100), (110, 110), (10, 10), None, 0), True, "検算 OK")
eq(P.check((5749, 5749), (6080, 6080), (330, 330), None, 0), True, "検算 端数±1（3504 丸八）")
eq(P.check((100, 100), (110, 110), (20, 20), None, 0), False, "検算 NG")
eq(P.check((100, 100), (110, 110), None, (10.0, 10.0), 0), True, "率で検算")
eq(P.check((100, 100), (110, 110), None, (12.0, 12.0), 0), False, "率で検算 NG")
eq(is_rv_title("2027年３月期通期業績予想の修正に関するお知らせ"), True, "表題")
eq(is_rv_title("2026年12月期通期連結業績の修正に関するお知らせ"), True, "表題（業績の修正）")
eq(is_rv_title("第35回新株予約権(行使価額修正条項付)の大量行使に関するお知らせ"), False, "表題（新株予約権）")
eq(is_rv_title("2026年９月期 決算説明会資料"), False, "表題（無関係）")

print("fc_revision_pdf:", "OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
