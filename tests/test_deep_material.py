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
# 2026-11-06 の型: deep は4文（事実→翻訳→なぜ→次に見る数字）・数字5つまで・200〜300字・見出しは問い q
SRC2 = ("今回当たった: SOX指数（半導体）が−3%以上下落 → 過去184回 翌日の日経 平均-1.352%・上昇19.6%\n"
        "全部の日2440日: 平均+0.062%・上昇53.2%\n日経平均: 62,184.32\n金が+2%以上上昇 → 過去78回 平均+0.533%・上昇56.4%")
DEEP4 = ("今朝は半導体指数（SOX）が3%以上下げた翌日に当たり、過去184回の同じ日に日経平均が上がったのは19.6%だけだった（条件なしの全期間では53.2%）。"
         "ふだんは五分五分で上がる日経が、この条件の翌日に限っては上がりにくい、という形だ。"
         "日経平均は半導体株の比重が大きく、米国の半導体の急落が翌朝の日本の半導体株にそのまま持ち越されやすいからだ。"
         "次に見るのは、寄り付き後の日経平均が前日終値の62,184円を上回って推移できるかどうかだ。")
Q = "半導体が3%下げた翌日、日経はどうなってきたか？"
ok(d.count_sentences(DEEP4) == 4 and d.count_numbers(DEEP4) == 5, "見本は4文・数字5つ")
ok(d.count_numbers("2026-10-06の高値70,683.98から-12.02%。この2つ") == 3, "日付は1つ、「この2つ」は数えない")
ok(d.validate_deep({"type": "history", "q": Q, "body": DEEP4}, SRC2) is None, "新しい型の deep は通る")
five = DEEP4.replace("からだ。", "からだ。金の上昇は翌日にほぼ効かない。", 1)
ok("文が5つ" in (d.validate_deep({"type": "history", "q": Q, "body": five}, SRC2) or ""), "文が5つの deep は落ちる")
six = DEEP4.replace("上がりにくい", "上がりにくい（金は過去78回）", 1)
ok("数字が6個" in (d.validate_deep({"type": "history", "q": Q, "body": six}, SRC2) or ""), "数字が6つの deep は落ちる")
ok("字" in (d.validate_deep({"type": "history", "q": Q, "body": DEEP4.replace("かどうかだ", "かどうか" + "ということ" * 20 + "だ")}, SRC2) or ""), "300字を超える deep は落ちる")
ok("q" in (d.validate_deep({"type": "history", "body": DEEP4}, SRC2) or ""), "問いの見出しが無い deep は落ちる")
ok("q" in (d.validate_deep({"type": "history", "q": "半導体の急落", "body": DEEP4}, SRC2) or ""), "「？」「か」で終わらない見出しは落ちる")
ok(d.validate_deep({"type": "history", "q": Q, "body": "短い"}, SRC2) is not None, "字数が足りない deep は落ちる")
ok(d.validate_deep({"type": "x", "q": Q, "body": DEEP4}, SRC2) is not None, "type が不正な deep は落ちる")
data = {"headline": "x", "deep": {"type": "history", "q": Q, "body": DEEP4.replace("184回", "185回")}}
ok(d.take_checked(data, "deep", d.validate_deep, SRC2) and "deep" not in data and "headline" in data,
   "落ちた deep だけ外し、本体は残す")

# long_view: 型ごとに3文（結論→翻訳→次に見る数字）・数字5つまで・問い q。落ちた型だけ外して残りは出す
PART = ("条件なしの全期間の61回では、その後に崩れたのが50.8%で、上抜けの31.1%を上回る位置にいる。"
        "戻りを待つより、下に振れやすい並びがまだ続いているということだ。"
        "次に見るのは、日経平均が高値70,683.98からの下げ幅を縮められるかどうかだ（長い目で見た位置の確認）。")
SRC3 = SRC + "\n" + SRC2
def lv(parts):
    return {"title": "10年で見た支え手", "parts": [dict(k=k, q="今の位置は過去と比べてどうか？", body=b) for k, b in zip(d.LONG_KEYS, parts)]}
good = [PART] * 4
data = {"view": "x", "long_view": lv(good)}
ok(d.check_long_view_parts(data, SRC3) == [] and len(data["long_view"]["parts"]) == 4, "4つの型がすべて通る")
bad = list(good)
bad[1] = PART + "崩れの側が多い。"
data = {"view": "x", "long_view": lv(bad)}
errs = d.check_long_view_parts(data, SRC3)
ok(len(errs) == 1 and "world" in errs[0] and [p["k"] for p in data["long_view"]["parts"]] == ["history", "why", "decade"],
   "文が4つの型（world）だけ外し、残り3つは出す")
bad = list(good)
bad[3] = PART.replace("61回", "61回・184回・-1.352%")
data = {"view": "x", "long_view": lv(bad)}
errs = d.check_long_view_parts(data, SRC3)
ok(len(errs) == 1 and "decade" in errs[0] and "数字" in errs[0], "数字が6つ以上の型（decade）だけ外す")
p0 = lv(good); p0["parts"][0].pop("q")
data = {"view": "x", "long_view": p0}
errs = d.check_long_view_parts(data, SRC3)
ok(len(errs) == 1 and "history" in errs[0], "問いの見出しが無い型は外す")
print("all ok")
