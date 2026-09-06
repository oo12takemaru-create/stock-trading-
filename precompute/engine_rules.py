# -*- coding: utf-8 -*-
"""既存エンジンのシグナル生成条件を、前計算列だけで書き直したもの。

■ ここが 1-A の肝
既存エンジン(integrated_backtest_v2_8_0.py, 2874行)を実際に走らせると約55分かかるので、
「エンジンのコードから読み取った条件」を「前計算列での抽出条件」に1対1で対応付け、
論理的に同じであることをコメントで示す方式を取る。
下の各関数のdocstringに、移植元のファイル・行番号・原文の条件を必ず書いてある。

■ 実装が2系統あることに注意
  engine  = integrated_backtest_v2_8_0.py … 10年バックテストの数字(勝率61%/PF1.72等)の出所
  scanner = daily_scanner_v2_8_0.py       … signals_log.csv を毎日書いている側
両者は微妙に条件が違う。signals_log と突き合わせるときは scanner 版を使う。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import BNF_RULES, MOMENTUM_RULES, SECTOR_BNF_THRESHOLDS, REGIME_BNF_MULTIPLIER


def bnf_threshold_series(sectors, regimes):
    """セクター列と地合い列から、行ごとの BNF 閾値を作る（get_bnf_threshold のベクトル版）。"""
    base = pd.Series(sectors).map(SECTOR_BNF_THRESHOLDS).fillna(
        SECTOR_BNF_THRESHOLDS["default"])
    mult = pd.Series(regimes).map(REGIME_BNF_MULTIPLIER).fillna(1.0)
    return (base.to_numpy() * mult.to_numpy())


# ══════════════════════════════════════════════════════════════════
#  BNF-LITE（逆張り）
# ══════════════════════════════════════════════════════════════════

def bnf_lite_mask(df, threshold, variant="scanner"):
    """BNF-LITE シグナル該当日を bool 配列で返す。

    移植元 scanner daily_scanner_v2_8_0.py check_bnf_signal L699-738
        1) if len(df) < 25: return False
        2) deviation = (close - ma25) / ma25 * 100
           threshold  = get_bnf_threshold(sector, regime)
           if deviation > threshold: return False
        3) if vol < vol_avg * 1.1: return False          # vol_avg = Vol20
        4) bb_check = df["BB_lower_1_5"]
           if pd.isna(bb_check) or close > bb_check: return False
        5) 落ちるナイフ・ガード(2026-06 追加, scanner のみ):
           if deviation < -25.0 and close < ma200: return False

    移植元 engine integrated_backtest_v2_8_0.py check_bnf_signal L1174-1260
        2)3)4) は同じ。5) のナイフ・ガードは無い。

    前計算列での対応:
        2) dev_25 <= threshold
        3) vol_ratio_20 >= 1.1        ★設計書§3の vol_ratio_25 では再現できない
        4) close <= bb_lower_1_5
        5) not (dev_25 < -25.0 and close < ma_200)
    """
    dev = df["dev_25"].to_numpy(dtype="float64")
    volr = df["vol_ratio_20"].to_numpy(dtype="float64")
    close = df["close"].to_numpy(dtype="float64")
    bb = df["bb_lower_1_5"].to_numpy(dtype="float64")
    ma200 = df["ma_200"].to_numpy(dtype="float64")
    th = np.asarray(threshold, dtype="float64")

    m = ~np.isnan(dev) & ~np.isnan(volr) & ~np.isnan(bb)
    m &= dev <= th
    m &= volr >= float(BNF_RULES["vol_mult"])
    m &= close <= bb
    if variant == "scanner":
        knife = float(BNF_RULES["knife_depth"])
        m &= ~((dev < knife) & (close < ma200) & ~np.isnan(ma200))
    return m


# ══════════════════════════════════════════════════════════════════
#  MOMENTUM（20日高値ブレイク）
# ══════════════════════════════════════════════════════════════════

def momentum_mask(df, sp500_change_1d=None, sp500_change_3d=None, variant="scanner"):
    """MOMENTUM シグナル該当日を bool 配列で返す。

    移植元 scanner check_momentum_signal L746-806 (v2.8.1)
        1) if len(df) < 200: return False
        2) if sp500_change_1d < -1.0: return False
           if sp500_change_3d < -3.0: return False
        3) if close < ma200 or close < ma50: return False
        4) prev_high = High[idx-20:idx].max()    # 当日を除く20日高値
           if high < prev_high: return False
        5) if vol < vol_avg * 1.5: return False  # vol_avg = Vol20
        6) ret_5d = (close/Close[idx-5]-1)*100 ; if ret_5d > 12: return False
        7) day_change = (close/Close[idx-1]-1)*100 ; if day_change > 8: return False
        8) high_52w = High[idx-252:idx+1].max()
           if close < high_52w * 0.95: return False       # v2.8.1 新高値型フィルタ

    移植元 engine check_momentum_signal L1264-1330 (v2.8.0)
        4) が if high <= prev_high * 1.001 （0.1%以上の明確なブレイクを要求）
        8) の52週高値フィルタは無い
        ほかは同じ。

    前計算列での対応:
        3) close >= ma_200 and close >= ma_50
        4) high >= high_20            （scanner）/ high > high_20 * 1.001（engine）
        5) vol_ratio_20 >= 1.5
        6) ret_5d <= 12
        7) day_change <= 8
        8) high_52w_ratio >= 95        （= close / 52週高値 * 100）
    """
    r = MOMENTUM_RULES
    close = df["close"].to_numpy(dtype="float64")
    high = df["high"].to_numpy(dtype="float64")
    ma50 = df["ma_50"].to_numpy(dtype="float64")
    ma200 = df["ma_200"].to_numpy(dtype="float64")
    h20 = df["high_20"].to_numpy(dtype="float64")
    volr = df["vol_ratio_20"].to_numpy(dtype="float64")
    ret5 = df["ret_5d"].to_numpy(dtype="float64")
    dchg = df["day_change"].to_numpy(dtype="float64")
    h52 = df["high_52w_ratio"].to_numpy(dtype="float64")

    m = ~np.isnan(ma50) & ~np.isnan(ma200) & ~np.isnan(volr) & ~np.isnan(h20)
    m &= (close >= ma200) & (close >= ma50)
    if variant == "scanner":
        m &= high >= h20
    else:
        m &= high > h20 * 1.001
    m &= volr >= float(r["vol_mult"])
    m &= ~(ret5 > float(r["ret_5d_max"]))
    m &= ~(dchg > float(r["day_change_max"]))
    if variant == "scanner":
        m &= h52 >= float(r["high_52w_min_ratio"]) * 100

    if sp500_change_1d is not None:
        s1 = np.asarray(sp500_change_1d, dtype="float64")
        s3 = np.asarray(sp500_change_3d, dtype="float64")
        m &= ~(s1 < float(r["sp500_change_1d_min"]))
        m &= ~(s3 < float(r["sp500_change_3d_min"]))
    return m


# ══════════════════════════════════════════════════════════════════
#  MINERVINI（Trend Template）
# ══════════════════════════════════════════════════════════════════

def trend_template_mask(df, lite=True):
    """Minervini Trend Template を前計算列で近似したもの。

    移植元 engine check_trend_template L1033-1078 / scanner L812-847
        close > ma150 and close > ma200
        ma150 > ma200
        (lite以外) ma200 > ma200[idx-20]
        ma50 > ma150 > ma200
        close > ma50
        close >= 52週安値 * 1.25
        close >= 52週高値 * 0.75
        ret_6m >= 15 (lite は 5)

    ★前計算列に無いもの: 52週安値、MA200の20日前の値。
      → 前者は low_52w 列を足せば済む。後者は ma_200 の shift(20) で作れる（要 ticker 単位）。
      本関数は「52週高値0.75条件」「MA並び」「ret_126」までを列だけで見る近似。
      MINERVINI の本体（VCP検出・MA50押し目）は ATR20 と60本ウィンドウの
      レンジ収縮判定を使うので、設計書§3 の列だけでは再現できない（検証レポートで指摘）。
    """
    close = df["close"].to_numpy(dtype="float64")
    ma50 = df["ma_50"].to_numpy(dtype="float64")
    ma150 = df["ma_150"].to_numpy(dtype="float64")
    ma200 = df["ma_200"].to_numpy(dtype="float64")
    h52r = df["high_52w_ratio"].to_numpy(dtype="float64")
    ret126 = df["ret_126"].to_numpy(dtype="float64")

    m = ~np.isnan(ma50) & ~np.isnan(ma150) & ~np.isnan(ma200)
    m &= (close > ma150) & (close > ma200) & (ma150 > ma200)
    m &= (ma50 > ma150) & (ma150 > ma200) & (close > ma50)
    m &= h52r >= 75.0
    m &= ~(ret126 < (5.0 if lite else 15.0))
    return m
