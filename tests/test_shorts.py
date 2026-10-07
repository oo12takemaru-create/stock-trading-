# -*- coding: utf-8 -*-
"""空売り機関トラッカー（shorts_build.py）と名寄せ表のテスト。ネットに出ない"""
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shorts_build import delta, new_score          # noqa: E402
from karauri_score import seller_ids                # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


# ---- 名寄せ表
rows = list(csv.DictReader((ROOT / "data" / "sellers.csv").open(encoding="utf-8")))
names = [r["name"] for r in rows]
ids = [r["id"] for r in rows if r["id"]]
eq(len(names), len(set(names)), "報告書上の名前に重複が無い")
eq(len(ids), len(set(ids)), "IDに重複が無い")
eq([i for i in ids if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i)], [], "IDは半角英小文字のスラッグ")
groups = {}
for r in rows:
    if r["group_id"]:
        groups.setdefault(r["group_id"], set()).add(r["group_display"])
eq({g: len(v) for g, v in groups.items() if len(v) != 1}, {}, "同じグループの表示名は1つ")
eq(seller_ids().get("個人"), None, "「個人」は機関ではないのでIDを付けない")
eq(seller_ids().get("GOLDMAN SACHS INTERNATIONAL"), "goldman-sachs-international", "報告書の名前からIDが引ける")

# ---- 残高の増減（pt・銘柄数）
eq(delta({"event": "新規", "ratio": 0.0061, "prev": None}), (0.61, 1), "新規は今回の割合ぶん増えて1銘柄増")
eq(tuple(round(x, 2) for x in delta({"event": "増加", "ratio": 0.0080, "prev": 0.0061})), (0.19, 0), "増加は差だけ")
eq(tuple(round(x, 2) for x in delta({"event": "解消", "ratio": 0.0042, "prev": 0.0061})), (-0.61, -1),
   "解消は前回ぶん全部が報告対象から外れる")

# ---- 成績（新規だけ・件数不足・母集団比）
def ev(event, d20, pub="2026-07-01"):
    return {"event": event, "d20": d20, "pub": pub}

rel = {"2026-07-01": 1.0}
eq(new_score([ev("新規", -2.0)] * 9, rel), {"n": 9, "low_n": True}, "10件未満は統計を出さない")
s = new_score([ev("新規", -2.0)] * 6 + [ev("新規", 3.0)] * 4 + [ev("増加", -50.0)] * 5, rel)
eq((s["n"], s["avg"], s["down_rate"], s["vs_universe"]), (10, 0.0, 60.0, -1.0),
   "新規だけを数え、増加は混ぜない（平均0%・下落60%・母集団比−1pt）")

print(f"{'NG ' + str(NG) + '件' if NG else '全て一致 ✓'}")
sys.exit(1 if NG else 0)
