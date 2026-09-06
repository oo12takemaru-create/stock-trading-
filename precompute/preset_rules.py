# -*- coding: utf-8 -*-
"""規定値ルール3本を、前計算列だけで書いたもの。

公開数字（10年・1,826トレード／勝率52.8%／PF1.55）を作る3戦略。
移植元は `集客サイト企画/_precompute/simulate_exits.py` の bnf_signal /
momentum_signal / minervini_signal で、そのまた元は daily_scanner_v2_8_0.py の check_*。

■ 相場環境で戦略が切り替わる（引き継ぎ書v2 §3.1）
    PANIC / BEARISH / NEUTRAL → BNF-LITE
    BULLISH                   → MOMENTUM
    BULLISH / NEUTRAL         → MINERVINI
  同じ日に複数当たったら priority の小さい方を採る（参照実装の評価順と同じ）。

■ 前計算列との対応
    close > BB下限         → bb_pos_1_5 <= 100
    volume >= vol20 * 1.1  → vol_ratio_20 >= 1.1
    close >= ma200         → dev_200 >= 0
    close >= ma50          → dev_50 >= 0
    high >= 20日高値       → high_20_ratio >= 100
    close >= 52週高値*0.95 → high_52w_ratio >= 95
    落ちるナイフ・ガード    → knife_guard（真＝建ててよい）
    VCP / 50日線押し目     → minervini_entry
"""
from __future__ import annotations

import numpy as np

from config import SECTOR_BNF_THRESHOLDS, REGIME_BNF_MULTIPLIER
from portfolio_engine import PresetExit, PresetRule


def bnf_threshold(sector: str, regime: str) -> float:
    base = SECTOR_BNF_THRESHOLDS.get(sector, SECTOR_BNF_THRESHOLDS["default"])
    return base * REGIME_BNF_MULTIPLIER.get(regime, 1.0)


def _ok(v) -> bool:
    return bool(np.isfinite(v))


def bnf_signal(cols, i, sector, regime, mkt, date) -> bool:
    dev = cols["dev_25"][i]
    volr = cols["vol_ratio_20"][i]
    bb = cols["bb_pos_1_5"][i]
    if not (_ok(dev) and _ok(volr) and _ok(bb)):
        return False
    if dev > bnf_threshold(sector, regime):
        return False
    if volr < 1.1:
        return False
    if bb > 100.0:
        return False
    return bool(cols["knife_guard"][i])


def momentum_signal(cols, i, sector, regime, mkt, date) -> bool:
    if mkt["sp1"].get(date, 0.0) < -1.0 or mkt["sp3"].get(date, 0.0) < -3.0:
        return False
    dev200, dev50 = cols["dev_200"][i], cols["dev_50"][i]
    volr, h20 = cols["vol_ratio_20"][i], cols["high_20_ratio"][i]
    if not (_ok(dev200) and _ok(dev50) and _ok(volr) and _ok(h20)):
        return False
    if dev200 < 0 or dev50 < 0:
        return False
    if h20 < 100.0:
        return False
    if volr < 1.5:
        return False
    if cols["ret_5d"][i] > 12.0:
        return False
    if cols["day_change"][i] > 8.0:
        return False
    h52 = cols["high_52w_ratio"][i]
    return _ok(h52) and h52 >= 95.0


def minervini_signal(cols, i, sector, regime, mkt, date) -> bool:
    return bool(cols["minervini_entry"][i])


PRESET_RULES = [
    PresetRule(
        rule_id="bnf-reversal",
        name="BNF逆張り",
        regimes=("PANIC", "BEARISH", "NEUTRAL"),
        signal=bnf_signal,
        exit=PresetExit(stop_pct=-5.0, ma_revert=25, calendar_days=14),
        priority=0,
    ),
    PresetRule(
        rule_id="momentum-breakout",
        name="モメンタム（20日高値ブレイク）",
        regimes=("BULLISH",),
        signal=momentum_signal,
        exit=PresetExit(stop_pct=-5.0, take_profit_pct=10.0, calendar_days=10),
        priority=1,
    ),
    PresetRule(
        rule_id="minervini-template",
        name="ミネルヴィニ・テンプレート",
        regimes=("BULLISH", "NEUTRAL"),
        signal=minervini_signal,
        exit=PresetExit(stop_pct=-9.0, split_take_pct=25.0,
                        exit_below_ema50=True, calendar_days=90,
                        time_exit_label="タイムストップ"),
        priority=2,
    ),
]
