# -*- coding: utf-8 -*-
"""成績表（ai_record.py）が前営業日の行を落とさないことのテスト（2026-11-17）。ネットにつながない
python -X utf8 tests/test_ai_record_guard.py

  1. 最新日の終値が空欄で返っても、fill_latest で埋めた終値で採点の行が残る（始値が空欄の日は資産曲線だけ飛ばす）
  2. 前の版より採点が減る・最新の採点済みの日が古くなる版は書かない（shrink_reason）
"""
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_record as R  # noqa: E402
import yf_latest  # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


# ── 1. 最新日の終値が空欄 ──
idx = pd.to_datetime(["2026-11-12", "2026-11-13", "2026-11-16"])
df = pd.DataFrame({"Open": [100.0, 101.0, np.nan], "Close": [100.5, 101.0, np.nan]}, index=idx)
fake = types.ModuleType("yfinance")
fake.download = lambda *a, **k: df
sys.modules["yfinance"] = fake
orig = yf_latest.fill_latest


def fake_fill(ticker, c, verbose=True, until=None):
    c = yf_latest._flat(c).copy()
    c.loc[pd.Timestamp("2026-11-16")] = 98.6     # 取引所の確定値で埋めた形
    return c.dropna()


yf_latest.fill_latest = fake_fill
try:
    px = R.n225_prices()
finally:
    yf_latest.fill_latest = orig
ok(px.get("2026-11-16") == {"Close": 98.6}, f"空欄の最新日は確定値の終値だけで残る {px.get('2026-11-16')}")
ok(px["2026-11-13"] == {"Close": 101.0, "Open": 101.0}, "ふつうの日は始値と終値")
same, _ = R.build_returns(R.n225_closes_from(px))
ok(round(same["2026-11-16"], 2) == round((98.6 / 101.0 - 1) * 100, 2), "最新日の騰落を採点できる")
curve = R.build_curve({"2026-11-13": {"stance": "attack"}, "2026-11-16": {"stance": "defense"}}, px)
ok([r["d"] for r in curve] == ["2026-11-13"], "始値が無い日は資産曲線だけ飛ばす")

# ── 2. 前の版より減る版は書かない ──
old = {"ai": {"n": 69, "rows": [{"d": "2026-11-13"}, {"d": "2026-11-16"}]},
       "regime": {"n": 70, "rows": [{"d": "2026-11-13"}]}}
ok(R.shrink_reason(old, old) is None, "同じなら書く")
ok(R.shrink_reason(None, old) is None, "前の版が無ければ書く")
grow = {"ai": {"n": 70, "rows": [{"d": "2026-11-17"}]}, "regime": {"n": 71, "rows": [{"d": "2026-11-16"}]}}
ok(R.shrink_reason(old, grow) is None, "増えるなら書く")
drop = {"ai": {"n": 68, "rows": [{"d": "2026-11-13"}]}, "regime": old["regime"]}
ok("減る" in (R.shrink_reason(old, drop) or ""), "AI朝刊の採点が減る版は書かない（11/17 03:57 の事故の形）")
stale = {"ai": {"n": 69, "rows": [{"d": "2026-11-13"}]}, "regime": old["regime"]}
ok("古くなる" in (R.shrink_reason(old, stale) or ""), "最新の採点済みの日が古くなる版は書かない")
rg = {"ai": old["ai"], "regime": {"n": 69, "rows": [{"d": "2026-11-12"}]}}
ok("地合い" in (R.shrink_reason(old, rg) or ""), "地合い判定の採点が減る版も書かない")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
