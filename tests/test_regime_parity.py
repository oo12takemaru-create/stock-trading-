# -*- coding: utf-8 -*-
"""地合い判定の一致テスト。本番スキャナーと検証エンジンが同じ答えを出すか。数字は作り物（実データは使わない）

なぜ要るか（2026-10-24）:
  本番 daily_scanner_v2_8_0.detect_market_regime と
  検証エンジン integrated_backtest_v2_8_0.detect_market_regime が、最初から別々に書かれていた。
  本番の BULLISH は日経50日線と1ヶ月騰落を見ておらず、2016〜2026/10 の 468営業日で答えが違った。
  勝率・PF・最大DDはすべて検証エンジンの判定で出した数字なので、本番が違う判定で動くと
  「検証済みの仕組み」で運用していないことになる。
  → どちらかを変えたら、このテストが必ず落ちるようにする。

  1. 4つの地合い（BULLISH/NEUTRAL/BEARISH/PANIC）がすべて出る作り物の相場で、全日の判定が一致する
  2. 履歴が200日に満たない最初の期間（データ不足）でも一致する
  3. 指数が欠けている場合（日経だけ・S&P500だけ・VIXだけ無い）でも一致する
  4. 本番が日付を date で受け取っても、検証エンジンが Timestamp で受け取っても同じ答えになる
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_scanner_v2_8_0 as SCAN          # noqa: E402
import integrated_backtest_v2_8_0 as ENGINE  # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


def make_market(seed=11, n=900):
    """強気・中立・弱気・暴落がすべて現れるよう、区間ごとに傾きとVIXを変えた作り物の相場"""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    # 区間: (日数, 日経の日次ドリフト, S&Pの日次ドリフト, VIXの水準)
    plan = [(260, 0.0010, 0.0008, 14),   # 上昇（200日線が育つ）
            (90, -0.0015, 0.0005, 18),   # 50日線割れ・1ヶ月マイナス（中立になりやすい）
            (80, 0.0012, 0.0010, 15),    # 再上昇
            (40, -0.0080, -0.0060, 34),  # 急落（暴落・弱気）
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
    seen, diff = set(), []
    for d in dates:
        a = SCAN.detect_market_regime(g, d.date())[0]     # 本番は date で呼ばれる
        b = ENGINE.detect_market_regime(g, d)[0]          # 検証エンジンは Timestamp で呼ばれる
        seen.add(b)
        if a != b:
            diff.append((d.date(), a, b))
    ok(not diff, f"{label}: {len(diff)}日で不一致 例={diff[:3]}")
    return seen


g, idx = make_market()

# 1・2. 全日（最初の200日の履歴不足期間を含む）
seen = compare(g, idx, "全日")
for r in ("BULLISH", "NEUTRAL", "BEARISH", "PANIC"):
    ok(r in seen, f"作り物の相場に {r} が1日も無い（テストとして弱い）")

# 3. 指数が欠けている場合
for drop in ("^N225", "^GSPC", "^VIX"):
    g2 = {k: v for k, v in g.items() if k != drop}
    compare(g2, idx[::7], f"{drop} 欠け")

# 1ヶ月騰落の定義（22営業日前比）がずれていないことを、強気の境目で確かめる
g3, idx3 = make_market(seed=23)
compare(g3, idx3, "別の乱数")

# 5. 終値が空(NaN)の行（2026-10-24 追加）
#    yfinance は寄り付き前などに、その日の行を終値が空のまま付けてくる。
#    本番はそれを読み飛ばして「最後に値がある日」で判定し、空の行が無い検証エンジンと同じ答えになること。
#    （修正前は、日経の最後に空の行があると強気の相場を BEARISH と答えていた）
def with_nan_row(g, key, when):
    g2 = dict(g)
    d = g[key].copy()
    d.loc[when, "Close"] = np.nan
    g2[key] = d.sort_index()
    return g2


nxt = idx[-1] + pd.offsets.BDay(1)
for key in ("^N225", "^GSPC", "^VIX"):
    for d in idx[-60:]:   # 直近60日それぞれを「最終日」とみなして、その翌営業日に空の行を付ける
        when = d + pd.offsets.BDay(1)
        sub = {k: v[v.index <= d] for k, v in g.items()}
        a = SCAN.detect_market_regime(with_nan_row(sub, key, when), when.date())[0]
        b = ENGINE.detect_market_regime(sub, when)[0]
        ok(a == b, f"{key} 最後に空の行 {when.date()}: 本番={a} 検証={b}")

# 途中に空の行が1本混ざっていても、その行を除いた検証エンジンと同じ答えになる
mid = idx[400]
for key in ("^N225", "^GSPC", "^VIX"):
    gm = with_nan_row(g, key, mid)                       # 本番は空の行つき
    gc = {k: (v.drop(mid) if k == key else v) for k, v in g.items()}  # 検証は空の行なし
    diff = [d.date() for d in idx[350:520:3]
            if d != mid and SCAN.detect_market_regime(gm, d.date())[0] != ENGINE.detect_market_regime(gc, d)[0]]
    ok(not diff, f"{key} 途中に空の行: {len(diff)}日で不一致 例={diff[:3]}")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
