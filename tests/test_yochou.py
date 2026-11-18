# -*- coding: utf-8 -*-
"""予兆レーダーのテスト: 日付単位の検定・ステータス・文面の禁止語・分割の物差し・p 値

python -X utf8 tests/test_yochou.py
（本番の場所には何も書かない。合成の値はこのファイルの中だけ）
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yochou as Y                 # noqa: E402

NG = 0


def eq(got, want, label, tol=None):
    global NG
    ok = (abs(got - want) <= tol) if tol is not None and got is not None else got == want
    if not ok:
        NG += 1
        print(f"  NG {label}\n     期待={want}\n     実際={got}")


# ── p 値（t 分布の両側）: 表の値
eq(round(Y.t_pvalue(2.228, 10), 3), 0.050, "t=2.228, df=10 → p=0.05")
eq(round(Y.t_pvalue(1.96, 1000), 3), 0.050, "t=1.96, df=1000 → p≒0.05")
eq(round(Y.t_pvalue(0.0, 5), 3), 1.0, "t=0 → p=1")

# ── 日付単位: 同じ日の銘柄は平均にまとめる（3銘柄が同じ日 → 1観測）
obs = [("2026-01-05", 3.0, "backfill"), ("2026-01-05", 1.0, "backfill"), ("2026-01-05", 2.0, "backfill"),
       ("2026-01-06", -1.0, "backfill")]
ordinal = {"2026-01-05": 0, "2026-01-06": 1}
r = Y.stat_record("volume_surge", "all", 1, obs, ordinal)
eq(r["n"], 4, "件数は銘柄ごと")
eq(r["days"], 2, "発生日数は日付")
eq(r["day_mean"], 0.5, "日付の平均 = (2.0 + (−1.0)) / 2")
eq(r["mean"], 1.25, "件数の平均 = 5/4")
eq(r["status"], "insufficient", "30日未満は insufficient")

# ── 小さな例の検定（手計算）: 日ごとの値 1,2,−0.5,3,1.5 → 平均1.4・標準偏差1.3416・t=1.4/(1.3416/√5)=2.3333…
vals = [1.0, 2.0, -0.5, 3.0, 1.5]
m, t, p, pn = Y.date_level_test(vals, [0, 1, 2, 3, 4], 1)
sd = math.sqrt(sum((x - 1.4) ** 2 for x in vals) / 4)
eq(round(t, 6), round(1.4 / (sd / math.sqrt(5)), 6), "窓1は素朴な t 検定と同じ")
eq(round(p, 4), round(pn, 4), "窓1は補正なし")

# ── 窓の重なり（正の相関）は p を大きくする（控えめ）。手計算: lag = Σ 2·w·e_i·e_j（w = 1 − 距離/h）
vals = [1.0, 1.2, 1.1, 0.9, 1.0, -0.2, -0.1, 0.0, 0.1, -0.3]
pos = list(range(10))
m, t, p, pn = Y.date_level_test(vals, pos, 5)
mu = sum(vals) / 10
e = [x - mu for x in vals]
s0 = sum(x * x for x in e)
lag = sum(2 * (1 - (i - j) / 5) * e[i] * e[j] for i in range(10) for j in range(i) if i - j < 5)
eq(round(t, 6), round(mu / math.sqrt((s0 + lag) / 9 / 10), 6), "Bartlett の重みの手計算と一致")
eq(p > pn, True, "重なりの補正で p が大きくなる")

# ── ステータス: 前半と後半で向きが違えば confirmed にしない
days = [f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in (6, 13, 20, 27)][:40]
ordd = {d: i * 10 for i, d in enumerate(days)}
obs = [(d, 1.0 + 0.1 * (i % 3), "backfill") for i, d in enumerate(days)]
r = Y.stat_record("high52_close", "all", 1, obs, ordd)
eq(r["status"], "confirmed", "40日すべてプラス → confirmed")
obs2 = [(d, (2.0 if i < 20 else -0.5) + 0.1 * (i % 3), "backfill") for i, d in enumerate(days)]
r2 = Y.stat_record("high52_close", "all", 1, obs2, ordd)
eq(r2["status"] != "confirmed", True, "後半の向きが逆 → confirmed にしない")
eq(r2["hypothesis_dir"], 1, "仮説の向き")

# ── live の印: 20件未満は live_ok にならない
obs3 = obs + [(days[0], 1.0, "live")] * 5
eq(Y.stat_record("high52_close", "all", 1, obs3, ordd)["live_ok"], False, "live 5件 → live_ok=False")

# ── 文面: 禁止語を含まない・件数・期間・窓が入る
rec = {"n": 312, "days": 118, "median": -1.8, "pos_rate": 39.0, "neg_rate": 61.0, "window": 20,
       "period": ["2021-11-19", "2026-11-17"]}
for t in list(Y.PHRASE) + ["thermo_stage_change"]:
    e = {"event_type": t, "code": "MKT" if t in ("thermo_stage_change", "gauge_stage_up") else "7203",
         "event_params": {"from": "NEUTRAL", "to": "BEARISH"}}
    txt = Y.make_text(e, rec)
    eq(Y.banned_in(txt), [], f"禁止語なし: {txt}")
    eq(all(s in txt for s in ("312回", "118日", "20営業日後", "過去5年")), True, f"件数・期間・窓: {txt}")
eq(Y.banned_in("信用買残が前回比30%以上増加"), [], "買残は可")
eq(Y.banned_in("空売り残高の報告"), [], "空売り残高（JPX の統計の名前）は可")
eq(Y.banned_in("空売りが増えた"), ["売り"], "空売り残高以外の「売り」は不可")
eq(Y.banned_in("買いが増えた"), ["買い"], "買いは不可")
eq(Y.make_text({"event_type": "volume_surge", "code": "7203"}, rec),
   "【予兆】7203 出来高が20日平均の3倍以上に。過去5年で同じ条件は312回（118日）。20営業日後の対TOPIXは中央値-1.8%、マイナスだった割合61%。",
   "設計書6章の型")

# ── 仮説は検出関数の種類をすべて覆う（集計の前に書く約束）
import ledger2_detect as L        # noqa: E402
eq(sorted(set(L.TYPES) - set(Y.HYPOTHESES)), [], "仮説が無い種類")

print("NG", NG) if NG else print("OK")
sys.exit(1 if NG else 0)
