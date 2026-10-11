# -*- coding: utf-8 -*-
"""前計算バッチの設定。

閾値・判定パラメータはすべて thresholds.json に切り出してある（設計書 §3 の要求）。
このモジュールは JSON を読み、既存エンジンと同じ意味のヘルパを提供する。

出典（読むだけ・変更していない）:
  https://github.com/oo12takemaru-create/stock-trading-
    integrated_backtest_v2_8_0.py … バックテストエンジン（正解の基準）
    daily_scanner_v2_8_0.py       … 日次スキャナ（signals_log.csv を生成している側）
"""
from __future__ import annotations

import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
OUT_DIR = os.path.join(BASE_DIR, "out")
THRESHOLDS_PATH = os.path.join(BASE_DIR, "thresholds.json")

# グローバル指数（既存エンジンの GLOBAL_TICKERS と同じ）
GLOBAL_TICKERS = {
    "^N225": "日経225",
    "^GSPC": "S&P500",
    "^VIX": "VIX恐怖指数",
}

# 既定の取得期間（年）
DEFAULT_YEARS = 10

# ★材料の開始日は「作った日」ではなく検証期間で決める★（2026-10-11）
#   以前は `今日 − 10年` で切っていたため、作り直すたびに材料の開始日が
#   後ろへずれ、検証期間（2016-09-06〜）の頭が月ごとに削られていた。
#   取り始めも `今日` から逆算していたので、ewm（ema_50 など）の値まで
#   作った日によって変わった。同じ価格でも開始日だけで
#   最大DDが −25.4〜−28.7 の幅で動いた（実測）。
#   検証期間の頭に固定し、取り始めはそこから一定の日数だけ前にする。
#   portfolio_run.START はこの値と一致していなければならない（import 時に確かめる）。
MATERIAL_START = "2016-09-06"
#: 移動平均200日・52週高値のウォームアップに約1.2年ぶん余分に取る
WARMUP_DAYS = 460

# 既存エンジン(integrated_backtest_v2_8_0.py)の取得方法に合わせる
#   engine : yf.download(..., auto_adjust=True)
#   scanner: yf.download(..., auto_adjust=False)   ← signals_log.csv はこちら
DEFAULT_AUTO_ADJUST = True

with open(THRESHOLDS_PATH, encoding="utf-8") as _f:
    TH = json.load(_f)

SECTOR_BNF_THRESHOLDS = TH["sector_bnf_thresholds"]
REGIME_BNF_MULTIPLIER = TH["regime_bnf_multiplier"]
SECTOR_RISK_MULTIPLIER = TH["sector_risk_multiplier"]
REGIME_RULES = TH["regime_rules"]
HALT_RULES = TH["halt_rules"]
BNF_RULES = TH["bnf_rules"]
MOMENTUM_RULES = TH["momentum_rules"]


def get_bnf_threshold(sector: str, regime: str) -> float:
    """セクターと地合いから BNF 逆張りの乖離率閾値を返す。

    移植元: integrated_backtest_v2_8_0.py L753-757 / daily_scanner_v2_8_0.py L511
        base = SECTOR_BNF_THRESHOLDS.get(sector, SECTOR_BNF_THRESHOLDS["default"])
        mult = REGIME_BNF_MULTIPLIER.get(regime, 1.0)
        return base * mult
    """
    base = SECTOR_BNF_THRESHOLDS.get(sector, SECTOR_BNF_THRESHOLDS["default"])
    mult = REGIME_BNF_MULTIPLIER.get(regime, 1.0)
    return base * mult


def ensure_dirs() -> None:
    for d in (CACHE_DIR, OUT_DIR, os.path.join(CACHE_DIR, "prices")):
        os.makedirs(d, exist_ok=True)
