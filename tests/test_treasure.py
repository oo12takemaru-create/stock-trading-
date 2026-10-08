# -*- coding: utf-8 -*-
"""宝探し（treasure_daily.py）のテスト。ネットに出ない"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from treasure_daily import quiet, contra, pick, reason      # noqa: E402

NG = 0


def eq(got, want, label):
    global NG
    if got != want:
        NG += 1
        print(f"  NG {label}\n     期待={want!r}\n     実際={got!r}")


# ---- 静けさ: vr≦0.5・|c20|≦3・買残の5日変化がマイナス（境界は含む）
P = {"A": {"vr": 0.5, "c20": 3.0, "p": 100}, "B": {"vr": 0.6, "c20": 0, "p": 1},
     "C": {"vr": 0.3, "c20": -3.1, "p": 1}, "D": {"vr": 0.2, "c20": 1, "p": 1}, "E": {"vr": 0.1, "c20": 0, "p": 1}}
M = {"A": {"b": 900, "bd": -100, "s": 10}, "B": {"b": 1, "bd": -1, "s": 1}, "C": {"b": 1, "bd": -1, "s": 1},
     "D": {"b": 1000, "bd": 0, "s": 1}}
q = quiet(P, M)
eq([x["c"] for x in q], ["A"], "静けさは境界を含み、買残が減っていない・信用残の無い銘柄は外す")
eq(q[0]["bd_pct"], -10.0, "買残の5日変化率は5営業日前の残高に対する割合")
eq(reason("quiet", {**q[0], "vr": 0.0})[0], "出来高が20日平均の5%未満まで低下", "vr=0.0 は「5%未満」")

# ---- 逆張り: (空売り上位10% or 信用倍率<1) かつ 52週高値から−30%以下
P2 = {str(i): {"hi52": -40, "p": 1} for i in range(20)}
P2["19"]["hi52"] = -29.9
K = {"stocks": [{"c": str(i), "total": float(i)} for i in range(20)]}
M2 = {"0": {"b": 50, "s": 100}, "1": {"b": 100, "s": 100}}
ct, meta = contra(P2, M2, K)
eq(sorted(x["c"] for x in ct), ["0", "18"], "上位10%（20銘柄なら2つ: 19・18）のうち高値−30%以下は18、倍率<1は0")
eq(meta["karauri_threshold"], 18.0, "上位10%の境目")
eq([x for x in ct if x["c"] == "1"], [], "信用倍率ちょうど1倍は入れない")

# ---- 今日の一枚: 同じ日なら何度でも同じ、入力の順序に左右されない
a, n = pick("2026-10-08", ["3", "1", "2"])
b, _ = pick("2026-10-08", ["2", "3", "1"])
eq((a, n), (b, 3), "日付シードで同じ銘柄")
eq(pick("2026-10-08", []), (None, 0), "候補が無い日は無し")

print("OK" if NG == 0 else f"NG {NG}件")
sys.exit(1 if NG else 0)
