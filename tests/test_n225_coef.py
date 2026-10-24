# -*- coding: utf-8 -*-
"""寄与度の係数（n225_coef.py）のテスト。数字は作り物（日経の配布物は使わない）

  1. 月末ウエート → 0.1 刻みの係数と除数（全体の倍率は結果に効かない）
  2. 入替（新しい銘柄）と臨時の係数変更を、その後の終値と日経平均から割り戻せる
  3. 分割の権利落ち日で係数に比率を掛ける
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import n225_coef as N   # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


random.seed(7)
codes = [f"{1000 + i}" for i in range(40)]
true = {c: random.choice([0.1, 0.2, 0.5, 1, 1, 1, 2, 3, 2.4, 22.5]) for c in codes}
days = [f"2026-09-{d:02d}" for d in range(1, 31)] + [f"2026-10-{d:02d}" for d in range(1, 11)]
px = {c: {} for c in codes + ["9001", "9002"]}
for c in px:
    p = random.uniform(300, 9000)
    for t in days:
        p *= 1 + random.gauss(0, 0.015)
        px[c][t] = round(p, 1)
D0 = 30.0
asof = "2026-09-30"
lv = sum(px[c][asof] * true[c] for c in codes)
w = {c: round(px[c][asof] * true[c] / lv * 100, 4) for c in codes}
px["N225"] = {t: sum(px[c][t] * true[c] for c in codes) / D0 for t in days if t <= asof}

# 1. 割り戻し
coef, D, st = N.derive(w, {c: px[c][asof] for c in codes}, px["N225"][asof])
scale = coef[codes[0]] / true[codes[0]]
ok(all(abs(coef[c] - true[c] * scale) < 1e-6 for c in codes), f"係数が既定値に戻る {st}")
ok(not st["offgrid"], "既定値に乗らない銘柄は無い")

# 2. 10/1 に入替（1000・1001 → 9001・9002 が係数1で入る）と 1002 の係数変更（×0.8）。除数は 9/30 の終値で続く
mem = [c for c in codes if c not in ("1000", "1001")] + ["9001", "9002"]
t2 = {c: true[c] for c in mem if c in true} | {"9001": 1.0, "9002": 1.0}
t2["1002"] = round(true["1002"] * 0.8, 1) if true["1002"] >= 1 else true["1002"]
D1 = sum(px[c][asof] * t2[c] for c in mem) / px["N225"][asof]
for t in days:
    if t > asof:
        px["N225"][t] = sum(px[c][t] * t2[c] for c in mem) / D1
seg = {"from": asof, "D": D, "coef": coef}
state = {"base": {"asof": asof}, "segs": [seg]}
chk = N.run_checks(state, px, days)
ok(any(not r["ok"] for r in chk), "入替の後は検算が合わなくなる")
fi = days.index("2026-10-01")
sg, info = N.refit(seg, px, days, fi, mem, set())
ok(sg is not None, f"割り戻し直せる {info}")
if sg:
    state["segs"].append(sg)
    ok(all(r["ok"] for r in N.run_checks(state, px, days)), "割り戻し後は全営業日で検算が合う")
    ok(abs(sg["coef"]["9001"] / scale - 1.0) < 1e-6, "新しい銘柄の係数")
# 日数が足りないときは試さない
sg2, why = N.refit(seg, px, days[:fi + 2], fi, mem, set())
ok(sg2 is None, "2営業日では割り戻さない")

# 3. 分割（権利落ち日 10/5・1:3）
st3 = {"basis": "2026-10-01", "segs": [{"from": asof, "D": 1, "coef": {"1003": 1.0, "1004": 2.0}}]}
sp = {"1003": [("2026-10-05", 3.0)], "1004": [("2026-09-01", 2.0)]}
co, _ = N.coef_now(st3, sp, "2026-10-04")
ok(co["1003"] == 1.0, "権利落ち日の前は掛けない")
co, _ = N.coef_now(st3, sp, "2026-10-05")
ok(co["1003"] == 3.0 and co["1004"] == 2.0, "権利落ち日から比率を掛ける（基準の日より前の分割は掛けない）")
N.rebase(st3, sp, "2026-10-06")
ok(st3["segs"][0]["coef"]["1003"] == 3.0 and st3["basis"] == "2026-10-06", "rebase で基準をそろえる")

print("n225_coef:", "OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
