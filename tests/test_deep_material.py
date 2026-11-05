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
# 2026-11-06 の型: 3文・数字4つまで・180〜260字（結論→根拠→条件）
SRC2 = ("今回当たった: SOX指数（半導体）が−3%以上下落 → 過去184回 翌日の日経 平均-1.352%・上昇19.6%\n"
        "金が+2%以上上昇 → 過去78回 平均+0.533%・上昇56.4%\nCME先物は現物比+1.38%")
DEEP3 = ("半導体指数（SOX）が3%以上下げた日の翌日、日経平均は過去184回で平均−1.35%と下げる側に偏っており、今朝はちょうどこの条件に当たる。"
         "金の上昇と原油の下落も同時に点灯したが、この2つは翌日の日経の上げ下げがほぼ五分で偏りが小さく、今朝の物差しとして効いているのは半導体の下げだけだ。"
         "ただしCME先物が示す+1.4%の高寄りが前場のうちに保たれるなら、過去の平均どおりにはならず、この物差しより当日の寄り付きの強さを重く見る。")
ok(d.count_sentences(DEEP3) == 3 and d.count_numbers(DEEP3) == 4, "見本は3文・数字4つ（「この2つ」は数えない）")
ok(d.count_numbers("2026-10-06の高値70,683.98から-12.02%") == 3, "日付は1つと数える")
ok(d.validate_deep({"type": "history", "title": "半導体の急落が翌日に響く朝", "body": DEEP3}, SRC2) is None, "新しい型の deep は通る")
four = DEEP3.replace("だけだ。", "だけだ。上昇は19.6%にとどまる。", 1)
ok("文が4つ" in (d.validate_deep({"type": "history", "title": "物差し", "body": four}, SRC2) or ""), "文が4つの deep は落ちる")
five = DEEP3.replace("偏りが小さく", "偏りが小さく（金は過去78回）", 1)
ok("数字が5個" in (d.validate_deep({"type": "history", "title": "物差し", "body": five}, SRC2) or ""), "数字が5つの deep は落ちる")
ok("字" in (d.validate_deep({"type": "history", "title": "物差し", "body": DEEP3.replace("重く見る", "重く見る" + "ことになる" * 10)}, SRC2) or ""), "260字を超える deep は落ちる")
ok(d.validate_deep({"type": "history", "title": "物差し", "body": "短い"}, SRC2) is not None, "字数が足りない deep は落ちる")
ok(d.validate_deep({"type": "x", "title": "物差し", "body": DEEP3}, SRC2) is not None, "type が不正な deep は落ちる")
data = {"headline": "x", "deep": {"type": "history", "title": "物差し", "body": DEEP3.replace("184回", "185回")}}
ok(d.take_checked(data, "deep", d.validate_deep, SRC2) and "deep" not in data and "headline" in data,
   "落ちた deep だけ外し、本体は残す")

# long_view: 型ごとに3文・数字4つまで。落ちた型だけ外して残りは出す
PART = ("今の日経は、条件なしの全期間61回で見ると崩れの側が多い位置にいる。"
        "20営業日後に崩れたのは50.8%で、上抜けの31.1%を上回る。"
        "似た局面の上げが続くなら、この見方は変わる。")
SRC3 = SRC + "\n" + SRC2
def lv(parts):
    return {"title": "10年で見た支え手", "parts": [dict(k=k, h="小見出し", body=b) for k, b in zip(d.LONG_KEYS, parts)]}
PAD = PART[:-1] + "（長い目で見た位置）" * 4 + "。"
good = [PAD] * 4
data = {"view": "x", "long_view": lv(good)}
ok(d.check_long_view_parts(data, SRC3) == [] and len(data["long_view"]["parts"]) == 4, "4つの型がすべて通る")
bad = list(good)
bad[1] = PAD + "崩れの側が多い。"
data = {"view": "x", "long_view": lv(bad)}
errs = d.check_long_view_parts(data, SRC3)
ok(len(errs) == 1 and "world" in errs[0] and [p["k"] for p in data["long_view"]["parts"]] == ["history", "why", "decade"],
   "文が4つの型（world）だけ外し、残り3つは出す")
bad = list(good)
bad[3] = PAD.replace("61回", "61回・184回・-1.352%")
data = {"view": "x", "long_view": lv(bad)}
errs = d.check_long_view_parts(data, SRC3)
ok(len(errs) == 1 and "decade" in errs[0] and "数字" in errs[0], "数字が5つ以上の型（decade）だけ外す")
p0 = lv(good); p0["parts"][0].pop("h")
data = {"view": "x", "long_view": p0}
d.check_long_view_parts(data, SRC3)
ok(data["long_view"]["parts"][0]["h"] == "歴史の物差し", "小見出しが無い型は型の名前で埋める")
print("all ok")
