# -*- coding: utf-8 -*-
"""引け後の答え合わせ（ai_today_result.py）のテスト。ネットにつながない・数字は作り物
python -X utf8 tests/test_ai_today_result.py

  1. 当たり・外れ・中立（数えない）・騰落0は外れ（ai_record.py と同じ判定）
  2. 書かない日: 今日の朝刊が無い・今日の終値がまだ無い
  3. 今月・通算・連勝/連敗（ai_record.json にある日は二重に数えない・無い日は足す）
  4. 週の最後の営業日だけ、今週の問いを今日の終値で判定する
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_today_result as T  # noqa: E402
import ai_analysis  # noqa: E402

NG = 0
JST = timezone(timedelta(hours=9))


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


NOW = datetime(2026, 11, 17, 16, 2, tzinfo=JST)
CL = [("2026-11-10", 100.0), ("2026-11-11", 101.0), ("2026-11-12", 100.3), ("2026-11-13", 100.8),
      ("2026-11-16", 98.4), ("2026-11-17", 97.1)]
DOC = {"latest": {"date": "2026-11-17", "stance": "lean_defense", "machine_stance": "lean_defense",
                  "headline": "見出し", "issue": 28},
       "history": [{"date": "2026-11-16", "stance": "lean_defense"}, {"date": "2026-11-13", "stance": "lean_defense"},
                   {"date": "2026-11-12", "stance": "neutral"}, {"date": "2026-11-11", "stance": "attack"}]}
REC = {"ai": {"rows": [{"d": "2026-10-30", "s": "attack", "ret": 1.0}, {"d": "2026-11-11", "s": "attack", "ret": 1.0},
                       {"d": "2026-11-12", "s": "neutral", "ret": -0.69}, {"d": "2026-11-13", "s": "lean_defense", "ret": 0.5}],
              "stats": {"attack": {"hit": 5, "judged": 8}, "lean_defense": {"hit": 2, "judged": 4},
                        "neutral": {"hit": 0, "judged": 0}}}}

# ── 1. 判定は ai_record / ai_analysis と同じ ──
for s in ("attack", "lean_attack", "neutral", "lean_defense", "defense"):
    for r in (1.2, -0.4, 0.0):
        ok(T.hit_of(s, r) == ai_analysis._hit(s, r), f"判定が朝刊のスコアボードと同じ {s} {r}")
d, why = T.build(DOC, REC, CL, NOW)
ok(why is None and d["date"] == "2026-11-17", f"書ける {why}")
ok(d["n225_ret"] == round((97.1 / 98.4 - 1) * 100, 2) and d["hit"] is True and d["result_jp"] == "当たり", "守りで下げ → 当たり")
ok(d["machine_hit"] is True and d["stance_jp"] == "やや守り" and d["issue"] == 28, "機械の構えの当たり・号数")
d2, _ = T.build({"latest": dict(DOC["latest"], stance="attack")}, REC, CL, NOW)
ok(d2["hit"] is False and d2["result_jp"] == "外れ", "強気で下げ → 外れ")
d3, _ = T.build({"latest": dict(DOC["latest"], stance="neutral")}, REC, CL, NOW)
ok(d3["hit"] is None and "数えない" in d3["result_jp"], "中立は数えない")
flat = CL[:-1] + [("2026-11-17", 98.4)]
ok(T.build({"latest": dict(DOC["latest"], stance="attack")}, REC, flat, NOW)[0]["hit"] is False, "騰落0は外れ")

# ── 2. 書かない日 ──
ok(T.build({"latest": dict(DOC["latest"], date="2026-11-16")}, REC, CL, NOW)[0] is None, "今日の朝刊が無い日は書かない")
ok(T.build(DOC, REC, CL[:-1], NOW)[0] is None, "今日の終値がまだ無い日は書かない")
ok(T.build(DOC, REC, [], NOW)[0] is None, "終値が取れない日は書かない")

# ── 3. 今月・通算・連勝 ──
# 11月: 11/11 attack +1.0 当たり・11/12 中立・11/13 やや守り +0.50 外れ・11/16 やや守り −2.38 当たり・11/17 当たり
ok(d["month"] == {"month": "2026-11", "win": 3, "lose": 1, "neutral": 1}, f"今月 {d['month']}")
# 通算 = ai_record の集計（7勝5敗）＋ ai_record に無い 11/16・11/17（2勝）
ok(d["total"] == {"win": 9, "lose": 5}, f"通算 {d['total']}")
ok(d["streak"] == 2 and d["streak_jp"] == "2連勝", f"連勝 {d['streak']}")
rec2 = {"ai": {"rows": REC["ai"]["rows"] + [{"d": "2026-11-16", "s": "lean_defense", "ret": -2.38},
                                            {"d": "2026-11-17", "s": "lean_defense", "ret": -1.32}],
               "stats": {"attack": {"hit": 5, "judged": 8}, "lean_defense": {"hit": 4, "judged": 6}}}}
ok(T.build(DOC, rec2, CL, NOW)[0]["total"] == {"win": 9, "lose": 5}, "ai_record にある日は二重に数えない")

# ── 4. 今週の問い（金曜だけ） ──
wq = {"id": "hold", "q": "今週、日経平均は100円を保てるか", "kind": "level", "level": 100.0, "rule": "判定日の終値が 100円 を上回るか"}
FRI = datetime(2026, 11, 20, 16, 2, tzinfo=JST)
clf = CL + [("2026-11-18", 99.0), ("2026-11-19", 99.5), ("2026-11-20", 100.6)]
docf = {"latest": dict(DOC["latest"], date="2026-11-20"),
        "history": [{"date": "2026-11-16", "stance": "neutral", "week_question": wq}]}
f, _ = T.build(docf, REC, clf, FRI)
ok(f["week_question"]["hit"] is True and f["week_question"]["asof"] == "2026-11-20" and f["week_question"]["set_on"] == "2026-11-16",
   f"金曜は今日の終値で今週の問いを判定 {f.get('week_question')}")
ok("week_question" not in d, "金曜以外は今週の問いを出さない")
wq_ma = dict(wq, kind="ma", n=3, level=None)
docm = dict(docf, history=[{"date": "2026-11-16", "week_question": wq_ma}])
fm = T.build(docm, REC, clf, FRI)[0]["week_question"]
ok(abs(fm["level"] - round((99.0 + 99.5 + 100.6) / 3, 2)) < 1e-9 and fm["hit"] is True, f"移動平均の問い {fm}")
ok("week_question" not in T.build(dict(docf, history=[{"date": "2026-11-13", "week_question": wq}]), REC, clf, FRI)[0],
   "先週の問いは使わない")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
