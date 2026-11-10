# -*- coding: utf-8 -*-
"""質問箱 Vol.2〜（定番の手じまいの問い＋掲示板2問）と、土曜の答え合わせの検査。ネットにつながずに動く。
python -X utf8 tests/test_qa_teiban.py"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import qa_material as qm  # noqa: E402
import qa_teiban as qt  # noqa: E402


def ok(cond, msg):
    if not cond:
        print("NG:", msg)
        sys.exit(1)
    print("ok:", msg)


SUN = date(2026, 11, 15)

# ---------------------------------------------------------------- 型の選び方
ok(qt.choose_type(SUN, -8.0, True, None)[0] == "a", "初回（11/15）は (a)")
ok(qt.choose_type(SUN + timedelta(7), 0.5, False, "a") == ("b", "週替わり"), "11/22 は形が無ければ週替わりの (b)")
ok(qt.choose_type(SUN + timedelta(14), 0.5, False, "b") == ("c", "週替わり"), "11/29 は (c)")
ok(qt.choose_type(SUN + timedelta(21), 0.5, False, "c") == ("a", "週替わり"), "12/6 は (a) に戻る")
ok(qt.choose_type(SUN + timedelta(7), 0.0, True, "a")[0] == "c", "温度計が今週守りに変わった → (c)")
ok(qt.choose_type(SUN + timedelta(14), -4.2, False, "b")[0] == "a", "日経が今週−3%以下 → (a)")
ok(qt.choose_type(SUN + timedelta(21), 3.5, False, "c")[0] == "b", "日経が今週+3%以上 → (b)")
ok(qt.choose_type(SUN + timedelta(7), -4.2, False, "a") == ("b", "週替わり"), "形に合う型が先週と同じなら週替わり")
ok(qt.choose_type(SUN + timedelta(21), 0.0, False, "a") == ("b", "週替わり"), "週替わりが先週と同じなら次の型")
for t in qt.TYPES:
    q = qt.TYPE_Q[t]
    ok(len(q) <= 30 and q.endswith("？") and "売れ" not in q and "買え" not in q, f"定番の問い ({t}) は30字以内・売れ/買えなし")

items = [{"d": "2026-11-06", "regime": "NEUTRAL"}, {"d": "2026-11-10", "regime": "BEARISH"}]
ok(qt.week_shape(date(2026, 11, 9), date(2026, 11, 13), n225=pd.Series(
    [100.0, 95.0], index=pd.to_datetime(["2026-11-06", "2026-11-13"])), radar_items=items) == (-5.0, True),
   "今週の形: 日経の週の騰落と、温度計が守りに変わったか")

# ---------------------------------------------------------------- 掲示板の問い
NG = ["死ね", "爆上げ確定"]
posts = [   # 新しい順（Supabase の order=created_at.desc と同じ）
    {"id": 9, "body": "#質問箱 7203は今が買い時ですか？", "created_at": "2026-11-14T10:00:00Z", "report_count": 0},
    {"id": 8, "body": "#質問箱 円高が進むと日経平均はどう動きやすいのでしょう", "created_at": "2026-11-13T09:00:00Z",
     "report_count": 0},                                                     # ゲストの投稿（投稿者の列は見ない）
    {"id": 7, "body": "#質問箱 詳しくは https://example.com を見て", "created_at": "2026-11-12T09:00:00Z", "report_count": 0},
    {"id": 6, "body": "#質問箱 VIXが上がった週のあと、相場はどうなってきましたか？", "created_at": "2026-11-12T08:00:00Z",
     "report_count": 1},                                                     # 通報あり
    {"id": 5, "body": "#質問箱 爆上げ確定の銘柄は？", "created_at": "2026-11-11T08:00:00Z", "report_count": 0},
    {"id": 4, "body": "#質問箱 米国の利下げが始まると、日本株にはどう効いてきましたか？", "created_at": "2026-11-10T08:00:00Z",
     "report_count": 0},
    {"id": 3, "body": "#質問箱 2026年の年末までに日経平均は上がりますか？", "created_at": "2026-11-09T08:00:00Z", "report_count": 0},
    {"id": 2, "body": "#質問箱 半導体株が崩れた週のあと、日経はどう動いてきた？", "created_at": "2026-11-08T08:00:00Z",
     "report_count": 0},
]
bq, drop = qt.board_questions(posts, NG, SUN)
ok([x["id"] for x in bq] == ["board8", "board4"], f"新しい順に通った2つ（助言・URL・通報・NG を外す）{drop}")
ok(bq[0]["q"].startswith("円高が進むと") and "#質問箱" not in bq[0]["q"], "#質問箱 の印は外して問いにする")
ok(bq[0]["src"] == "掲示板の #質問箱（11/13の投稿）", "出所は投稿の日付")
ok(qt.post_ok("2026年の年末に向けて、金利と円の関係はどう見ればいいですか", NG) is None, "『2026年』は証券コードと見なさない")
ok(qt.post_ok("6758の決算はどう読めばいいですか", NG) == "証券コード", "4桁のコードは外す")
ok(qt.post_ok("損切りすべきか迷っています、どう考えれば", NG).startswith("投資助言"), "損切りすべき＝助言を求める問いは外す")

qs, note = qt.weekly_questions(SUN, date(2026, 11, 9), date(2026, 11, 13), posts=posts, ng=NG, shape=(-6.0, False), prev=None)
ok([x["id"] for x in qs] == ["exit_a", "board8", "board4"] and note["search_pick"] == 0, "定番1＋掲示板2")
qs, note = qt.weekly_questions(SUN, date(2026, 11, 9), date(2026, 11, 13), posts=posts[:2], ng=NG, shape=(0, False), prev=None)
ok([x["kind"] for x in qs] == ["teiban", "board", "search"] and note["board_pick"] == 1, "掲示板が1つなら、よく検索される問いで1つ埋める")
qs, note = qt.weekly_questions(SUN, date(2026, 11, 9), date(2026, 11, 13), posts=[], ng=NG, shape=(0, False), prev=None)
ok([x["kind"] for x in qs] == ["teiban", "search", "search"] and len({x["id"] for x in qs}) == 3, "掲示板が0なら2つとも埋める")
nx, _ = qt.weekly_questions(SUN + timedelta(7), date(2026, 11, 16), date(2026, 11, 20), posts=[], ng=NG, shape=(0, False), prev="a")
ok({x["id"] for x in nx[1:]}.isdisjoint({x["id"] for x in qs[1:]}), "よく検索される問いは週替わり（翌週は別の2つ）")

# 差し込み口（qa_questions.json）の週は、そちらが優先。11/15 より前の日曜は出さない
ok(qm.questions_for("2026-11-08")[0][0]["id"] == "sox", "11/8 は qa_questions.json の3問（Vol.1）")
ok(qm.questions_for("2026-11-01") == ([], None), "11/15 より前で差し込みが無い日曜は出さない")

# ---------------------------------------------------------------- PROMPT.txt の qa_ctx の行（ingest が同じ問いで検査する）
ctx = {"items": {"exit_a": {"q": qt.TYPE_Q["a"], "n": 79, "low": 55, "src": "日経平均", "stat": "exit_a", "kind": "teiban"}},
       "pick": {"type": "a"}}
line = qm.CTX_MARK + json.dumps(ctx, ensure_ascii=False, separators=(",", ":"))
got = qm.ctx_from_prompt("前の行\n" + line + "\n後の行")
ok(got["items"] == ctx["items"] and got["vol"] is None, "qa_ctx の行を読み戻せる")
ok(qm.ctx_from_prompt("qa_ctx の行が無い") is None, "行が無ければ None（作り直した問いで検査）")

# ---------------------------------------------------------------- 定番 (a) の答えが検査を通る（kind が残る）
SRC = """## 問い exit_a（定番の問い）: 大きく下げた週、どの線で手じまいするか？
  線① 25日線割れ（その週のうちに終値が25日移動平均を下回った最初の日に手じまい）: 線に届いた78回
    手じまいの日から20営業日の日経平均: 77回・中央値-3.42%・プラスだった割合27.3% → 手じまいの方が良かった割合 72.7%
  日経平均: +2.87 / -4.16 / -12.42 / +20.10
