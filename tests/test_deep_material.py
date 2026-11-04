# -*- coding: utf-8 -*-
"""deep_material の検査（数字・人名・禁止語・字数）。python -X utf8 tests/test_deep_material.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import deep_material as d  # noqa: E402

SRC = "日経62,188.63は2026-10-06の高値70,683.98から-12.02%。過去5年で1回。全期間61回: 崩れ50.8%・上抜け31.1%"
BODY = ("日経平均は2026-10-06の高値70,683.98から-12.02%の位置にある。今と同じ条件がそろった局面は過去5年で1回だけ。"
        "条件を外した全期間61回では、20営業日後に崩れが50.8%、上抜けが31.1%で、崩れの側が多い並び。"
        "高値から12%下げた今は、1回の例よりも61回の並びのほうを物差しにする。3つの型のうち歴史の物差しで読むと、"
        "崩れの側が多い並びが、今の位置の目安になる。上抜けの側は31.1%にとどまる。")
SRC += "・20営業日"


def ok(cond, msg):
    if not cond:
        print("NG:", msg)
        sys.exit(1)
    print("ok:", msg)


ok(d.numbers_ok(BODY, SRC) == [], "入力にある数字・丸め（12）・数え方（3つ）は通る")
ok(d.numbers_ok("崩れは55.0%", SRC) == ["55.0"], "入力に無い数字は落ちる")
ok(d.numbers_ok("高値70,684円", SRC) == [], "70,683.98 を整数に丸めたものは通る")
ok(d.names_ok("相場の型で読む") is None, "人名の無い文は通る")
ok(d.names_ok("山田氏は強気") is not None, "「〜氏」は落ちる")
ok(d.names_ok("テスト流の読み方") is not None, "「〜流の」は落ちる")
ok(d.names_ok("半導体が主流の指数・世界の潮流に") is None, "主流・潮流は人を指さないので通る")
ok(d.words_ok("買い越しが続き、売り越しが減った。自社株買いも多い") is None, "需給の名詞は通る")
ok(d.words_ok("押し目買いの局面") is not None, "「買い」は落ちる")
ok(d.words_ok("必ず戻る") is not None, "「必ず」は落ちる")
ok(d.validate_deep({"type": "history", "title": "物差し", "body": BODY}, SRC) is None, "deep の正しい形は通る")
ok(d.validate_deep({"type": "history", "title": "物差し", "body": "短い"}, SRC) is not None, "字数が足りない deep は落ちる")
ok(d.validate_deep({"type": "x", "title": "物差し", "body": BODY}, SRC) is not None, "type が不正な deep は落ちる")
data = {"headline": "x", "deep": {"type": "history", "title": "物差し", "body": BODY.replace("50.8%", "60.8%")}}
ok(d.take_checked(data, "deep", d.validate_deep, SRC) and "deep" not in data and "headline" in data,
   "落ちた deep だけ外し、本体は残す")
print("all ok")
