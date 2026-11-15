# -*- coding: utf-8 -*-
"""日曜版（来週の地図）のプロンプトを、いま作る（作り直す）かどうかを決める（ai-weekly-prep.yml から呼ぶ）。

2026-11-15〜 日曜版は土曜の朝に書いておき、日曜の朝に公開する（本人決定）。
  prep は土曜の朝に ai-prompt/<日曜>-sun を作る。そのとき土曜版はまだ main に無いので、
  プロンプトの「昨日（土曜）の振り返り」の節は空（weekly.NO_SAT）になる。
  土曜版を取り込んだ後（ai-weekly-ingest.yml が mode=sun で prep を呼び直す）に、
  土曜 REFRESH_UNTIL（JST）までに限り、土曜版入りで作り直す。それより後は差し替えない
  （日曜版の routine が読み始めた後にプロンプトを変えると、取り込みの検査が別の問いで照らしてしまうため）。
  土曜の routine が書けず回答が無いまま日曜になったときは、日曜 SUN_REFRESH_UNTIL までの予備の prep
  （cron-worker の 7:40 / 8:00）でも作り直す（日曜の予備の routine は利用上限が戻る 16:00 より後に置く想定）。

  python -X utf8 weekly_prep_gate.py --date 2026-11-22 --answered 0 --existing EXISTING.txt docs/ai_weekly.json
  終了コード: 0=作る / 10=作らない（理由を表示）
"""
import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from weekly import JST, NO_SAT

REFRESH_UNTIL = "09:00"       # 土曜のこの時刻（JST）までなら、土曜版入りで作り直す
SUN_REFRESH_UNTIL = "15:00"   # 回答がまだ無ければ、日曜のこの時刻（JST）までも作り直す
BUILD, SKIP = 0, 10


def decide(d, out, answered, existing, now):
    """(作るか, 理由)。d=日曜版の日付(date) / out=main の ai_weekly.json / answered=ai-weekly/<d>-sun があるか /
    existing=いまの ai-prompt/<d>-sun の PROMPT.txt（無ければ ""）/ now=JST の datetime"""
    if ((out or {}).get("weekend") or {}).get("date") == d.isoformat():
        return False, f"{d} の日曜版は公開済み"
    if answered:
        return False, f"ai-weekly/{d}-sun がある（回答済み・取り込み待ち）"
    if not (existing or "").strip():
        return True, "まだ無いので作る"
    if NO_SAT not in existing:
        return False, "作成済み（土曜版入り）"
    sat = (out or {}).get("sat") or {}
    wsat = d - timedelta(days=(d.weekday() - 5) % 7)
    if sat.get("weekend") != wsat.isoformat():
        return False, "作成済み（土曜版はまだ main に無い）"
    hm = now.strftime("%H:%M")
    if not ((now.date() == wsat and hm < REFRESH_UNTIL) or (now.date() == d and hm < SUN_REFRESH_UNTIL)):
        return False, (f"作成済み（土曜版が無い版だが、差し替えてよい時刻〈土曜 {REFRESH_UNTIL}・日曜 {SUN_REFRESH_UNTIL} JST まで〉"
                       "を過ぎたので差し替えない）")
    return True, "土曜版を入れて作り直す"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default="docs/ai_weekly.json")
    ap.add_argument("--date", required=True, help="日曜版の日付 YYYY-MM-DD")
    ap.add_argument("--answered", default="0", help="ai-weekly/<日付>-sun があれば 1")
    ap.add_argument("--existing", default="", help="いまの ai-prompt/<日付>-sun の PROMPT.txt（無ければ空・無いファイル）")
    ap.add_argument("--now", default="", help="テスト用: JST の時刻 YYYY-MM-DDTHH:MM")
    a = ap.parse_args()
    d = date.fromisoformat(a.date)
    try:
        out = json.loads(Path(a.out).read_text(encoding="utf-8"))
    except Exception:
        out = {}
    ex = ""
    if a.existing and Path(a.existing).exists():
        ex = Path(a.existing).read_text(encoding="utf-8", errors="replace")
    now = datetime.fromisoformat(a.now).replace(tzinfo=JST) if a.now else datetime.now(JST)
    ok, why = decide(d, out, a.answered.strip() == "1", ex, now)
    print(f"日曜版 {d}: {'作る' if ok else '作らない'} — {why}")
    sys.exit(BUILD if ok else SKIP)


if __name__ == "__main__":
    main()
