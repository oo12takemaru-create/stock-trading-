# -*- coding: utf-8 -*-
"""前計算列では書けない判定を、ブール列として先に計算しておく。

■ なぜ要るか
条件ブロック方式（列・演算子・数値）で書けない判定が2つある。

  1. 落ちるナイフ・ガード
       「25日乖離が -25% 未満 かつ 200日線割れ」を**除外**する。
       AND の否定なので、条件を並べる（＝AND で足す）形では書けない。
       → knife_guard 列（真＝建ててよい）にして、条件は knife_guard = 1 と書く。

  2. ミネルヴィニの入り口（VCP / 50日線押し目）
       過去60本のATR収縮とピボット判定。1日ぶんの列では表現できない。
       → minervini_entry 列（真＝入り口成立）にする。
       ミネルヴィニは会員が値を変えられない固定プリセットなので、
       ブール列にしても「自分で条件を組む」自由は損なわれない
       （企画書v2 §11: ミネルヴィニは無料の固定表示のみ）。

あわせて、出口判定に使う位置関係も列にしておく。
  ema_50_pos = 終値 ÷ 50日EMA × 100 … 100未満で「50EMA下抜け」（ミネルヴィニの出口）
  ※「終値が25日線まで戻った」は dev_25 >= 0 と同値なので列を持たない
    （close/ma25*100 == dev_25 + 100）。75日線も同じく dev_75 >= 0。

■ 移植元（読むだけ・変更していない）
  集客サイト企画/_precompute/simulate_exits.py
    detect_vcp() / detect_ma_pullback() / trend_template() / minervini_signal()
  これは公開数字（10年・1,826トレード／勝率52.8%／PF1.55）を作った実装で、
  さらにその元は daily_scanner_v2_8_0.py の各 check_*。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 移植元の定数（simulate_exits.py と同じ値であること）
VCP_LOOKBACK = 60
VCP_ATR_CONTRACTION = 0.90     # 直近5本のATR平均 < 過去10本のATR平均 * 0.90
VCP_PIVOT_LOW = 0.93           # 終値がピボットの93%〜110%に居ること
VCP_PIVOT_HIGH = 1.10
VCP_RANGE_MAX = 15.0           # 直近レンジは15%以内
VCP_RANGE_RATIO = 0.85         # 直近レンジ < 前半平均レンジ * 0.85
PULLBACK_MA_DIST = 0.03        # 終値が50日線の±3%以内
PULLBACK_LOW_DIST = 1.02       # 直近10日安値が50日線の102%以下（＝一度は近づいた）
MINERVINI_VOL_MULT = 1.3
KNIFE_DEPTH = -25.0

COLUMNS = ["knife_guard", "minervini_entry", "ema_50_pos"]


def _atr20(high, low, close):
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(20).mean()


def _trend_template(a, i):
    """scanner check_trend_template（標準版）。simulate_exits.trend_template と同じ。"""
    ma50, ma150, ma200 = a["ma50"][i], a["ma150"][i], a["ma200"][i]
    if np.isnan(ma50) or np.isnan(ma150) or np.isnan(ma200):
        return False
    close = a["close"][i]
    if close <= ma150 or close <= ma200 or ma150 <= ma200:
        return False
    prev200 = a["ma200_20ago"][i]
    if np.isnan(prev200) or ma200 <= prev200:
        return False
    if not (ma50 > ma150 > ma200) or close <= ma50:
        return False
    low52, high52 = a["low52w"][i], a["high52w"][i]
    if np.isnan(low52) or np.isnan(high52) or low52 <= 0 or high52 <= 0:
        return False
    if close < low52 * 1.25 or close < high52 * 0.75:
        return False
    ret126 = a["ret126"][i]
    return not (np.isnan(ret126) or ret126 < 15.0)


def _detect_vcp(a, i, lookback=VCP_LOOKBACK):
    if i < lookback:
        return None
    s, e = i - lookback, i + 1
    atr = a["atr20"][s:e]
    atr_recent = np.nanmean(atr[-5:])
    atr_past = np.nanmean(atr[:10])
    if np.isnan(atr_recent) or np.isnan(atr_past) or atr_past == 0:
        return None
    if atr_recent > atr_past * VCP_ATR_CONTRACTION:
        return None
    hi, lw = a["high"][s:e], a["low"][s:e]
    pivot = np.nanmax(hi[:-1])
    cur = a["close"][i]
    if cur < pivot * VCP_PIVOT_LOW or cur > pivot * VCP_PIVOT_HIGH:
        return None
    ranges = []
    for k in range(0, lookback - 10, 10):
        h = np.nanmax(hi[k:k + 10])
        l = np.nanmin(lw[k:k + 10])
        if h > 0:
            ranges.append((h - l) / h * 100)
    if len(ranges) < 3:
        return None
    last = ranges[-1]
    if last > VCP_RANGE_MAX:
        return None
    half = max(1, len(ranges) // 2)
    if last >= (sum(ranges[:half]) / half) * VCP_RANGE_RATIO:
        return None
    return pivot


def _detect_ma_pullback(a, i):
    ma50 = a["ma50"][i]
    if np.isnan(ma50):
        return None
    close = a["close"][i]
    if abs(close - ma50) / ma50 > PULLBACK_MA_DIST:
        return None
    if a["recent_low10"][i] > ma50 * PULLBACK_LOW_DIST:
        return None
    if close <= a["open"][i]:
        if i > 0 and close <= a["close"][i - 1]:
            return None
    rh = a["recent_high20"][i]
    if np.isnan(rh):
        return None
    return rh


def compute(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV から、条件ブロックで書けない判定の列を作る。

    df: yfinance の日足（Open/High/Low/Close/Volume・index=DatetimeIndex）
    """
    close = df["Close"].astype("float64")
    high = df["High"].astype("float64")
    low = df["Low"].astype("float64")
    openp = df["Open"].astype("float64")
    vol = df["Volume"].astype("float64")

    ma25 = close.rolling(25).mean()
    ma50 = close.rolling(50).mean()
    ma150 = close.rolling(150).mean()
    ma200 = close.rolling(200).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    vol20 = vol.rolling(20).mean()

    out = pd.DataFrame(index=df.index)
    out["ema_50_pos"] = close / ema50.replace(0, np.nan) * 100

    # ── 落ちるナイフ・ガード（真＝建ててよい）──
    dev25 = (close - ma25) / ma25 * 100
    knife = ~((dev25 < KNIFE_DEPTH) & (close < ma200) & ma200.notna())
    out["knife_guard"] = knife.fillna(True).astype(bool)

    # ── ミネルヴィニの入り口 ──
    a = {
        "close": close.to_numpy(), "high": high.to_numpy(), "low": low.to_numpy(),
        "open": openp.to_numpy(), "volume": vol.to_numpy(),
        "ma50": ma50.to_numpy(), "ma150": ma150.to_numpy(), "ma200": ma200.to_numpy(),
        "ma200_20ago": ma200.shift(20).to_numpy(),
        "vol20": vol20.to_numpy(),
        "atr20": _atr20(high, low, close).to_numpy(),
        "high52w": high.rolling(253, min_periods=1).max().to_numpy(),
        "low52w": low.rolling(253, min_periods=1).min().to_numpy(),
        "ret126": ((close / close.shift(126) - 1) * 100).to_numpy(),
        "recent_high20": high.shift(1).rolling(20).max().to_numpy(),
        "recent_low10": low.rolling(11).min().to_numpy(),
    }
    n = len(df)
    entry = np.zeros(n, dtype=bool)
    v20 = a["vol20"]
    for i in range(200, n):
        if np.isnan(v20[i]) or a["volume"][i] < v20[i] * MINERVINI_VOL_MULT:
            continue
        if not _trend_template(a, i):
            continue
        p = _detect_vcp(a, i)
        if p is not None and a["high"][i] >= p:
            entry[i] = True
            continue
        p = _detect_ma_pullback(a, i)
        if p is not None and a["high"][i] >= p:
            entry[i] = True
    out["minervini_entry"] = entry
    return out
