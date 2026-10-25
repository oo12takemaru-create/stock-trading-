# -*- coding: utf-8 -*-
"""前計算の地合い判定の一致テスト。前計算・検証エンジン・本番スキャナーの3つが同じ答えを出すか。数字は作り物

なぜ要るか（2026-10-25）:
  #716 で本番スキャナーを検証エンジンと同じ判定にした（tests/test_regime_parity.py が見張る）。
  ルールビルダーの前計算（precompute/metrics.py）は別に書かれていて、
    ・正本 regime が旧スキャナー版（BULLISH に50日線と1ヶ月騰落の条件が無い）だった
    ・日経1ヶ月騰落が検証エンジンと1本ずれていた（前計算22本前・エンジン21本前）
  の2つで答えが違った。公開している10年の数字は前計算から出るので、ここも同じ判定に揃え、
  どれかを変えたらこのテストが必ず落ちるようにする。

  1. 4つの地合いがすべて出る作り物の相場で、前計算の regime と検証エンジンの detect_market_regime が全日一致
  2. 前計算の nikkei_1m_change と、検証エンジンの signals["n225_1m_change"] の値が全日一致（作られている日）
  3. 前計算の is_halt・halt_reason と、検証エンジンの precompute_halt_only_states が全日一致
  4. 本番スキャナー（daily_scanner_v2_8_0.detect_market_regime）とも全日一致
  5. 履歴が200日に満たない最初の期間・指数が欠けている場合も一致
  6. regime_engine は regime と同じ。regime_scanner は旧スキャナー版（22本前比）の答え
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "precompute"))
import daily_scanner_v2_8_0 as SCAN          # noqa: E402
import integrated_backtest_v2_8_0 as ENGINE  # noqa: E402
import metrics                               # noqa: E402
from config import HALT_RULES                # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


def make_market(seed=11, n=900):
    """強気・中立・弱気・暴落と HALT がすべて現れるよう、区間ごとに傾きとVIXを変えた作り物の相場"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    plan = [(260, 0.0010, 0.0008, 14),   # 上昇（200日線が育つ）
            (90, -0.0015, 0.0005, 18),   # 50日線割れ・1ヶ月マイナス（中立になりやすい）
            (80, 0.0012, 0.0010, 15),    # 再上昇
            (40, -0.0090, -0.0060, 37),  # 急落（暴落・弱気・HALT）
            (120, -0.0005, -0.0002, 24), # 戻りの鈍い弱気
            (310, 0.0009, 0.0007, 16)]   # 回復
    n_dr, s_dr, vx = [], [], []
    for days, a, b, v in plan:
        n_dr += [a] * days
        s_dr += [b] * days
        vx += [v] * days
    n_dr, s_dr, vx = np.array(n_dr[:n]), np.array(s_dr[:n]), np.array(vx[:n], dtype=float)
    n225 = 20000 * np.exp(np.cumsum(n_dr + rng.normal(0, 0.011, n)))
    sp = 3000 * np.exp(np.cumsum(s_dr + rng.normal(0, 0.009, n)))
    vix = np.clip(vx + rng.normal(0, 2.5, n), 9, 80)
    frame = lambda x: pd.DataFrame({"Close": x}, index=idx)  # noqa: E731
    return {"^N225": frame(n225), "^GSPC": frame(sp), "^VIX": frame(vix)}, idx


