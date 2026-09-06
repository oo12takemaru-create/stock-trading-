# -*- coding: utf-8 -*-
"""前計算列の計算ロジック。

設計書 12_前計算バッチと統計API_設計.md §3 の daily_metrics / market_condition を作る。
precompute_metrics.py と verify_against_engine.py の両方から呼ぶ（同じ定義を2回書かない）。

■ 列の定義と、既存エンジンのどこに対応するか
  dev_25/50/75/200 : (Close - MA_N) / MA_N * 100
                     エンジンの BNF 判定 (engine L1198) は dev_25 と同義。
  ma_50/150/200    : engine prepare_indicators L1349-1351 と同じ（単純移動平均）
  vol_ratio_25     : Volume / 25日平均出来高   ← 設計書 §3 の定義
  vol_ratio_20     : Volume / 20日平均出来高   ← ★エンジンが実際に使うのはこちら
                     engine/scanner の Vol20 = Volume.rolling(20).mean()。
                     BNF は vol >= Vol20*1.1、MOMENTUM は vol >= Vol20*1.5。
                     設計書 §3 は vol_ratio_25 しか持たないため、そのままでは
                     エンジンの判定を再現できない（検証レポートで指摘）。
  high_20/60/250   : 「当日を除く」過去N日の High の最大値。
                     engine L1298 / scanner L731 の prev_high = High[idx-20:idx].max()
                     （当日を含めるとブレイク判定が自己参照になるため除外が正しい）
  high_52w_ratio   : Close / (当日を含む過去253本の High 最大) * 100
                     engine L1068 の high_52w = High[idx-252:idx+1].max() と同じ窓
  rs_rank          : 126営業日リターンの、その日のユニバース内パーセンタイル(0-100)
                     エンジンは Trend Template で ret_6m = Close/Close[idx-126]-1 を
                     絶対値の閾値(+15% / LITE +5%)で見る。順位化は本設計の追加。
  turnover_25      : (Close * Volume) の25日平均（円）
  macd_cross       : MACD(12,26,9) のゴールデン=1 / デッド=-1 / なし=0（未検証タグ）
  bb_lower_1_5     : 20日BBの -1.5sigma。engine L1359 / scanner L570。BNF-LITE の必須条件
  bb_lower_2       : 20日BBの -2sigma。engine L1357。BNF(HIGH QUALITY)版が使う
  ret_5d/day_change: MOMENTUM の過熱フィルタ (engine L1318-1329 / scanner L780-789)
  open/high/low    : 設計書 §4 の出口モデル（翌営業日始値で約定・損切り判定）に必須。
                     §3 の列定義には無いが、無いと 1-C が作れないので入れてある。
  *_ratio / *_over_*: 「終値 <= BB下限」「高値 >= 20日高値」のような列同士の比較を、
                     条件ブロック方式（列・演算子・数値）で書けるようにした比率列。
                     いずれも 100（並びは 0）が境目。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import preset_signals
from config import HALT_RULES, REGIME_RULES

# 設計書 §3 に列挙されている列（この順で出力する）
SPEC_COLUMNS = [
    "date", "ticker",
    "close", "volume",
    "dev_25", "dev_50", "dev_75", "dev_200",
    "vol_ratio_25",
    "high_20", "high_60", "high_250", "high_52w_ratio",
    "ma_50", "ma_150", "ma_200",
    "rs_rank", "turnover_25", "macd_cross",
]

# 設計書 §3 には無いが、エンジン再現・出口計算に必要な追加列
EXTRA_COLUMNS = [
    "open", "high", "low",
    "ma_25", "ma_75",
    "vol_ratio_20", "vol_avg_20", "vol_avg_25",
    "bb_lower_1_5", "bb_lower_2",
    "ret_5d", "ret_126", "day_change",
    # 「列 vs 列」を条件ブロックで書けるようにする比率列（100 が境目）
    "high_20_ratio", "high_60_ratio", "high_250_ratio",
    "bb_pos_1_5", "ma_50_over_150", "ma_150_over_200",
    # 条件ブロックで書けない判定と、出口判定に使う位置関係
    "knife_guard", "minervini_entry", "ema_50_pos",
]

ALL_COLUMNS = SPEC_COLUMNS + EXTRA_COLUMNS

# ── Supabase に入れる列（2026-09-05 Fable判断・案③で絞り込み）──
# 無料枠 500MB の 59.2% まで使っていたので、
#   「他の列から復元できる中間値」と「比率列に置き換わった実値」を落とした。
#     volume            … 条件は vol_ratio_20 で書く
#     ma_25/50/75/150/200 … 上下は dev_*、並びは ma_*_over_* で書ける
#     high_20/60/250    … ブレイク判定は high_*_ratio（100が境目）で書ける
#     bb_lower_1_5/_2   … BNF の必須条件は bb_pos_1_5（100が境目）で書ける
#     vol_avg_20/25     … vol_ratio_* を作るための中間値
#     vol_ratio_25      … エンジンが実際に使うのは vol_ratio_20
# ローカルの CSV/Parquet には従来どおり全列を出す（engine_rules.py の照合に要る）。
# Supabase 側で real（float32）にしてある列。
#   ★重要★ Python 側でも同じ丸めを通さないと、境目の比較で判定が反転する。
#   実測: high_20_ratio が float64 で 99.99999681 の日が float32 では丸めて
#   ちょうど 100.0 になり、「20日高値を抜けたか」の判定が食い違った（9,322件中18件）。
#   値の正本は「DBに入っている値」なので、検証側を DB に合わせる。
REAL_COLUMNS = [
    "dev_25", "dev_50", "dev_75", "dev_200",
    "vol_ratio_20",
    "high_20_ratio", "high_60_ratio", "high_250_ratio",
    "ma_50_over_150", "ma_150_over_200",
    "high_52w_ratio", "rs_rank",
    "bb_pos_1_5",
    "ret_5d", "ret_126", "day_change",
    "ema_50_pos",
]

DB_COLUMNS = [
    "date", "ticker",
    # 約定計算に要る四本値
    "open", "high", "low", "close",
    # 材料1・4: 移動平均乖離率／移動平均の上下
    "dev_25", "dev_50", "dev_75", "dev_200",
    # 材料2: 出来高倍率
    "vol_ratio_20",
    # 材料3: N日高値ブレイク（100が境目）
    "high_20_ratio", "high_60_ratio", "high_250_ratio",
    # 材料5: 移動平均の並び（0が境目）
    "ma_50_over_150", "ma_150_over_200",
    # 材料6・7・8・10
    "high_52w_ratio", "rs_rank", "turnover_25", "macd_cross",
    # BNF の必須条件（100が境目）
    "bb_pos_1_5",
    # モメンタムの過熱フィルタ／RS の材料
    "ret_5d", "ret_126", "day_change",
    # 条件ブロックで書けない判定（真偽値）
    "knife_guard", "minervini_entry",
    # 出口判定に使う位置関係（100 が境目。25日線・75日線への戻りは dev_25/dev_75 >= 0 で見る）
    "ema_50_pos",
]


def _sma(s, n):
    return s.rolling(n).mean()


def compute_stock_metrics(df, ticker):
    """1銘柄のOHLCVから前計算列を作る。

    df: yfinance の日足（列 Open/High/Low/Close/Volume、index=DatetimeIndex）
    """
    out = pd.DataFrame(index=df.index)
    close = df["Close"].astype("float64")
    high = df["High"].astype("float64")
    low = df["Low"].astype("float64")
    open_ = df["Open"].astype("float64")
    vol = df["Volume"].astype("float64")

    out["ticker"] = ticker
    out["close"] = close
    out["open"] = open_
    out["high"] = high
    out["low"] = low
    out["volume"] = vol

    # -- 移動平均と乖離率 --
    ma25 = _sma(close, 25)
    ma50 = _sma(close, 50)
    ma75 = _sma(close, 75)
    ma150 = _sma(close, 150)
    ma200 = _sma(close, 200)
    out["ma_25"] = ma25
    out["ma_50"] = ma50
    out["ma_75"] = ma75
    out["ma_150"] = ma150
    out["ma_200"] = ma200
    out["dev_25"] = (close - ma25) / ma25 * 100
    out["dev_50"] = (close - ma50) / ma50 * 100
    out["dev_75"] = (close - ma75) / ma75 * 100
    out["dev_200"] = (close - ma200) / ma200 * 100

    # -- 出来高 --
    vol20 = _sma(vol, 20)   # エンジンの Vol20
    vol25 = _sma(vol, 25)   # 設計書 §3 の vol_ratio_25 用
    out["vol_avg_20"] = vol20
    out["vol_avg_25"] = vol25
    out["vol_ratio_20"] = vol / vol20.replace(0, np.nan)
    out["vol_ratio_25"] = vol / vol25.replace(0, np.nan)

    # -- N日高値（当日を除く）--
    prev_high = high.shift(1)
    out["high_20"] = prev_high.rolling(20).max()
    out["high_60"] = prev_high.rolling(60).max()
    out["high_250"] = prev_high.rolling(250).max()

    # -- 52週高値比（当日を含む253本。engine L1068 と同じ窓）--
    high_52w = high.rolling(253, min_periods=1).max()
    out["high_52w_ratio"] = close / high_52w * 100

    # -- ボリンジャーバンド（20日）--
    bb_mid = _sma(close, 20)
    bb_std = close.rolling(20).std()
    out["bb_lower_1_5"] = bb_mid - 1.5 * bb_std
    out["bb_lower_2"] = bb_mid - 2.0 * bb_std

    # -- 比率列（「列 vs 列」の条件を、条件ブロック方式の「列 vs 数値」で書けるようにする）--
    #    引き継ぎ書v2 §5.1 の「N日高値ブレイク（直下判定%）」「移動平均の並び」に対応する。
    #    100 が境目: high_20_ratio >= 100 なら20日高値を上抜け、97 なら高値の3%下。
    out["high_20_ratio"] = high / out["high_20"].replace(0, np.nan) * 100
    out["high_60_ratio"] = high / out["high_60"].replace(0, np.nan) * 100
    out["high_250_ratio"] = high / out["high_250"].replace(0, np.nan) * 100
    #    bb_pos_1_5 <= 100 が「終値がBB -1.5σ以下」（BNF の必須条件）
    out["bb_pos_1_5"] = close / out["bb_lower_1_5"].replace(0, np.nan) * 100
    #    移動平均の並び。0 より大きければ「短い方が上」
    out["ma_50_over_150"] = (ma50 / ma150 - 1) * 100
    out["ma_150_over_200"] = (ma150 / ma200 - 1) * 100

    # -- 売買代金 --
    out["turnover_25"] = (close * vol).rolling(25).mean()

    # -- MACD(12,26,9) ゴールデン/デッドクロス（未検証タグ）--
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    above = (macd > sig).fillna(False)
    prev_above = above.shift(1).fillna(False).astype(bool)
    cross = np.where(above.to_numpy() & ~prev_above.to_numpy(), 1,
                     np.where(~above.to_numpy() & prev_above.to_numpy(), -1, 0))
    out["macd_cross"] = pd.Series(cross, index=df.index).astype("int8")

    # -- リターン系（MOMENTUM の過熱フィルタ / RS の材料）--
    out["ret_5d"] = (close / close.shift(5) - 1) * 100
    out["ret_126"] = (close / close.shift(126) - 1) * 100
    out["day_change"] = (close / close.shift(1) - 1) * 100

    # 条件ブロックで書けない判定（ナイフガード・ミネルヴィニの入り口）と、
    # 出口判定に使う位置関係の列。preset_signals.py 参照。
    extra = preset_signals.compute(df)
    for c in preset_signals.COLUMNS:
        out[c] = extra[c]

    out["rs_rank"] = np.nan  # 横断計算なので panel 完成後に埋める
    return out


def round_to_db_precision(panel):
    """DB（real 列）と同じ精度に丸める。REAL_COLUMNS のコメント参照。"""
    for c in REAL_COLUMNS:
        if c in panel.columns:
            panel[c] = panel[c].astype("float32").astype("float64")
    return panel


def add_rs_rank(panel):
    """rs_rank = その日のユニバース内での ret_126 のパーセンタイル順位(0-100)。"""
    panel = panel.copy()
    panel["rs_rank"] = panel.groupby("date")["ret_126"].rank(pct=True) * 100
    return panel


# ==================================================================
#  market_condition（相場環境）
#  移植元: integrated_backtest_v2_8_0.py detect_market_regime L885-940
#          / check_halt_conditions L946 / precompute_halt_only_states L973
#  閾値はすべて thresholds.json に切り出し済み
# ==================================================================

def _align(series, dates):
    """各日 d について「index <= d の最後の値」を返す。
    エンジンの df[df.index <= date].iloc[-1] と同じ意味（ffill）。"""
    s = series[~series.index.duplicated(keep="last")].sort_index()
    idx = s.index.union(pd.DatetimeIndex(dates))
    return s.reindex(idx).ffill().reindex(pd.DatetimeIndex(dates))


def compute_market_condition(global_data, dates, panel=None):
    """日本市場の営業日 dates について相場環境を判定する。"""
    r = REGIME_RULES
    dates = pd.DatetimeIndex(dates)
    n225 = global_data.get("^N225")
    gspc = global_data.get("^GSPC")
    vixd = global_data.get("^VIX")

    md = pd.DataFrame(index=dates)
    md.index.name = "date"

    # -- 日経225 --
    if n225 is not None and len(n225):
        c = n225["Close"].astype("float64")
        md["nikkei_close"] = _align(c, dates)
        md["nikkei_ma_25"] = _align(c.rolling(25).mean(), dates)
        md["nikkei_ma_50"] = _align(c.rolling(int(r["n225_ma_short"])).mean(), dates)
        md["nikkei_ma_75"] = _align(c.rolling(75).mean(), dates)
        md["nikkei_ma_200"] = _align(c.rolling(int(r["n225_ma_long"])).mean(), dates)
        # engine L897: change_1m = close / Close[-22] - 1
        md["nikkei_1m_change"] = _align(
            (c / c.shift(int(r["n225_1m_lookback_bars"])) - 1) * 100, dates)
        # 十分な履歴が無い日は engine の len(df_sub) >= 200 ガードに相当させる
        bars = _align(pd.Series(np.arange(1, len(c) + 1), index=c.index), dates)
        md["_n225_enough"] = (bars >= int(r["n225_ma_long"])).fillna(False)
    else:
        for col in ("nikkei_close", "nikkei_ma_25", "nikkei_ma_50", "nikkei_ma_75",
                    "nikkei_ma_200", "nikkei_1m_change"):
            md[col] = np.nan
        md["_n225_enough"] = False

    md["nikkei_dev_25"] = (md["nikkei_close"] - md["nikkei_ma_25"]) / md["nikkei_ma_25"] * 100
    md["nikkei_dev_75"] = (md["nikkei_close"] - md["nikkei_ma_75"]) / md["nikkei_ma_75"] * 100

    # -- S&P500 --
    if gspc is not None and len(gspc):
        c = gspc["Close"].astype("float64")
        md["sp500_close"] = _align(c, dates)
        md["sp500_ma_200"] = _align(c.rolling(int(r["sp500_ma_long"])).mean(), dates)
        md["sp500_change_1d"] = _align((c / c.shift(1) - 1) * 100, dates)
        md["sp500_change_3d"] = _align((c / c.shift(3) - 1) * 100, dates)
        bars = _align(pd.Series(np.arange(1, len(c) + 1), index=c.index), dates)
        md["_sp_enough"] = (bars >= int(r["sp500_ma_long"])).fillna(False)
    else:
        for col in ("sp500_close", "sp500_ma_200", "sp500_change_1d", "sp500_change_3d"):
            md[col] = np.nan
        md["_sp_enough"] = False

    # -- VIX --
    if vixd is not None and len(vixd):
        md["vix"] = _align(vixd["Close"].astype("float64"), dates)
    else:
        md["vix"] = np.nan

    # -- 4段階判定（engine L920-940 をそのまま）--
    vix = md["vix"].fillna(float(r["vix_default_when_missing"]))
    chg = md["nikkei_1m_change"].fillna(0.0)
    enough_n = md["_n225_enough"].astype(bool)
    enough_s = md["_sp_enough"].astype(bool)
    above200 = (md["nikkei_close"] > md["nikkei_ma_200"]).fillna(False) & enough_n
    above50 = (md["nikkei_close"] > md["nikkei_ma_50"]).fillna(False) & enough_n
    sp_above200 = (md["sp500_close"] > md["sp500_ma_200"]).fillna(False) & enough_s
    # engine: signals.get("n225_above_200ma", True) -> 履歴不足なら「上」とみなす
    above200_for_bearish = above200.where(enough_n, True)

    regime = pd.Series("NEUTRAL", index=dates, dtype=object)
    is_bullish = (above200 & above50 & sp_above200
                  & (vix < float(r["bullish_vix_lt"]))
                  & (chg > float(r["bullish_n225_1m_change_gt"])))
    regime[is_bullish.to_numpy()] = "BULLISH"
    is_bearish = (~above200_for_bearish) | (vix > float(r["bearish_vix_gt"]))
    regime[is_bearish.to_numpy()] = "BEARISH"
    # engine は PANIC -> BEARISH -> BULLISH の順に return するので PANIC が最優先
    is_panic = ((vix > float(r["panic_vix_gt"]))
                & (chg < float(r["panic_n225_1m_change_lt"])))
    regime[is_panic.to_numpy()] = "PANIC"
    md["regime_engine"] = regime.to_numpy()

    # -- ★同じリポジトリの中で regime の実装が2つあり、判定が食い違う --
    #   engine  integrated_backtest_v2_8_0.py L920-940
    #       BULLISH = 日経>200MA かつ 日経>50MA かつ S&P>200MA かつ VIX<20 かつ 1ヶ月変化>0
    #   scanner daily_scanner_v2_8_0.py L646-649   ← signals_log.csv を書いている側
    #       BULLISH = 日経>200MA かつ S&P>200MA かつ VIX<20   （50MA条件と1ヶ月変化条件が無い）
    #   PANIC / BEARISH の条件は両者同じ。
    #   決着（1-B・2026-09-05）: 起動文 §1「現行スキャナーの地合い判定を全期間ぶん再計算」と
    #   引き継ぎ書v2 §3.1「環境判定ロジックは現行scannerのMARKET REGIME判定を流用」に従い、
    #   ★scanner 版を regime（正本）とする。engine 版は regime_engine として残す。
    regime_s = pd.Series("NEUTRAL", index=dates, dtype=object)
    s_bull = (above200 & sp_above200 & (vix < float(r["bullish_vix_lt"])))
    regime_s[s_bull.to_numpy()] = "BULLISH"
    regime_s[is_bearish.to_numpy()] = "BEARISH"
    regime_s[is_panic.to_numpy()] = "PANIC"
    md["regime"] = regime_s.to_numpy()   # ★正本。起動文 §1 が指す「現行スキャナーの地合い判定」

    # -- HALT（engine check_halt_conditions + precompute_halt_only_states）--
    hv = float(HALT_RULES["halt_vix"])
    hd = float(HALT_RULES["halt_n225_drop"])
    cooldown = int(HALT_RULES["halt_cooldown"])
    raw_vix_halt = (md["vix"] > hv).fillna(False)
    raw_n225_halt = (md["nikkei_1m_change"] < -hd).fillna(False)
    raw_halt = raw_vix_halt | raw_n225_halt

    states, reasons = [], []
    state, reason, clear = "NORMAL", None, 0
    vix_vals = md["vix"].to_numpy()
    chg_vals = md["nikkei_1m_change"].to_numpy()
    rv = raw_vix_halt.to_numpy()
    rh = raw_halt.to_numpy()
    for i in range(len(dates)):
        if rh[i]:
            state, clear = "HALT", 0
            if rv[i]:
                reason = "VIX=%.1f > %.0f（極度のパニック）" % (vix_vals[i], hv)
            else:
                reason = "日経1ヶ月%.1f%%下落（急落）" % chg_vals[i]
        else:
            if state == "HALT":
                clear += 1
                if clear >= cooldown:
                    state, reason = "NORMAL", None
        states.append(state)
        reasons.append(reason)
    md["is_halt"] = [s == "HALT" for s in states]
    md["halt_reason"] = reasons

    # -- breadth（ユニバース238銘柄から算出。市場全体ではない点に注意）--
    if panel is not None and len(panel):
        p = panel[["date", "ticker", "close", "day_change", "high_52w_ratio"]].copy()
        p["date"] = pd.to_datetime(p["date"])
        adv = p.assign(v=(p["day_change"] > 0).astype("int32")).groupby("date")["v"].sum()
        dec = p.assign(v=(p["day_change"] < 0).astype("int32")).groupby("date")["v"].sum()
        adv25 = adv.rolling(25).sum()
        dec25 = dec.rolling(25).sum()
        md["breadth_ratio"] = _align(adv25 / dec25.replace(0, np.nan) * 100, dates)
        md["advancing"] = _align(adv.astype("float64"), dates)
        md["declining"] = _align(dec.astype("float64"), dates)
        nh = p.assign(v=(p["high_52w_ratio"] >= 99.999).astype("int32")).groupby("date")["v"].sum()
        p = p.sort_values(["ticker", "date"])
        low253 = p.groupby("ticker")["close"].transform(
            lambda s: s.rolling(253, min_periods=253).min())
        p["_at_low"] = (p["close"] <= low253 * 1.00001).fillna(False)
        nl = p.assign(v=p["_at_low"].astype("int32")).groupby("date")["v"].sum()
        md["new_high"] = _align(nh.astype("float64"), dates)
        md["new_low"] = _align(nl.astype("float64"), dates)
    else:
        for col in ("breadth_ratio", "advancing", "declining", "new_high", "new_low"):
            md[col] = np.nan

    md = md.drop(columns=[c for c in md.columns if c.startswith("_")])
    return md.reset_index()


MARKET_COLUMNS = [
    "date", "regime", "regime_engine", "nikkei_close", "nikkei_dev_25",
    "nikkei_dev_75", "vix",
    "is_halt", "halt_reason", "breadth_ratio", "new_high", "new_low",
    # 追加列（設計書 §3 には無いが MOMENTUM の米国フィルタ再現などに必要）
    "nikkei_ma_25", "nikkei_ma_50", "nikkei_ma_75", "nikkei_ma_200", "nikkei_1m_change",
    "sp500_close", "sp500_ma_200", "sp500_change_1d", "sp500_change_3d",
    "advancing", "declining",
]
