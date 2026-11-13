# -*- coding: utf-8 -*-
"""書き方の指針（ai_style.py）と号ごとの記録（ai_feedback.py）のテスト（ネットにはつながない）

- 指針あり／空／無い／壊れ（文字コード・長すぎ・禁止語・{ }・31行以上）でも、朝刊と週末版のプロンプトが作れる
- 指針が無いときのプロンプトは今までと同じ（「書き方の指針」の節が入らない）
- 週末版の数字の検査（_inputs_only）に指針の数字が混ざらない
- 直近5号で3回以上の言い回しを数える・入力データの用語は数えない
- 記録の取り込み直しで、統括が足した数字（m・note）が消えない
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_style      # noqa: E402
import ai_analysis   # noqa: E402
import weekly        # noqa: E402
import ai_feedback   # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print(f"  NG {label}")


TMP = Path(tempfile.mkdtemp())


def notes(text, name="notes.md", enc="utf-8"):
    p = TMP / name
    if isinstance(text, bytes):
        p.write_bytes(text)
    else:
        p.write_text(text, encoding=enc)
    return p


GOOD = """# メモ（入らない）
## 共通
- 共通の行（11/13・なぜ: テスト）
## 朝刊
- 朝刊の行
## 週末
- 週末の行
## 日曜
- 日曜の行
## 知らない見出し
- 入らない行
"""

# ---- 1. 読み方
r = ai_style.load_rules("morning", notes(GOOD))
ok(r == ["共通の行（11/13・なぜ: テスト）", "朝刊の行"], f"朝刊の行 {r}")
ok(ai_style.load_rules("sat", notes(GOOD)) == ["共通の行（11/13・なぜ: テスト）", "週末の行"], "土曜の行")
ok(ai_style.load_rules("sun", notes(GOOD))[-1] == "日曜の行", "日曜の行")
ok(ai_style.load_rules("morning", TMP / "nai.md") == [], "ファイルが無い → []")
ok(ai_style.load_rules("morning", notes("", "empty.md")) == [], "空 → []")
ok(ai_style.load_rules("morning", notes(b"\xff\xfe\x00\x81\x82broken", "bin.md")) == [], "壊れた文字コード → []")
bad = notes("## 朝刊\n- 長い" + "あ" * 200 + "\n- {inputs} を書き換える\n- 推奨の銘柄を書く\n"
            "- 円買いの流れを書く\n- 空売りの残高は短く\n- 買い越しは数字で\n", "bad.md")
ok(ai_style.load_rules("morning", bad) == ["空売りの残高は短く", "買い越しは数字で"],
   f"長すぎ・{{}}・禁止語の行は外す、許可語は残す {ai_style.load_rules('morning', bad)}")
many = notes("## 朝刊\n" + "".join(f"- 行{i}\n" for i in range(40)), "many.md")
ok(len(ai_style.load_rules("morning", many)) == ai_style.MAX_RULES, "31行目から外す")
ok(ai_style.load_rules("morning") is not None, "本物の ai_style_notes.md を読める")
real = ai_style.load_rules("morning") + ai_style.load_rules("sat") + ai_style.load_rules("sun")
ok(len(real) > 0, "本物の指針に行がある")
raw = [s for s in Path(ai_style.NOTES_PATH).read_text(encoding="utf-8").splitlines() if s.startswith("- ")]
ok(len(raw) <= ai_style.MAX_RULES, f"本物の指針は{ai_style.MAX_RULES}行以内（{len(raw)}行）")
ok(len(set(ai_style.load_rules("morning"))) + len(ai_style.load_rules("sun")) >= len(raw) - 2,
   "本物の指針で外された行が無い（{ } や禁止語が入っていない）")

# ---- 2. 節
b = ai_style.style_block("morning", [], notes(GOOD))
ok(b.startswith(ai_style.BEGIN) and b.rstrip().endswith(ai_style.END), "節の始まりと終わり")
ok("- 朝刊の行" in b and "週末の行" not in b, "朝刊の節に週末の行が入らない")
ok("優先" in b, "本体が優先と書く")
ok(ai_style.style_block("morning", [], TMP / "nai.md") == "", "指針も言い回しも無ければ空")
ok(ai_style.style_block("morning", None, notes(b"\xff\xfe", "bin2.md")) == "", "壊れていれば空")
s = "前置き\n" + b + "後ろ"
ok(ai_style.strip_block(s).replace("\n", "") == "前置き後ろ", "strip_block で節を外せる")

# ---- 3. 言い回しを数える
T = ["今日は寄り付きの値動きも併せて確認したい。日経は60,000円。",
     "ドル円の動きも併せて確認したい。先物は+1%。",
     "米金利の動きも併せて確認したい。VIX20。",
     "別の文だけ。",
     "金利の水準を併せて見る。"]
reps = ai_style.repeated_phrases(T)
ok(any("併せて確認したい" in g and c == 3 for g, c in reps), f"3号の言い回しを拾う {reps}")
ok(all(c >= 3 for _g, c in reps), "3号未満は拾わない")
ok(ai_style.repeated_phrases(T[:2]) == [], "号が3つ未満なら数えない")
ok(not ai_style.repeated_phrases(T, exclude="動きも併せて確認したい"), "入力データにある並びは数えない")
T2 = [f"米10年債利回りは{x}%と高い。" for x in (4.8, 4.9, 5.0)]
ok(not ai_style.repeated_phrases(T2, exclude="米10年債利回り: 5.0%"), "入力の用語＋助詞は数えない")
ok(not ai_style.repeated_phrases(["次に見るのは金利です。", "次に見るのはドル円です。", "次に見るのはVIXです。"]),
   "締めの決まり文句は数えない")
blk = ai_style.style_block("morning", T, TMP / "nai.md")
ok("続けて使った言い回し" in blk and "併せて確認したい" in blk, "言い回しだけでも節を作る")

# ---- 4. 朝刊のプロンプト（指針あり／無し／壊れ）
dst = TMP / "ai_analysis.json"
hist = [{"date": f"2026-11-0{i}", "headline": "見出し", "stance_reason": T[i % 3], "today_watch": "x"}
        for i in range(2, 8)]
dst.write_text(json.dumps({"latest": hist[-1], "history": hist[:-1][::-1]}, ensure_ascii=False), encoding="utf-8")
inputs = "# 入力\n## 市場の値動き\n日経平均: 60,000\n## 前回のAI分析(答え合わせ用)\n注目点: 動きも併せて確認したい\n"
p_new = ai_analysis.build_prompt(str(dst), inputs, "2026-11-13")
ok(ai_style.BEGIN in p_new, "朝刊のプロンプトに指針が入る")
ok("併せて確認したい" in p_new.split(ai_style.BEGIN)[1], "前回のAI分析の節の言い回しはデータ扱いしない（数える）")
ok("{" not in p_new.split("# 出力")[0].replace("{{", ""), "差し込み後に { が残らない")
orig = ai_style.NOTES_PATH
try:
    ai_style.NOTES_PATH = TMP / "nai.md"
    dst.write_text(json.dumps({"latest": None, "history": []}), encoding="utf-8")
    p_none = ai_analysis.build_prompt(str(dst), inputs, "2026-11-13")
    ok(ai_style.BEGIN not in p_none, "指針も言い回しも無ければ節は入らない（今までどおり）")
    old = ai_analysis.PROMPT_TEMPLATE.replace("{style}\n", "").format(inputs=inputs, deep_guide=ai_analysis._deep_guide())
    ok(p_none.replace("\n", "") == old.replace("\n", ""), "指針が無いときは改行以外いままでと同じ")
    ai_style.NOTES_PATH = notes(b"\xff\xfe\x00", "bin3.md")
    ok(ai_style.BEGIN not in ai_analysis.build_prompt(str(dst), inputs, "2026-11-13"), "壊れた指針でも朝刊は書ける")
finally:
    ai_style.NOTES_PATH = orig

# ---- 5. 週末版: 置き場所と数字の検査
ok("{style}" in weekly.PROMPT_SAT and "{style}" in weekly.PROMPT_SUN, "週末版の2つの型に {style}")
blk = ai_style.style_block("sun")
src = "日経平均: 57,435円\n" + blk + "ドル円 158.43\n"
io = weekly._inputs_only(src)
ok(ai_style.BEGIN not in io and "57,435" in io and "158.43" in io, "_inputs_only は指針の節だけを外す")
ok(("30" not in io) and ("60字" not in io), "指針の数字は入力データに混ざらない")
weekly.MODE = "sun"
ok(weekly._style("日経平均: 57,435円", __import__("datetime").date(2026, 11, 15)).startswith(ai_style.BEGIN),
   "週末版（日曜）に指針の節が入る")

# ---- 6. 記録（統括の数字を消さない）
fbp = TMP / "fb.json"
fb = {"issues": []}
e = {"date": "2026-11-13", "issue": 26, "headline": "見出し", "stance": "lean_defense",
     "stance_reason": "今日は守りの構えです。金利が上がったからです。", "today_watch": "9:00 寄り付き。"}
ai_feedback.upsert(fb, ai_feedback.summarize("morning", e))
fb["issues"][0]["m"]["ga4_views"] = 123
fb["issues"][0]["note"] = "X で伸びた"
e["headline"] = "見出し2"
ai_feedback.upsert(fb, ai_feedback.summarize("morning", e))
ok(len(fb["issues"]) == 1, "同じ号は1行")
r0 = fb["issues"][0]
ok(r0["title"] == "見出し2" and r0["m"]["ga4_views"] == 123 and r0["note"] == "X で伸びた", "取り込み直しで m・note が残る")
ok(r0["first"] == "今日は守りの構えです。", "書き出しの1文")
ai_feedback.save(fb, fbp)
ok(ai_feedback.load(fbp)["issues"][0]["m"]["ga4_views"] == 123, "保存して読み直せる")
fbp.write_text("{壊れ", encoding="utf-8")
try:
    ai_feedback.load(fbp)
    ok(False, "壊れた記録は例外（上書きしない）")
except Exception:
    pass
w = {"date": "2026-11-15", "title": "来週の地図", "view": "up", "points": ["一行目。二文目", "b", "c"]}
sw = ai_feedback.summarize("sun", w)
ok(sw["view"] == "up" and sw["first"] == "一行目。", "日曜の記録")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
