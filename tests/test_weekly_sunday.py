# -*- coding: utf-8 -*-
"""日曜版を土曜の朝に書いておき、日曜の朝に公開する（2026-11-15〜）の検査。ネットにつながずに動く。
python -X utf8 tests/test_weekly_sunday.py"""
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import weekly as w  # noqa: E402
from weekly_prep_gate import decide  # noqa: E402

SUN = date(2026, 11, 22)
SAT_ED = {"weekend": "2026-11-21", "date": "2026-11-21"}
WITH_SAT = "# 昨日（土曜）の「今週の振り返り」\nタイトル「x」\n"
NO_SAT = "# 昨日（土曜）の「今週の振り返り」\n" + w.NO_SAT + "\n"


def ok(cond, msg):
    if not cond:
        print("NG:", msg)
        sys.exit(1)
    print("ok:", msg)


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=w.JST)


# ---- 日付の関数が「土曜に日曜の日付で作る」でも日曜に作ったのと同じになる
ok(w.week_range(SUN) == w.week_range(date(2026, 11, 21)), "week_range は土日で同じ金曜")
ok(w.weekend_sat(SUN) == date(2026, 11, 21), "weekend_sat(日曜) は前日の土曜")
ok(w.next_week_days(SUN) == w.next_week_days(date(2026, 11, 21)), "next_week_days は土日で同じ月曜から")
ok(w.issue_of([{"date": "2026-11-15"}, {"date": "2026-11-22"}], "2026-11-22") == 2, "issue_of は日付で数える")

# ---- edition_date: --date があればそれ・無ければ今日
w.TODAY = None
ok(w.edition_date(at("2026-11-21T07:40")) == date(2026, 11, 21), "--date 無しは今日")
w.TODAY = SUN
ok(w.edition_date(at("2026-11-21T07:40")) == SUN, "--date 2026-11-22 なら土曜に動いても日曜の号")
w.TODAY = None

# ---- prep の作る／作らない（weekly_prep_gate.decide）
cases = [
    # (説明, out, answered, existing, now, 作るか)
    ("土曜 7:40 まだ無い → 作る", {}, False, "", "2026-11-21T07:40", True),
    ("土曜 8:00 作成済み・土曜版はまだ main に無い → 作らない", {}, False, NO_SAT, "2026-11-21T08:00", False),
    ("土曜 8:13 土曜版を取り込んだ後 → 土曜版入りで作り直す", {"sat": SAT_ED}, False, NO_SAT, "2026-11-21T08:13", True),
    ("土曜 9:30 土曜版入りでない版でも 9:00 を過ぎたら差し替えない", {"sat": SAT_ED}, False, NO_SAT, "2026-11-21T09:30", False),
    ("土曜 11:07（遅れた schedule）土曜版入りなら作らない", {"sat": SAT_ED}, False, WITH_SAT, "2026-11-21T11:07", False),
    ("回答済み（ai-weekly/<日曜>-sun がある）→ 作らない", {"sat": SAT_ED}, True, NO_SAT, "2026-11-21T08:13", False),
    ("公開済み → 作らない", {"weekend": {"date": "2026-11-22"}}, False, "", "2026-11-22T07:40", False),
    ("日曜 7:40 予備: 土曜に作ってあれば作らない", {"sat": SAT_ED}, False, WITH_SAT, "2026-11-22T07:40", False),
    ("日曜 7:40 予備: 土曜に何も作れていなければ作る", {"sat": SAT_ED}, False, "", "2026-11-22T07:40", True),
    ("日曜 7:40 予備: 土曜版の無い版で回答も無い → 土曜版入りで作り直す", {"sat": SAT_ED}, False, NO_SAT, "2026-11-22T07:40", True),
    ("日曜 15:30 は差し替えない（予備の routine が読む時刻に近い）", {"sat": SAT_ED}, False, NO_SAT, "2026-11-22T15:30", False),
    ("先週の土曜版しか無い → 作り直さない", {"sat": {"weekend": "2026-11-14"}}, False, NO_SAT, "2026-11-21T08:13", False),
]
for msg, out, ans, ex, now, want in cases:
    got, why = decide(SUN, out, ans, ex, at(now))
    ok(got == want, f"{msg}（{why}）")

