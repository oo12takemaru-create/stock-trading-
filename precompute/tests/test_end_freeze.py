# -*- coding: utf-8 -*-
"""検証期間の末（END）より後に日を足しても、検証の結果が1桁も動かないことの試験。

    python precompute/tests/test_end_freeze.py

■ 何を守るか（2026-10-17・Fable 案X）
  以前は END の時点で残っている建玉を、END より後の**いちばん新しい日の終値**で
  「期末強制決済」していた。日次で新しい日が足されるたびに、固定したはずの10年の
  累積リターンが動き、公開の検査（#621）に毎日当たって公開が止まった（2026-10-13〜）。

■ 見るもの（正本は使わない。作った値で portfolio_run.run を丸ごと通す）
  1. END までの材料と、END の後に日を足した材料で、summary・採用トレードの並び・
     月次カーブ・決済理由の内訳が完全に一致する（足す日数を変えて2通り）
  2. END の時点で残った建玉は、END の日の終値で「期末強制決済」になる
  3. 約定日・決済日が END を越えるトレードが無い
  4. 約定した日に強制決済になるだけの（損益0の）トレードを作らない
  5. ★対照★ 期間の末を後ろにずらせば答えが変わる
     ここが「変わらない」なら、足した日に意味のある値動きが無く、1 は何も見ていない
"""
from __future__ import annotations

import hashlib
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import config  # noqa: E402
import portfolio_engine as pe  # noqa: E402
import portfolio_run  # noqa: E402
from universe import JAPAN_STOCKS  # noqa: E402

PASS = FAIL = 0
START = "2026-06-01"
END = "2026-09-04"          # 検証期間の末（本番の portfolio_run.END と同じ日付にしておく）


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def fingerprint(trades):
    h = hashlib.sha256()
    for t in trades:
        h.update(("%s|%s|%s|%.6f|%.6f|%d|%s\n" % (
            t.ticker, t.entry_date, t.exit_date,
            t.entry_price, t.exit_price, t.shares, t.reason)).encode())
    return h.hexdigest()[:16]


def pick_tickers(n=4):
    """セクター係数が0でない銘柄を n 個（0 のセクターは建てないので試験にならない）"""
    risk = config.SECTOR_RISK_MULTIPLIER
    out = []
    for t, (_, sec) in JAPAN_STOCKS.items():
        if risk.get(sec, risk["default"]) > 0 and sec not in {JAPAN_STOCKS[x][1] for x in out}:
            out.append(t)
        if len(out) == n:
            break
    return out


def make_material(last_day: str, seed=7):
    """START〜last_day の営業日ぶんの材料。END より後は大きく動かす（決済に効くように）。"""
    dates = pd.bdate_range(START, last_day)
    rng = np.random.default_rng(seed)
    rows = []
    for k, t in enumerate(pick_tickers()):
        close = 1000.0 + 50 * k
        for d in dates:
            # END まではおだやかに上がる（損切りに触れない）。END の後は荒く動く
            step = 1.002 if d <= pd.Timestamp(END) else float(rng.choice([0.90, 1.12]))
            close *= step
            rows.append({"ticker": t, "date": d, "close": close, "low": close * 0.995,
                         "dev_25": -1.0, "dev_50": -1.0, "dev_75": -1.0, "dev_200": -1.0,
                         "vol_ratio_20": 1.0, "bb_pos_1_5": 0.0, "high_20_ratio": 0.9,
                         "high_52w_ratio": 0.9, "ret_5d": 0.0, "day_change": 0.0,
                         "knife_guard": False, "minervini_entry": False, "ema_50_pos": 101.0})
    panel = pd.DataFrame(rows)
    market = pd.DataFrame({"date": dates, "regime": "BULLISH", "sp500_change_1d": 0.0,
                           "sp500_change_3d": 0.0, "is_halt": False})
    return panel, market


# シグナルの日（END の前後に散らす）。END の前日のシグナル＝END に約定、は作らないことも見る
SIGNAL_DAYS = {pd.Timestamp(d) for d in (
    "2026-06-02", "2026-07-01", "2026-08-20", "2026-09-02", "2026-09-03", "2026-09-04",
    "2026-09-10")}

