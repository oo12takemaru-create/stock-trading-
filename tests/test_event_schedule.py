# -*- coding: utf-8 -*-
"""今後の日程（event_score.py の build_schedule）の答え合わせ

公式の日程（BLS・Fed・日銀・JPX）と、日本時間への直し方（米国の夏時間・FOMCの翌日）を
実物の日付で確かめる。日程の正は event_dates.json の1か所（積上⑥ 2026-10-08）。
使い方: python -X utf8 tests/test_event_schedule.py
"""
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import event_score as E  # noqa: E402

tbl = json.loads((HERE / "event_dates.json").read_text(encoding="utf-8"))
sc = E.build_schedule(tbl, date(2026, 10, 8))
by = {(e["d"], e["k"]): e for e in sc}

CASES = [
    # (JSTの日付, 種別, 時刻の表示, タイトルに含む)
    ("2026-10-14", "cpi", "21:30", "9月分"),          # BLS cpi.htm Oct. 14, 2026（夏時間）
    ("2026-11-06", "payroll", "22:30", "10月分"),     # BLS empsit.htm Nov. 06, 2026（冬時間に切り替わった後）
    ("2026-10-29", "fomc", "午前3:00", "10/27-28"),   # Fed 10/27-28 → 日本時間 10/29 3:00
    ("2026-12-10", "fomc", "午前4:00", "12/8-9"),     # 冬時間は 4:00
    ("2026-10-30", "boj", "昼ごろ", "10/29-30"),      # 日銀 index.htm
    ("2027-01-22", "boj", "昼ごろ", "1/21-22"),
    ("2026-10-09", "sq_minor", "寄付", "10月限"),     # 第2金曜
    ("2026-12-11", "sq", "寄付", "12月限"),
    ("2027-03-29", "kenri", "大引け", "3月末"),       # 3/31(水)の2営業日前（3/22 は春分の振替休日）
    ("2027-03-30", "kenri_ex", "寄付", "3月末"),
    ("2026-10-30", "topix", "大引け", "100%"),
]

ng = 0
for d, k, tm, frag in CASES:
    e = by.get((d, k))
    ok = bool(e) and e["time"] == tm and frag in e["t"]
    ng += not ok
    print(("OK " if ok else "NG ") + f"{d} {k}: {e and (e['t'], e['time'])}")
hz = E.build_horizon(tbl)
print("horizon:", hz)
ng += not all(k in hz for k in ("boj", "fomc", "cpi", "payroll"))
sys.exit(1 if ng else 0)