def compare(g, dates, label):
    md = metrics.compute_market_condition(g, dates).set_index("date")
    halt = ENGINE.precompute_halt_only_states(g, list(dates), dict(HALT_RULES))
    seen, d_reg, d_chg, d_halt, d_scan = set(), [], [], [], []
    for d in dates:
        b, sig = ENGINE.detect_market_regime(g, d)
        a = md.at[d, "regime"]
        seen.add(b)
        if a != b:
            d_reg.append((d.date(), a, b))
        if "n225_1m_change" in sig and not (md.at[d, "nikkei_1m_change"] == sig["n225_1m_change"]):
            d_chg.append((d.date(), md.at[d, "nikkei_1m_change"], sig["n225_1m_change"]))
        st, rs = halt[d]
        # 文の違いは閾値の書き方だけ（エンジンは float の 35.0 をそのまま書く・前計算は %.0f）。数と理由は同じであること
        rs = rs.replace("> 35.0（", "> 35（") if rs else rs
        mine = md.at[d, "halt_reason"]
        mine = None if mine is None or (isinstance(mine, float) and np.isnan(mine)) else mine
        if bool(md.at[d, "is_halt"]) != (st == "HALT") or mine != rs:
            d_halt.append((d.date(), md.at[d, "is_halt"], md.at[d, "halt_reason"], st, rs))
        sc = SCAN.detect_market_regime(g, d.date())[0]
        if sc != b:
            d_scan.append((d.date(), sc, b))
    ok(not d_reg, f"{label}: 前計算と検証エンジンの地合いが {len(d_reg)}日で不一致 例={d_reg[:3]}")
    ok(not d_chg, f"{label}: 日経1ヶ月騰落の値が {len(d_chg)}日で不一致 例={d_chg[:3]}")
    ok(not d_halt, f"{label}: HALT が {len(d_halt)}日で不一致 例={d_halt[:3]}")
    ok(not d_scan, f"{label}: 本番スキャナーと検証エンジンが {len(d_scan)}日で不一致 例={d_scan[:3]}")
    ok((md["regime_engine"] == md["regime"]).all(), f"{label}: regime_engine が regime と違う")
    return seen, md


def scanner_old(g, d):
    """旧スキャナー版（2026-10-25 までの前計算の正本）を素直に書いたもの。22本前比・50日線と騰落の条件なし"""
    sig = {}
    n = g.get("^N225")
    if n is not None:
        s = n[n.index <= d]["Close"]
        if len(s) >= 200:
            sig["a200"] = s.iloc[-1] > s.rolling(200).mean().iloc[-1]
            if len(s) >= 23:
                sig["chg"] = (s.iloc[-1] / s.iloc[-23] - 1) * 100
    sp = g.get("^GSPC")
    if sp is not None:
        s = sp[sp.index <= d]["Close"]
        if len(s) >= 200:
            sig["s200"] = s.iloc[-1] > s.rolling(200).mean().iloc[-1]
    vx = g.get("^VIX")
    vix = float(vx[vx.index <= d]["Close"].iloc[-1]) if vx is not None and len(vx[vx.index <= d]) else 20
    chg = sig.get("chg", 0)
    if vix > 30 and chg < -10:
        return "PANIC"
    if not sig.get("a200", True) or vix > 25:
        return "BEARISH"
    if sig.get("a200", False) and sig.get("s200", False) and vix < 20:
        return "BULLISH"
    return "NEUTRAL"


g, idx = make_market()
seen, md = compare(g, idx, "全日")
for r in ("BULLISH", "NEUTRAL", "BEARISH", "PANIC"):
    ok(r in seen, f"作り物の相場に {r} が1日も無い（テストとして弱い）")
ok(md["is_halt"].any(), "作り物の相場に HALT が1日も無い（テストとして弱い）")
d_old = [d.date() for d in idx if md.at[d, "regime_scanner"] != scanner_old(g, d)]
ok(not d_old, f"regime_scanner が旧スキャナー版と {len(d_old)}日で不一致 例={d_old[:3]}")
ok((md["regime_scanner"] != md["regime"]).any(), "旧スキャナー版と正本が1日も違わない（テストとして弱い）")

# 21本と22本の違いを見分けられる相場であること（1本ずれたら落ちることの確かめ）
c = g["^N225"]["Close"]
c22 = metrics._align(metrics.n225_change_pct(c, 22), idx)
ok((c22 != md["nikkei_1m_change"]).sum() > 100, "22本前比と21本前比が見分けられない（テストとして弱い）")

for drop in ("^N225", "^GSPC", "^VIX"):
    g2 = {k: v for k, v in g.items() if k != drop}
    compare(g2, idx[::7], f"{drop} 欠け")

g3, idx3 = make_market(seed=23)
compare(g3, idx3, "別の乱数")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
