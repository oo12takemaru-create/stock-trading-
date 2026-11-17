# -*- coding: utf-8 -*-
"""AI朝刊の新しい欄（ai_morning_extra）のテスト。ネットにつながない・数字は作り物
python -X utf8 tests/test_ai_morning_extra.py

  1. いまの場面: 取れた日・取れない日・古い日・当てはまる場面が無い日
  2. 温度計の ℃: 今日・前日・1週間前・判定の変化・古い日
  3. 朝刊の成績: 夕方の答え合わせで前回の号を補う・今月と通算・連勝/連敗
  4. 読者の1タップ: 無い日は入れない・ある日は割合
  5. 今週の問い: 月曜=候補・水曜=途中経過・金曜=判定・月曜が祝日の週・候補の無い週
  6. 検査: 新しい欄のあり/なし・材料の無い日の欄・入力に無い数字・禁止語・字数・JPX の数字・同じ一言・判定の食い違い
  7. 朝刊の本体と合わせた検査（validate を通る・落ちた欄だけ外れる）とプロンプトの形
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_morning_extra as X  # noqa: E402

NG = 0
TMP = Path(tempfile.mkdtemp())


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


TUE = date(2026, 11, 17)
BAMEN = {
    "date": "2026-11-16", "close": 59662.02, "chg": -2.42, "from_52w_high": -17.56,
    "today": [{"slug": "dd10", "name": "日経平均が52週高値から10%下げたとき", "count": 35, "clusters": 35,
               "since": "1965-01-05", "last_hit": "2026-07-17", "mark": "高値から -11.4%",
               "after": [{"h": 20, "label": "20営業日後", "n": 35, "up": 18, "down": 17, "median": 0.34},
                         {"h": 250, "label": "250営業日後", "n": 33, "up": 20, "down": 13, "median": 9.7}],
               "few": False}],
}

# ── 1. いまの場面 ──
sl = X.scene_lines(TUE, doc=BAMEN)
ok(sl and sl[0] == X.H_SCENE, "場面の節の見出し")
txt = "\n".join(sl)
ok("1965年以降35回" in txt and "20営業日後に上がっていたのは35回中18回" in txt and "33回中20回" in txt, f"回数とその後 {txt}")
ok("11/16 の終値 59,662.02円" in txt, "いつの終値か")


def boom(url):
    raise OSError("network down")


ok(X.scene_lines(TUE, fetch=boom) == [], "取れない日は入れない")
ok(X.scene_lines(TUE, doc=dict(BAMEN, date="2026-11-01")) == [], "古い日は入れない")
ok(X.scene_lines(TUE, doc=dict(BAMEN, date="2026-11-17")) == [], "今日の日付（先の日）は入れない")
ok(X.scene_lines(TUE, doc=dict(BAMEN, today=[])) == [], "当てはまる場面が無い日は入れない")
ok(X.scene_lines(TUE, doc="壊れた") == [], "壊れた JSON は入れない")

# ── 2. 温度計 ──
TH = {"trade_date": "2026-11-16", "value": 17, "regime_jp": "弱気", "regime_changed": False,
      "compare": {"yesterday": {"d": "2026-11-13", "value": 17, "regime_jp": "弱気"},
                  "week": {"d": "2026-11-09", "value": 20, "regime_jp": "弱気"}}}
tl = "\n".join(X.thermo_lines(TUE, doc=TH))
ok("−6℃（弱気）" in tl and "前日（11/13） −6℃" in tl and "1週間前（11/9） −4℃" in tl, f"温度の ℃ {tl}")
ok("変わっていない" in tl, "判定が変わっていない日")
tl2 = "\n".join(X.thermo_lines(TUE, doc=dict(TH, value=45, regime_jp="中立", regime_changed=True)))
ok("16℃（中立）" in tl2 and "弱気から中立に変わった" in tl2, "判定が変わった日")
ok(X.thermo_lines(TUE, doc=dict(TH, celsius={"value": -6})) and X.celsius(0) == -20 and X.celsius(100) == 60, "℃ の式")
ok(X.thermo_lines(TUE, doc=dict(TH, trade_date="2026-11-01")) == [], "古い温度計は入れない")
ok(X.thermo_lines(TUE, doc={}) == [], "温度計が無い日は入れない")

# ── 3. 朝刊の成績 ──
REC = {"ai": {"rows": [{"d": "2026-10-30", "s": "attack", "ret": 1.0},
                       {"d": "2026-11-12", "s": "neutral", "ret": -0.7},
                       {"d": "2026-11-13", "s": "lean_defense", "ret": 0.53}],
              "stats": {"attack": {"hit": 10, "judged": 15}, "lean_defense": {"hit": 3, "judged": 6},
                        "neutral": {"hit": 0, "judged": 0}}}}
RES = {"date": "2026-11-16", "stance": "lean_defense", "n225_ret": -2.42}
rl = "\n".join(X.record_lines(TUE, prev={"date": "2026-11-16"}, rec=REC, result=RES))
ok("前回（11/16）の構え: やや守り → その日の日経平均 -2.42% → 当たり" in rl, f"夕方の答え合わせで前回を補う {rl}")
ok("今月（11月）: 1勝1敗" in rl, "今月は中立を数えない")
ok("通算: 14勝8敗" in rl, "通算 = ai_record の集計 + 新しい日")
ok("1連勝" in rl, "連勝")
rl2 = "\n".join(X.record_lines(TUE, prev={"date": "2026-11-16"}, rec=REC, result={}))
ok("まだ採点されていない" in rl2 and "1連敗" in rl2, f"採点が無い前回は数えない {rl2}")
dup = "\n".join(X.record_lines(TUE, prev={"date": "2026-11-13"}, rec=REC, result=dict(RES, date="2026-11-13", n225_ret=-1.0)))
ok("0.53%" in dup or "+0.53%" in dup, "ai_record にある日は ai_record の値を使う（夕方の値で二重に数えない）")
ok(X.record_lines(TUE, prev=None, rec={}, result={}) == [], "採点が1つも無ければ入れない")
ok(X.record_lines(TUE, prev={"date": "2026-11-17"}, rec={"ai": {"rows": [{"d": "2026-11-17", "s": "attack", "ret": 1}]}},
                  result={}) == [], "今日以降の行は使わない")

# ── 4. 読者の1タップ ──
os.environ.pop("AI_VOTE_URL", None)
ok(X.vote_lines(TUE) == [], "置き場所が無い日は入れない")
vl = "\n".join(X.vote_lines(TUE, doc={"date": "2026-11-16", "up": 30, "down": 70}))
ok(X.H_VOTE in vl and "上で終わる 30人（30%）" in vl and "100人" in vl, f"1タップの割合 {vl}")
ok(X.vote_lines(TUE, doc={"date": "2026-11-16", "up": 0, "down": 0}) == [], "0人の日は入れない")
ok(X.vote_lines(TUE, doc={"date": "2026-11-01", "up": 3, "down": 1}) == [], "古い集計は入れない")
os.environ["AI_VOTE_URL"] = "https://example.invalid/vote.json"
ok(X.vote_lines(TUE, fetch=boom) == [], "取れない日は入れない")
os.environ.pop("AI_VOTE_URL", None)


# ── 5. 今週の問い ──
def series_until(end: date, n=260, start=60000.0, step=-20.0):
    out, d, v = [], end, start
    days = []
    while len(days) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d)
    for i, x in enumerate(sorted(days)):
        out.append((x.isoformat(), start + step * (i - n)))
    return out


MON = date(2026, 11, 16)
s_mon = series_until(MON)
wl = X.week_lines(MON, dst=str(TMP / "none.json"), series=s_mon)
ok(wl and wl[0] == X.H_WEEK and any(x.startswith(X.WEEKQ_TAG) for x in wl), "月曜は候補を出す")
cands = X._tag_json("\n".join(wl), X.WEEKQ_TAG)
ok({c["id"] for c in cands} >= {"ma25", "ma200", "week_up", "hold", "reach"}, f"候補の id {cands}")
ok("11/20" in wl[1], "判定日（週の最後の営業日）を書く")
ok(X.week_lines(MON, dst="x", series=s_mon[:10]) == [], "データが足りない週は出さない")
# 月曜が祝日（11/23）の週は火曜に立てる
ok(X.week_lines(date(2026, 11, 23), dst="x", series=s_mon) == [], "祝日の月曜は出さない")
ok(any(x.startswith(X.WEEKQ_TAG) for x in X.week_lines(date(2026, 11, 24), dst="x", series=series_until(date(2026, 11, 24)))),
   "月曜が祝日の週は火曜に立てる")

dst = TMP / "ai_analysis.json"
wq = {"id": "ma25", "q": "今週、日経平均は25日線を回復できるか", "kind": "ma", "n": 25,
      "rule": "判定日の終値が、その日の25日移動平均（終値の単純平均）を上回るか"}
dst.write_text(json.dumps({"latest": {"date": "2026-11-18", "stance": "neutral"},
                           "history": [{"date": "2026-11-17"}, {"date": "2026-11-16", "week_question": wq},
                                       {"date": "2026-11-13", "week_question": dict(wq, id="hold")}]},
                          ensure_ascii=False), encoding="utf-8")
WED = date(2026, 11, 18)
ww = "\n".join(X.week_lines(WED, dst=str(dst), series=series_until(WED)))
ok("今週の問い（11/16に立てた）" in ww and "途中経過" in ww and X.WEEKA_TAG not in ww, f"水曜は途中経過 {ww}")
FRI = date(2026, 11, 20)
s_fri = series_until(FRI)
wf = "\n".join(X.week_lines(FRI, dst=str(dst), series=s_fri))
ja = X._tag_json(wf, X.WEEKA_TAG)
ok(ja and ja["id"] == "ma25" and ja["hit"] is False and ja["asof"] == "2026-11-19", f"金曜は機械の判定（下げ続き→未達） {ja}")
ok("未達" in wf and "11/19 の終値で" in wf, "判定の材料")
up = [(d, 50000 + i * 30.0) for i, (d, _) in enumerate(s_fri)]
ok(X._tag_json("\n".join(X.week_lines(FRI, dst=str(dst), series=up)), X.WEEKA_TAG)["hit"] is True, "上げ続き→達成")
ok(X.week_lines(FRI, dst=str(TMP / "none.json"), series=s_fri) == [], "月曜に問いが立っていない週の金曜は出さない")
ok(X.judge({"kind": "level", "level": 100}, [("2026-11-19", 100.0)])["hit"] is False, "同じ値は上回っていない")

# ── 6. 検査 ──
SRC = "\n".join([
    "# AI朝刊 入力データ",
    "## 市場の値動き", "日経平均: 59,662.02 (前日-2.4% / 5日-3.1%)", "VIX: 23.27 (前日+8.0%)",
    "## 機関投資家・投機筋データ(当サイト独自集計・週次)",
    "[JPX投資部門別売買 11/2〜11/6]", "  海外投資家: ネット-4,321億円 (前週+1,234億円)",
    "", *sl, "", *X.thermo_lines(TUE, doc=TH), "", *X.record_lines(TUE, prev={"date": "2026-11-16"}, rec=REC, result=RES),
])
GOOD = {
    "one_line": "VIXは23台、今日も守りの構えで寄りを見ます",
    "arthur_note": "昨日は当たりましたが、構えが守りの日が続くのは気持ちのいいものではありません。",
    "changes": ["温度は−6℃のまま、判定は弱気で変わらず", "構えはやや守りのまま"],
    "scene": {"title": "52週高値から10%超の下げ",
              "body": "1965年以降35回、20営業日後に上がっていたのは35回中18回。250営業日後は33回中20回でした。"},
}
d = json.loads(json.dumps(GOOD, ensure_ascii=False))
dr = X.check_extras(d, SRC, str(dst), "2026-11-17")
ok(dr == {} and set(d) == set(GOOD), f"よい欄は全部通る {dr}")
ok(X.check_extras({}, SRC) == {}, "新しい欄が無い回答も通る（何もしない）")


def drop(field, value, src=SRC, want=None):
    d = {"one_line": GOOD["one_line"]}
    d[field] = value
    r = X.check_extras(d, src, str(dst), "2026-11-17")
    good = field in r and field not in d and (field == "one_line" or "one_line" in d)
    if want:
        good = good and want in r[field]
    ok(good, f"{field} が外れる（{want}） → {r}")


drop("one_line", "日経は59,662円、VIXは23台で守り", want="数字は1つまで")
drop("one_line", "海外投資家の4,321億円の売り越しが重い", want="JPX")
drop("one_line", "今日は守り、" + "あ" * 40, want="40字")
drop("one_line", "VIXは25台で守り", want="入力に無い数字")
drop("one_line", "守りの構えでもチャンスはある", want="チャンス")
drop("one_line", "今日は押し目買いの日", want="禁止語")
drop("changes", ["a", "b", "c", "d"], want="1〜3行")
drop("changes", ["温度は−47℃に下がった"], want="入力に無い数字")
drop("changes", ["温度は" + "下" * 40], want="40字")
drop("arthur_note", "当たりでもおごらず、" + "い" * 60, want="60字")
drop("scene", {"title": "下げ", "body": "1965年以降35回、上がったのは18回。"}, want="回中")
drop("scene", {"title": "下げ", "body": "1965年以降36回、35回中18回。"}, want="入力に無い数字")
drop("scene", GOOD["scene"], src=SRC.replace(X.H_SCENE, "## 別の節"), want="材料が無い日")
drop("scene", {"title": "下げ", "body": "1965年以降35回。35回中18回。250営業日後は33回中20回。"}, want="2文まで")
drop("scene", "文字だけ", want="dict")
drop("reader_vote", {"body": "読者は上が多い"}, want="材料が無い日")
drop("week_question", {"id": "ma25", "q": "25日線を回復できるか"}, want="候補が入力に無い")
drop("week_answer", {"hit": True, "body": "達成"}, want="判定が入力に無い")
# 同じ一言はくり返さない
dst2 = TMP / "notes.json"
dst2.write_text(json.dumps({"latest": {"date": "2026-11-16", "arthur_note": GOOD["arthur_note"]}, "history": []},
                           ensure_ascii=False), encoding="utf-8")
d = {"arthur_note": GOOD["arthur_note"]}
ok("arthur_note" in X.check_extras(d, SRC, str(dst2), "2026-11-17"), "直近の号と同じ一言は外れる")
d = {"arthur_note": GOOD["arthur_note"]}
ok(X.check_extras(d, SRC, str(dst2), "2026-11-16") == {}, "同じ日の号（取り込み直し）とは比べない")
# 入力データが無いときは数字を確かめられないので外す
d = dict(GOOD)
ok(set(X.check_extras(d, "", str(dst), "2026-11-17")) == set(GOOD) and not any(k in d for k in GOOD), "入力が無いと新しい欄は全部外す")

# 今週の問い（月曜）と答え合わせ（金曜）
SRC_MON = SRC + "\n" + "\n".join(wl)
d = {"week_question": {"id": "ma25", "q": "今週、日経平均は25日線を回復できるか", "level": 1}}
ok(X.check_extras(d, SRC_MON, str(dst), "2026-11-16") == {} and d["week_question"]["kind"] == "ma"
   and d["week_question"]["rule"].startswith("判定日の終値") and "level" not in d["week_question"],
   f"問いは候補の決まりで上書きされる {d}")
drop("week_question", {"id": "nikkei_up", "q": "今週は上がるか"}, src=SRC_MON, want="候補に無い")
drop("week_question", {"id": "ma25", "q": "25日線"}, src=SRC_MON, want="？")
SRC_FRI = SRC + "\n" + wf
d = {"week_answer": {"hit": False, "body": "11/19の終値は25日線の下で、今週の問いは未達でした。"}}
ok(X.check_extras(d, SRC_FRI, str(dst), "2026-11-20") == {} and d["week_answer"]["q"] == wq["q"]
   and d["week_answer"]["asof"] == "2026-11-19", f"答え合わせは機械の判定で上書き {d}")
drop("week_answer", {"hit": True, "body": "達成でした。"}, src=SRC_FRI, want="機械の判定")
# 読者の1タップがある日
SRC_V = SRC + "\n" + vl
d = {"reader_vote": {"body": "読者は下で終わるが70%、実際も下げでした。"}}
ok(X.check_extras(d, SRC_V, str(dst), "2026-11-17") == {}, "1タップの材料がある日は通る")

# JPX の数字の見分け
jn = X.jpx_numbers(SRC)
ok("4321" in jn and "1234" in jn and "59662.02" not in jn, f"JPX の行だけの数字 {sorted(jn)[:8]}")

# ── 7. 朝刊の本体と合わせて ──
import ai_analysis  # noqa: E402

base = {"headline": "VIX23台、寄りは安く始まりやすい", "stance": "lean_defense", "stance_shift": "",
        "stance_reason": "今日はやや守りの構え。", "turn_attack": "a", "turn_defense": "b",
        "today_watch": "9:00", "world_flow": [{"theme": "金利", "body": "x"}],
        "sectors": [{"name": "銀行", "bias": "watch", "match": [], "reason": "x"}], "caution": "x"}
full = dict(base, **json.loads(json.dumps(GOOD, ensure_ascii=False)))
full["one_line"] = "日経は59,662円、VIXは23台"
dr = ai_analysis.check_extras(full, SRC, str(dst), "2026-11-17")
ok(list(dr) == ["one_line"] and "scene" in full and ai_analysis.validate(full) is None, f"落ちた欄だけ外れ、本体は通る {dr}")
bad = dict(base, scene={"title": "7203.T の場面", "body": "35回中18回"})
ai_analysis.check_extras(bad, SRC + "\n7203", str(dst), "2026-11-17")
ok("scene" not in bad and ai_analysis.validate(bad) is None, "証券コードを含む欄は欄だけ外れ、本体は通る")
p = ai_analysis.build_prompt(str(dst), SRC, "2026-11-17")
schema = p.split("# 出力")[1]
ok('"one_line"' in schema and '"week_question"' in schema and "{schema_extra}" not in p and "{extra_guide}" not in p,
   "プロンプトに新しい欄のスキーマ")
ok("# 新しい欄" in p, "プロンプトに新しい欄の書き方")
body = schema[schema.index("{"):schema.rindex("}") + 1]
ok(body.count("{") == body.count("}"), "スキーマの括弧が釣り合う")
for w in X.EXTRA_BANNED + ["推奨", "おすすめ", "必ず", "買い", "売り"]:
    ok(w not in X.EXTRA_GUIDE and w not in X.SCHEMA_EXTRA, f"書き方・スキーマの説明に「{w}」を使わない")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