## 今週（11/09〜11/13）のニュースの見出し（AI朝刊の入力・[日付 媒体] 見出し。本文は無い）
[11/09 NHK 経済] 米就業者 市場予想に反し減少 利上げ観測後退で円高ドル安進む
道の条件は来週1週間（5営業日）だけで判定する
  幅 v（符号つき）: 1, 2, 3, 5, 7, 10, 15, 20%（米10年金利だけ 0.1, 0.2, 0.3%pt）"""
ANS = {"id": "exit_a", "q": "大きく下げた週、どの線で手じまいするか？", "a": [
    "大きく下げた週の手じまいは、下げの深さより、どの線を先に決めておくかで結果が分かれてきました。",
    "米就業者が予想に反して減ったという報道（11/09 NHK）で円高が進み、日経平均は20日で-12.42%と、損切りの線が試される週が続いています。",
    "要は、線を引かずに持ち続けると、下げの途中で迷う時間がいちばん長くなるということです。",
    "下げが一段落するなら持ち続けた人が戻りを取りやすく、下げが続くなら早めの線で手じまいした人の傷が浅くなりやすい道で、"
    "指数で数えた結果ですが、過去に25日線を割った日に手じまいした方が良かった割合は72.7%でした。",
    "次に見るのは、来週の日経平均が5%以上戻せるかどうかです。"],
    "paths": [{"name": "戻りを取る道", "t": "N225", "op": ">=", "v": 5}, {"name": "下げが続く道", "t": "N225", "op": "<=", "v": -5}]}
data = {"qa": {"items": [json.loads(json.dumps(ANS))]}}
errs = qm.check_qa(data, {"items": ctx["items"], "vol": None}, SRC)
ok(not errs and data["qa"]["items"][0]["kind"] == "teiban", f"定番の答えが通り、kind=teiban が残る {errs}")
bad = json.loads(json.dumps(ANS))
bad["a"][3] = bad["a"][3].replace("手じまいした人の傷が浅くなりやすい", "早めに売りに回る")
data = {"qa": {"items": [bad]}}
errs = qm.check_qa(data, {"items": ctx["items"], "vol": None}, SRC)
ok(errs and "qa" not in data, f"「売り」を書いた定番の答えは落ちる {errs}")

# ---------------------------------------------------------------- 統計（作った値動きで）
idx = pd.bdate_range("2000-01-03", periods=400)
v = [100.0] * 400
for i in range(200, 205):          # 1週間で −12%
    v[i] = 100.0 * (1 - 0.025 * (i - 199))
for i in range(205, 400):
    v[i] = v[204] + 0.8 * (i - 204)     # その後は戻る
c = pd.Series(v, index=idx)
s = qt.stat_exit_a(c)
ok(s["n"] == 1 and s["line"]["ma25"]["hit"] == 1 and s["line"]["cut8"]["hit"] == 1, "(a) 下げの週が1回・2つの線に届く")
e = s["events"][0]
ok(e["lines"]["ma25"]["exit"] == -2.5 and e["lines"]["cut8"]["exit"] == -10.0, "(a) 25日線割れは初日・−8%は4日目（−10%）")
ok(s["line"]["ma25"]["exit_better"] == 0.0, "(a) その後に戻った回は「手じまいの方が良かった」に入らない")

up = [100.0 * (1 + 0.002 * i) for i in range(300)]
for i in range(150, 160):
    up[i] = up[149] * (1 - 0.012 * (i - 149))
for i in range(160, 300):
    up[i] = up[159]
idx2 = pd.bdate_range("2001-01-01", periods=300)
s = qt.stat_exit_b(pd.Series(up, index=idx2))
ok(s["n"] >= 1 and all(x["kessan"][5:7] in ("02", "05", "08", "11") for x in s["events"]), "(b) 決算の目安は2・5・8・11月")
ok(qt.next_kessan(date(2026, 11, 15)) == date(2027, 2, 15) and qt.next_kessan(date(2026, 11, 14)) == date(2026, 11, 15),
   "(b) 次の決算の目安の日")

days = pd.bdate_range("2010-01-04", periods=60)
n = pd.Series([100.0 + i for i in range(60)], index=days)
reg = {d: ("BEARISH" if 20 <= k < 25 else "NEUTRAL") for k, d in enumerate(days)}
s = qt.stat_exit_c(n225=n, reg=reg)
ok(s["switches"] == 1 and s["spell_len"] == 5 and s["spell"]["med"] > 0 and s["spell_exit_better"] == 0.0,
   "(c) 守りの間に上げていれば、手じまいの方が良かったに入らない")
ok(s["cagr_rule"] < s["cagr_hold"], "(c) 上げ続ける相場では、守りの日を持たない方が年率は低い")

# ---------------------------------------------------------------- 11/14（土）の Vol.1 の答え合わせ（judge_prev）
weekend = json.loads((ROOT / "docs" / "ai_weekly.json").read_text(encoding="utf-8")).get("weekend") or {}
if weekend.get("date") == "2026-11-08" and weekend.get("qa"):
    px = {"^SOX": (100.0, 106.0), "^GSPC": (100.0, 99.0), "^N225": (100.0, 94.0)}

    def fake(t, start):
        a, b = px[t]
        return pd.Series([a, a * 1.01, b], index=pd.to_datetime(["2026-11-06", "2026-11-10", "2026-11-13"]))
    real = qm.closes
    qm.closes = fake
    try:
        qc = qm.judge_prev(weekend, date(2026, 11, 13))
    finally:
        qm.closes = real
    v = {x["id"]: x["verdict"] for x in qc["items"]}
    ok(qc["vol"] == 1 and qc["week"]["to"] == "2026-11-13", "Vol.1 の答え合わせは 11/9〜11/13 の週")
    ok(v == {"sox": "「半導体の反発が続く」の条件に届いた", "payroll": "どちらの道の条件にも届かなかった（間の動き）",
             "midterm": "「回復の型から外れる」の条件に届いた"}, f"3問の判定 {v}")
    ok(qm.judge_prev(weekend, date(2026, 11, 20)) is None, "別の週の金曜では答え合わせしない")

    def stale(t, start):    # 金曜の終値がまだ無い（火曜までしか取れていない）
        return pd.Series([100.0, 120.0], index=pd.to_datetime(["2026-11-06", "2026-11-10"]))
    qm.closes = stale
    try:
        qc = qm.judge_prev(weekend, date(2026, 11, 13))
    finally:
        qm.closes = real
    ok(all(x["verdict"].startswith("判定できず") for x in qc["items"]), "週の途中の値では判定しない（判定できず）")
else:
    print("skip: docs/ai_weekly.json の weekend が Vol.1（2026-11-08）でない")

print("all ok")