RULES = [
    # 長く持つ（90暦日）ので END をまたぐ → 期末強制決済になる
    pe.PresetRule(rule_id="long-hold", name="試験・長期", regimes=("BULLISH",),
                  signal=lambda cols, i, sector, regime, mkt, date: date in SIGNAL_DAYS,
                  exit=pe.PresetExit(stop_pct=-5.0, calendar_days=90,
                                     time_exit_label="タイムストップ")),
]


def run(panel, market, end=END):
    saved = portfolio_run.PRESET_RULES
    portfolio_run.PRESET_RULES = RULES
    try:
        return portfolio_run.run(panel, market, start=START, end=end, log=lambda m: None)
    finally:
        portfolio_run.PRESET_RULES = saved


def main():
    base_p, base_m = make_material(END)
    r0, t0 = run(base_p, base_m)
    s0 = r0["summary"]
    print("END までの材料: trades %s / cum %s / maxDD %s / 指紋 %s"
          % (s0.get("trades"), s0.get("total_return"), s0.get("max_dd"), fingerprint(t0)))
    check(s0.get("trades", 0) > 0, "トレードが1件以上ある（0件では何も見ていない）")

    print("\n― 1. END の後に日を足しても同じか ―")
    for last in ("2026-09-18", "2026-10-09"):
        p, m = make_material(last)
        r, t = run(p, m)
        same = (r["summary"] == s0 and fingerprint(t) == fingerprint(t0)
                and r["equity_curve"] == r0["equity_curve"]
                and r["exit_reasons"] == r0["exit_reasons"])
        check(same, "〜%s まで足しても summary・並び・カーブ・内訳が一致" % last,
              "" if same else "%s → %s" % (s0, r["summary"]))

    print("\n― 2. END に残った建玉は END の日の終値で閉じる ―")
    end_ts = pd.Timestamp(END)
    forced = [t for t in t0 if t.reason == "期末強制決済"]
    check(len(forced) > 0, "期末強制決済が1件以上ある", str(len(forced)))
    closes = base_p.set_index(["ticker", "date"])["close"]
    ok = all(t.exit_date == end_ts and abs(t.exit_price - closes[(t.ticker, end_ts)]) < 1e-9
             for t in forced)
    check(ok, "期末強制決済は END の日・END の終値")

    print("\n― 3. END を越えるトレードが無い ―")
    p, m = make_material("2026-10-09")
    _, t_long = run(p, m)
    over = [t for t in t_long if t.entry_date > end_ts or t.exit_date > end_ts]
    check(not over, "約定日・決済日とも END 以前", "%d 件越えている" % len(over) if over else "")

    print("\n― 4. 約定した日に強制決済になるだけのトレードを作らない ―")
    same_day = [t for t in t0 + t_long if t.entry_date == t.exit_date]
    check(not same_day, "約定日＝決済日のトレードが無い",
          "%d 件" % len(same_day) if same_day else "")
    # END の前日だけに出るシグナル → 約定は END の日 → 1日も持てない。建てないこと
    only_eve = pe.PresetRule(
        rule_id="eve", name="試験・前日", regimes=("BULLISH",),
        signal=lambda cols, i, sector, regime, mkt, date: date == pd.Timestamp("2026-09-03"),
        exit=pe.PresetExit(stop_pct=-5.0, calendar_days=90))
    mkt, _, _ = portfolio_run.build_market(base_m)
    for label, mat in (("END まで", base_p), ("END の後まで", p)):
        sub = mat[mat["ticker"] == mat["ticker"].iloc[0]].sort_values("date")
        cols = {c: sub[c].to_numpy().astype("float64") for c in ("close", "low")}
        dates = pd.DatetimeIndex(sub["date"].to_numpy())
        mk = {"regime": {d: "BULLISH" for d in dates}, "sp1": mkt["sp1"], "sp3": mkt["sp3"]}
        tr = pe.simulate_ticker("TEST", "", cols, dates, [only_eve], mk,
                                {"default": 1.0}, start=START, end=END)
        check(not tr, "END の前日のシグナルでは建てない（材料: %s）" % label,
              "" if not tr else "%s → %s" % (tr[0].entry_date.date(), tr[0].exit_date.date()))

    print("\n― 5. ★対照★ 期間の末を後ろにずらせば答えは変わる ―")
    r5, t5 = run(p, m, end="2026-10-09")
    changed = r5["summary"] != s0 or fingerprint(t5) != fingerprint(t0)
    check(changed, "★END の後の値動きは結果を変えうる（＝1 は意味のある比較）★")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
