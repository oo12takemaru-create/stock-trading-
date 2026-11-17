# -*- coding: utf-8 -*-
"""「位置の温度」の共通部品（業種の温度・世界の温度・銘柄の温度）— 2026-11-17

■ 何か
  相場の温度計（thermo100.py）は「判定（4段階）で帯を決め、帯の中を過去10年の位置で決める」。
  判定は VIX・S&P500 を使う市場全体のものなので、業種・海外の指数・個別の銘柄には使えない。
  そこで、同じ考え方のうち **値動きの位置だけ** で温度を作る。これを「位置の温度」と呼び、
  相場の温度計とは別の物差しとして扱う（数字を直接くらべない）。

■ 作り方（その日までのデータだけを使う。先読みしない）
  1) 3つの入力: 終値と200日線の差（%）・終値と50日線の差（%）・22営業日前比（%）
     （相場の温度計の日経平均の3つの入力と同じ式）
  2) それぞれを「その日までの過去 WINDOW 本（約10年）の中で何%の位置か」にする（同じ値は半分ずつ数える）
  3) 3つの位置の平均（0〜100）を四捨五入した整数 = 温度
     （相場の温度計のように「同じ判定の日の中での位置」にもう一度直すことはしない。判定が無いのと、
       直すと1日の動きが大きくなるだけで幅はほとんど変わらないため。日経平均・S&P500 の過去10年で、
       平均のままでも 1%点〜99%点が 1〜99 に広がっている）
  4) ℃ = round(値 × 0.8 − 20)（相場の温度計と同じ読み替え。thermo100.celsius を使う）
  3つの入力が全部そろう日だけ出す。位置を測る本数が MIN_PERIODS に満たない日は出さない。

■ 帯（位置の温度の帯。相場の温度計の判定とは別の名前にする）
  0〜14 冷え込み ／ 15〜39 寒い ／ 40〜59 ふつう ／ 60〜100 暖かい
  （数字の境目は相場の温度計の帯と同じにして、℃ の境目 −9℃・11℃・27℃ をそろえる）
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from thermo100 import WINDOW, MIN_PERIODS, celsius, c_label  # noqa: F401  （℃ の換算は thermo100 の1つだけを使う）

POS_INPUTS = [
    ("ma200", "終値と200日線の差", "%"),
    ("ma50", "終値と50日線の差", "%"),
    ("chg22", "22営業日前比", "%"),
]

POS_BANDS = [
    {"key": "cold2", "jp": "冷え込み", "lo": 0, "hi": 14},
    {"key": "cold", "jp": "寒い", "lo": 15, "hi": 39},
    {"key": "mild", "jp": "ふつう", "lo": 40, "hi": 59},
    {"key": "warm", "jp": "暖かい", "lo": 60, "hi": 100},
]
POS_BANDS_C = [{"key": b["key"], "jp": b["jp"], "lo": celsius(b["lo"]), "hi": celsius(b["hi"])} for b in POS_BANDS]

NG_WORDS = ("買い", "売り", "推奨", "おすすめ", "オススメ", "必ず", "予想", "狙い目", "有望",
            "チャンス", "面白い", "過熱", "恐怖と強欲", "体温計")
# 「底」「天井」は断定に使わない（定義文には入れない）
NG_WORDS_STRICT = NG_WORDS + ("底", "天井")


def text_ng(s: str, strict: bool = True) -> list[str]:
    return [w for w in (NG_WORDS_STRICT if strict else NG_WORDS) if w in s]


def band_index(v: int) -> int:
    for i, b in enumerate(POS_BANDS):
        if b["lo"] <= v <= b["hi"]:
            return i
    raise ValueError(f"範囲外: {v}")


def fast_pct(s, window: int = WINDOW, min_periods: int = MIN_PERIODS):
    """thermo100.rolling_pct と同じ値（その日を含む過去 window 本の中で何%の位置か・0〜1）を速く出す。
    pandas の rolling().rank()（同じ値は平均の順位）を (順位−1)/(本数−1) に直す。
    Series でも DataFrame（列ごと）でもよい。欠損は窓から除く（rolling_pct と同じ）"""
    obj = s if isinstance(s, (pd.Series, pd.DataFrame)) else pd.Series(np.asarray(s, dtype=float))
    r = obj.rolling(window, min_periods=min_periods).rank(method="average", pct=False)
    n = obj.notna().astype(float).rolling(window, min_periods=1).sum()
    out = ((r - 1) / (n - 1)).where(n > 1, 0.5)
    return out.where(obj.notna() & (n >= min_periods))


def features(close: pd.Series) -> pd.DataFrame:
    """3つの入力（相場の温度計の日経平均の入力と同じ式）"""
    c = close.dropna()
    return pd.DataFrame({
        "ma200": (c / c.rolling(200).mean() - 1) * 100,
        "ma50": (c / c.rolling(50).mean() - 1) * 100,
        "chg22": (c / c.shift(21) - 1) * 100,
    })


def temp_series(close: pd.Series, window: int = WINDOW, min_periods: int = MIN_PERIODS) -> pd.DataFrame:
    """終値の Series → 日ごとの位置の温度。列: ma200/ma50/chg22（入力の生の値）・p_*（位置 0〜100）・
    t（温度 = 3つの位置の平均を四捨五入した 0〜100 の整数）・tc（℃）。温度が出ない日は行を落とす"""
    f = features(close)
    p = fast_pct(f, window, min_periods) * 100
    score = p.mean(axis=1, skipna=False)          # 3つそろった日だけ
    out = f.copy()
    for k, *_ in POS_INPUTS:
        out[f"p_{k}"] = p[k]
    out["t"] = score
    out = out.dropna(subset=["t"])
    out["t"] = out["t"].round().astype(int).clip(0, 100)
    out["tc"] = out["t"].map(celsius)
    return out


def temp_frame(closes: pd.DataFrame, window: int = WINDOW, min_periods: int = MIN_PERIODS) -> pd.DataFrame:
    """たくさんの銘柄（列）をまとめて計算し、温度（0〜100 の位置・小数）の DataFrame を返す。
    各列は欠損を除いた自分の日付で計算する（休場・上場前の日を窓に入れない）"""
    out = {}
    for col in closes.columns:
        c = closes[col].dropna()
        if len(c) < 200 + min_periods - 1:
            continue
        f = features(c)
        p = fast_pct(f, window, min_periods)
        out[col] = p.mean(axis=1, skipna=False) * 100
    return pd.DataFrame(out)


def clean_close(c: pd.Series, limit: float = 0.30) -> pd.Series:
    """Yahoo の桁違いの足（前後11本の中央値から30%以上離れた終値）を落とす（thermo100.topix_etf と同じ基準）"""
    c = c.dropna()
    med = c.rolling(11, center=True, min_periods=3).median()
    return c[~((c / med - 1).abs() > limit)]


def summary_row(df: pd.DataFrame) -> dict | None:
    """temp_series の結果 → 今日の値・前日比・5営業日前比・入力（JSON 用）"""
    if df is None or not len(df):
        return None
    last = df.iloc[-1]
    v = int(last["t"])

    def back(n):
        return int(df["t"].iloc[-1 - n]) if len(df) > n else None

    v1, v5, v20 = back(1), back(5), back(20)
    bi = band_index(v)
    return {
        "d": str(df.index[-1].date()),
        "value": v, "tc": celsius(v), "label": c_label(celsius(v)),
        "band": POS_BANDS[bi]["key"], "band_jp": POS_BANDS[bi]["jp"],
        "diff1_c": None if v1 is None else celsius(v) - celsius(v1),
        "diff5_c": None if v5 is None else celsius(v) - celsius(v5),
        "diff20_c": None if v20 is None else celsius(v) - celsius(v20),
        "parts": {k: {"raw": round(float(last[k]), 2), "pos": round(float(last[f"p_{k}"]), 1)} for k, *_ in POS_INPUTS},
        "since": str(df.index[0].date()),
    }