# ---- ingest の保留（ai-weekly-ingest.yml の gate と同じ比較を bash で）
wf = (ROOT / ".github/workflows/ai-weekly-ingest.yml").read_text(encoding="utf-8")
m = re.search(r'if \[ "\$NOW" \\< "\$D (\d\d:\d\d)" \]', wf)
ok(m and m.group(1) == "07:00", "ingest の保留の時刻は 07:00")
for now, hold in [("2026-11-21 08:15", True), ("2026-11-21 23:59", True), ("2026-11-22 06:59", True),
                  ("2026-11-22 07:00", False), ("2026-11-22 08:00", False), ("2026-11-23 09:00", False)]:
    r = subprocess.run(["bash", "-c", r'NOW="$1"; D="$2"; if [ "$NOW" \< "$D 07:00" ]; then echo hold; else echo go; fi',
                        "_", now, "2026-11-22"], capture_output=True, text=True)
    ok(r.stdout.strip() == ("hold" if hold else "go"), f"日曜版 2026-11-22 を {now} JST に push → {'保留' if hold else '取り込む'}")

# ---- prep の targets（ai-weekly-prep.yml の Decide targets を、date と曜日を差し替えて bash で動かす）
pf = (ROOT / ".github/workflows/ai-weekly-prep.yml").read_text(encoding="utf-8")
blk = pf.split("      - name: Decide targets", 1)[1].split("        run: |\n", 1)[1].split("\n\n", 1)[0]
script = "\n".join(l[10:] for l in blk.splitlines())


def targets(mode, now, inp_date=""):
    fake = f'''date() {{ case "$*" in *%u*) TZ=Asia/Tokyo command date -d "{now}" +%u ;;
      *tomorrow*) TZ=Asia/Tokyo command date -d "{now} +1 day" +%Y-%m-%d ;;
      *) TZ=Asia/Tokyo command date -d "{now}" +%Y-%m-%d ;; esac; }}
GITHUB_OUTPUT=$(mktemp)
'''
    r = subprocess.run(["bash", "-c", fake + script + '\ncat "$GITHUB_OUTPUT"'], capture_output=True, text=True,
                       env={"INPUT_MODE": mode, "INPUT_DATE": inp_date, "FORCE_SUN": "false",
                            "PATH": __import__("os").environ["PATH"]})
    t = re.search(r"^targets=(.*)$", r.stdout, re.M)
    return t.group(1) if t else ("skip" if "mode=skip" in r.stdout else r.stdout + r.stderr)


ok(targets("", "2026-11-21 07:40") == "sat:2026-11-21:force sun:2026-11-22:auto", "土曜の schedule → 土曜版と明日の日曜版")
ok(targets("sat", "2026-11-21 08:00") == "sat:2026-11-21:force sun:2026-11-22:auto", "土曜の dispatch(sat) → 土曜版と明日の日曜版")
ok(targets("sun", "2026-11-21 08:13") == "sun:2026-11-22:auto", "土曜の dispatch(sun)（土曜版の取り込み後）→ 明日の日曜版")
ok(targets("", "2026-11-22 07:40") == "sun:2026-11-22:auto", "日曜の schedule → 今日の日曜版（予備）")
ok(targets("sun", "2026-11-22 08:00") == "sun:2026-11-22:auto", "日曜の dispatch(sun) → 今日の日曜版（予備）")
ok(targets("sun", "2026-11-21 10:00", "2026-11-22") == "sun:2026-11-22:force", "date 指定 → 作り直す")
ok(targets("sat", "2026-11-20 10:00") == "sat:2026-11-20:force", "金曜に sat を手で動かしたら土曜版だけ")
print("すべて ok")
