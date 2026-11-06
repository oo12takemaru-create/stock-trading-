# -*- coding: utf-8 -*-
"""qa_material の検査（アーサーの質問箱）。ネットにつながずに動く。python -X utf8 tests/test_qa_material.py"""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import qa_material as q  # noqa: E402

SRC = """## 問い sox: 半導体指数が1日で10%近く下げた。ここからどう転びうるか？
  20営業日後の日経平均: この条件 16回・中央値-3.70%・上がった割合25.0% ／ 条件なしの全期間 7953回・中央値+0.65%
  SOX指数（半導体）: -12.25 / -21.77 / -28.62 / +27.22
## 今週（11/02〜11/06）のニュースの見出し（AI朝刊の入力・[日付 媒体] 見出し。本文は無い）
[11/06 NHK 経済] オープンAI 開発一時中断 AIみずからサイバー攻撃実行のおそれ
[11/06 Yahoo 国際] バンス氏 米大統領と距離置く姿勢
道の条件（来週の金曜終値→翌週の金曜終値の騰落で、翌週の土曜に機械が判定する）に使える指標と幅:
  幅 v（符号つき）: 1, 2, 3, 5, 7, 10, 15, 20%（米10年金利だけ 0.1, 0.2, 0.3%pt）"""
META = {"q": "x", "n": 17, "low": 16, "src": "SOX", "stat": "sox_drop"}
OK = {"id": "sox", "q": "半導体の急落は、ここからどう転びうるか？", "a": [
    "今週の半導体の急落は、AIへの期待で膨らんだ相場が一気に縮んだ動きです。",
    "オープンAIの開発一時中断の報道（11/06 NHK）と同じ週に、SOX指数は今週-21.77%下げ、AIの値札が一斉に見直されました。",
    "要は、市場が「AIの夢に先回りしすぎた」と気づき、いったん距離を置き始めた形です。",
    "下げが行き過ぎだったなら半導体から戻りやすく、AIへの疑いが広がるなら日経平均の重さが続きやすい道で、"
    "回数が少ないものの過去16回では20営業日後の日経平均が上がった割合は25.0%と、後者が重く見えます。",
    "次に見るのは、来週のSOX指数が5%以上戻せるかどうかです。"],
    "paths": [{"name": "戻りの道", "t": "SOX", "op": ">=", "v": 5}, {"name": "重さが続く道", "t": "SOX", "op": "<=", "v": -5}]}


def ok(cond, msg):
    if not cond:
        print("NG:", msg)
        sys.exit(1)
    print("ok:", msg)


def var(i, old, new, base=OK):
    x = copy.deepcopy(base)
    x["a"][i] = x["a"][i].replace(old, new)
    assert x["a"][i] != base["a"][i], (old, new)
    return x


def err(it):
    return q.validate_item(it, META, SRC)


ok(err(OK) is None, f"見本は通る（{err(OK)}）")
ok("回数が少ない" in (err(var(3, "回数が少ないものの", "")) or ""), "回数30未満で「回数が少ない」が無いと落ちる")
ok("入力に無い数字" in (err(var(3, "25.0%", "23.4%")) or ""), "入力に無い数字は落ちる")
ok("意図" in (err(var(1, "AIの値札", "大手の思惑どおりAIの値札")) or ""), "「思惑」は落ちる")
ok("言い切り" in (err(var(2, "形です。", "形で、来週は下がるだろう。")) or ""), "「〜だろう」は落ちる")
ok("言い切り" in (err(var(2, "形です。", "形で、来週は下がる。")) or ""), "文末の「下がる」は落ちる")
ok("禁止語" in (err(var(2, "距離を置き始めた", "売りを急いだ")) or ""), "「売り」は落ちる")
ok(err(var(2, "要は、", "要は、バンス氏の発言とは別に、")) is None, "見出しにある人物の呼び方は通る")
ok("人を指す" in (err(var(2, "要は、", "要は、山田氏の言うとおり、")) or ""), "見出しに無い「〜氏」は落ちる")
ok("2つの道" in (err(var(3, "AIへの疑いが広がるなら", "AIへの疑いが広がると")) or ""), "「なら」が1つだけなら落ちる")
x = copy.deepcopy(OK)
x["paths"][1]["op"] = ">="
ok(err(x) is not None, "2つの道が同じ向きなら落ちる")
x = copy.deepcopy(OK)
x["paths"][0]["v"] = 4
ok("決まった幅" in (err(x) or ""), "決まっていない幅は落ちる")
x = copy.deepcopy(OK)
x["a"].append("念のため、もう1文を足します。")
ok(err(x) is not None, "6文は落ちる")
ok(err(var(1, "今週-21.77%", "今週-21.77%（1日で-12.25%）")) is not None, "数字が7個なら落ちる")
ok(q.validate_item(OK, None, SRC) is not None, "材料に無い問いは落ちる")

data = {"qa": {"items": [OK, var(3, "回数が少ないものの", "")]}}
errs = q.check_qa(data, {"items": {"sox": META}, "vol": None}, SRC)
ok(len(data["qa"]["items"]) == 1 and len(errs) == 1, "同じ id の2問目は外れ、1問目だけ残る")
ok(data["qa"]["items"][0]["paths"][0]["cond_jp"] == "SOX指数が来週+5%以上", "道の条件の文を機械が足す")
data = {"qa": {"items": [var(3, "回数が少ないものの", "")]}}
q.check_qa(data, {"items": {"sox": META}}, SRC)
ok("qa" not in data, "全部落ちたら qa ごと外す")
ok([q.election_day(int(d[:4])).isoformat() for d in q.MIDTERMS] == q.MIDTERMS and len(q.MIDTERMS) == 19,
   "中間選挙の投票日の表は「11月の第1月曜の翌日の火曜」と一致・19回")

from datetime import date  # noqa: E402
SUN = date(2026, 11, 8)
ok(q.news_ok("NYダウ急落 一時1800ドル超安", SUN), "経済の見出しは残る")
ok(not q.news_ok("イチローさん マリナーズOBのホームラン競争", SUN), "スポーツは除く")
ok(not q.news_ok("台湾の九份 日本人男性が行方不明", SUN), "事件事故は除く（国名があっても）")
ok(q.news_ok("露軍がウの学校に空爆 26人死亡", SUN), "戦争・紛争の見出しは残る")
ok(not q.news_ok("【詳細】ロシア ウクライナに軍事侵攻（8月8日の動き）", SUN), "日曜の7日前より古い日付の見出しは除く")
ok(not q.news_ok("香川の讃岐うどん店 混雑対応苦慮", SUN), "経済・金融・政治・国際の語が無い見出しは除く")
days = {"2026-11-02": "## 直近ニュース見出し\n[A 経済] 日銀 会合へ\n[B 経済] 円安 進む\n## x",
        "2026-11-03": "## 直近ニュース見出し\n[A 経済] 日銀 会合へ\n[B 経済] 株価 上昇\n## x",
        "2026-11-04": "## 直近ニュース見出し\n[A 経済] 日銀 会合へ\n[B 経済] 金利 低下\n## x"}
L = q.week_news(date(2026, 11, 2), date(2026, 11, 6), fetch=lambda d: days.get(d, ""), sunday=SUN)
ok(L == ["[11/04 B 経済] 金利 低下", "[11/03 B 経済] 株価 上昇", "[11/02 B 経済] 円安 進む"],
   "毎日同じ並びの媒体（更新が止まった RSS）は除き、新しい順")
print("all ok")
