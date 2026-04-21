"""
================================================================================
 デイリースキャナー v2.7.3 (GitHub Actions対応版)
 BNF + Minervini + MOMENTUM 統合戦略 - 実運用シグナル生成ツール
================================================================================

機能:
  - マーケット環境判定 (BULLISH / NEUTRAL / BEARISH / PANIC)
  - サーキットブレーカー判定 (VIX>35 / 日経-15% / 5連敗)
  - v2.7.3 実証閾値 × 地合い倍率でBNF-LITEシグナル生成
  - Minervini (Trend Template + VCP + MA50押し目) シグナル生成
  - MOMENTUM (20日高値ブレイク) シグナル生成
  - Markdown形式レポート出力 (GitHub Issues / Actions Summary対応)

実行:
  python daily_scanner_v2_7_3.py
  python daily_scanner_v2_7_3.py --capital 1000000 --output signal.md
  python daily_scanner_v2_7_3.py --output signal.md --concise

GitHub Actions自動実行:
  朝  8:00 JST, 昼 12:00 JST, 晩 18:00 JST
  出力は GitHub Issue として自動投稿 (メール通知される)

資金計算:
  推奨株数 = (capital * risk_pct/100) / (entry_price - stop_price)
  PANIC時のBNF-LITEは panic_risk_mult(デフォルト0.5) でリスク半減

================================================================================
"""

from __future__ import annotations

import argparse
import os
import sys
import json
import warnings
from datetime import datetime, timedelta
from pathlib import Path

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError:
    print("エラー: yfinance pandas numpy が必要です")
    print("  pip install yfinance pandas numpy")
    sys.exit(1)

warnings.filterwarnings("ignore")

# ============================================================================
# 銘柄リスト (v2.7.3と同期)
# ============================================================================

STOCKS = {
    # 半導体・精密
    "8035.T": ("東京エレクトロン", "半導体製造装置"),
    "6857.T": ("アドバンテスト", "半導体製造装置"),
    "6146.T": ("ディスコ", "半導体製造装置"),
    "6920.T": ("レーザーテック", "半導体製造装置"),
    "7735.T": ("SCREEN HD", "半導体製造装置"),
    "6728.T": ("アルバック", "半導体製造装置"),
    "6323.T": ("ローツェ", "半導体製造装置"),
    "6758.T": ("ソニー", "電機"),
    "6861.T": ("キーエンス", "FAセンサー"),
    "6273.T": ("SMC", "空圧制御"),
    "6981.T": ("村田製作所", "電子部品"),
    "6762.T": ("TDK", "電子部品"),
    "6971.T": ("京セラ", "電子部品"),
    "6954.T": ("ファナック", "産業用ロボット"),
    "7011.T": ("三菱重工", "重工業"),
    "7012.T": ("川崎重工", "重工業"),
    "7013.T": ("IHI", "重工業"),
    "6301.T": ("コマツ", "建機"),
    "6326.T": ("クボタ", "農機"),
    "6367.T": ("ダイキン", "空調"),
    "6594.T": ("ニデック", "モーター"),
    "6501.T": ("日立製作所", "総合電機"),
    "6503.T": ("三菱電機", "総合電機"),
    "6506.T": ("安川電機", "モーター"),
    "6902.T": ("デンソー", "自動車部品"),

    # ゲーム・エンタメ
    "7974.T": ("任天堂", "ゲーム"),
    "9684.T": ("スクウェア・エニックス", "ゲーム"),
    "9697.T": ("カプコン", "ゲーム"),
    "9766.T": ("コナミ", "ゲーム"),
    "3659.T": ("ネクソン", "ゲーム"),
    "6460.T": ("セガサミーHD", "ゲーム"),

    # IT・グロース
    "4307.T": ("野村総研", "ITサービス"),
    "4063.T": ("信越化学", "化学"),
    "4568.T": ("第一三共", "医薬品"),
    "4519.T": ("中外製薬", "医薬品"),
    "4523.T": ("エーザイ", "医薬品"),
    "4578.T": ("大塚HD", "医薬品"),
    "2413.T": ("エムスリー", "医療IT"),
    "4751.T": ("サイバーエージェント", "広告IT"),
    "4385.T": ("メルカリ", "フリマEC"),
    "4478.T": ("フリー", "SaaS"),
    "4704.T": ("トレンドマイクロ", "セキュリティ"),

    # 小売
    "9843.T": ("ニトリHD", "小売"),
    "3382.T": ("セブン&アイ", "小売"),
    "9983.T": ("ファーストリテイリング", "小売"),
    "7532.T": ("パンパシHD", "小売"),
    "8227.T": ("しまむら", "小売"),
    "2670.T": ("ABCマート", "小売"),
    "3092.T": ("ZOZO", "ECファッション"),
    "3088.T": ("マツキヨココカラ", "ドラッグ"),
    "9832.T": ("オートバックス", "カー用品"),
    "3048.T": ("ビックカメラ", "家電量販"),

    # インバウンド・旅行
    "9201.T": ("日本航空", "空運"),
    "9202.T": ("ANA", "空運"),
    "9020.T": ("JR東日本", "鉄道"),
    "9021.T": ("JR西日本", "鉄道"),
    "9022.T": ("JR東海", "鉄道"),
    "4661.T": ("オリエンタルランド", "レジャー"),
    "9602.T": ("東宝", "映画"),

    # 通信・商社
    "9432.T": ("NTT", "通信"),
    "9433.T": ("KDDI", "通信"),
    "9434.T": ("ソフトバンク", "通信"),
    "9984.T": ("ソフトバンクG", "投資会社"),
    "8058.T": ("三菱商事", "商社"),
    "8031.T": ("三井物産", "商社"),
    "8001.T": ("伊藤忠商事", "商社"),
    "8002.T": ("丸紅", "商社"),
    "8053.T": ("住友商事", "商社"),
    "2768.T": ("双日", "商社"),

    # 自動車
    "7203.T": ("トヨタ自動車", "自動車"),
    "7267.T": ("ホンダ", "自動車"),
    "7201.T": ("日産自動車", "自動車"),
    "7269.T": ("スズキ", "自動車"),
    "7261.T": ("マツダ", "自動車"),
    "7270.T": ("SUBARU", "自動車"),

    # 素材・化学
    "4005.T": ("住友化学", "化学"),
    "4188.T": ("三菱ケミカル", "化学"),
    "4042.T": ("東ソー", "化学"),
    "4452.T": ("花王", "化学"),
    "4901.T": ("富士フイルム", "化学"),
    "3402.T": ("東レ", "化学"),
    "4183.T": ("三井化学", "化学"),
    "4204.T": ("積水化学", "化学"),
    "5019.T": ("出光興産", "石油"),
    "5020.T": ("ENEOS", "石油"),
    "5411.T": ("JFE", "鉄鋼"),
    "5401.T": ("日本製鉄", "鉄鋼"),
    "5713.T": ("住友金属鉱山", "非鉄"),
    "5802.T": ("住友電工", "電線"),

    # 食品
    "2502.T": ("アサヒGHD", "飲料"),
    "2503.T": ("キリンHD", "飲料"),
    "2802.T": ("味の素", "食品"),
    "2914.T": ("JT", "たばこ"),
    "2897.T": ("日清食品HD", "食品"),
    "2801.T": ("キッコーマン", "食品"),
    "2269.T": ("明治HD", "食品"),

    # 海運・不動産
    "9101.T": ("日本郵船", "海運"),
    "9104.T": ("商船三井", "海運"),
    "9107.T": ("川崎汽船", "海運"),
    "8802.T": ("三菱地所", "不動産"),
    "8801.T": ("三井不動産", "不動産"),
    "8830.T": ("住友不動産", "不動産"),
    "3003.T": ("ヒューリック", "不動産"),

    # 銀行・証券・保険
    "8306.T": ("三菱UFJ", "銀行"),
    "8316.T": ("三井住友FG", "銀行"),
    "8411.T": ("みずほFG", "銀行"),
    "8591.T": ("オリックス", "リース"),
    "8473.T": ("SBIホールディングス", "証券"),
    "8604.T": ("野村HD", "証券"),
    "8766.T": ("東京海上", "保険"),
    "8750.T": ("第一生命HD", "保険"),

    # 追加の主要銘柄
    "8267.T": ("イオン", "小売"),
    "8113.T": ("ユニ・チャーム", "日用品"),
    "4502.T": ("武田薬品", "医薬品"),
    "4503.T": ("アステラス製薬", "医薬品"),
    "3436.T": ("SUMCO", "半導体"),
    "6976.T": ("太陽誘電", "電子部品"),
    "6701.T": ("NEC", "電機"),
    "6702.T": ("富士通", "電機"),
    "6098.T": ("リクルートHD", "人材"),
    "4755.T": ("楽天グループ", "IT"),
    "9735.T": ("セコム", "警備"),
    "9064.T": ("ヤマトHD", "物流"),
    "9503.T": ("関西電力", "電力"),
    "9501.T": ("東京電力HD", "電力"),
    "9531.T": ("東京ガス", "ガス"),
    "1605.T": ("INPEX", "石油"),
    "5108.T": ("ブリヂストン", "タイヤ"),

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★v2.7.4: 新規追加35本(ticker_verifier.pyで疎通確認・流動性5億円/日以上)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # SaaS系(6本)
    "3923.T": ("ラクス", "SaaS"),
    "4776.T": ("サイボウズ", "SaaS"),
    "3994.T": ("マネーフォワード", "SaaS"),
    "4443.T": ("Sansan", "SaaS"),
    "4194.T": ("ビジョナル", "SaaS"),

    # バイオ・医療機器(5本)
    "4587.T": ("ペプチドリーム", "バイオ"),
    "4592.T": ("サンバイオ", "バイオ"),
    "2160.T": ("GNI", "バイオ"),
    "6869.T": ("シスメックス", "医療機器"),
    "4543.T": ("テルモ", "医療機器"),

    # 半導体関連(3本) ※ディスコ/SCREEN HD/アルバックは既存のため除外
    "4186.T": ("東京応化工業", "半導体"),
    "6723.T": ("ルネサスエレクトロニクス", "半導体"),
    "6525.T": ("KOKUSAI ELECTRIC", "半導体製造装置"),

    # IT・コンサル(4本)
    "4689.T": ("LINEヤフー", "IT"),
    "9449.T": ("GMOインターネット", "IT"),
    "6532.T": ("ベイカレント", "コンサル"),
    "4324.T": ("電通グループ", "広告"),

    # 機械・電子部品(3本)
    "6324.T": ("ハーモニックドライブ", "産業用ロボット"),
    "6481.T": ("THK", "機械"),
    "6479.T": ("ミネベアミツミ", "電子部品"),

    # エンタメ・ゲーム(5本)
    "7832.T": ("バンダイナムコHD", "エンタメ"),
    "8136.T": ("サンリオ", "エンタメ"),
    "4816.T": ("東映アニメーション", "エンタメ"),
    "3635.T": ("コーエーテクモ", "ゲーム"),
    "2121.T": ("MIXI", "ゲーム"),

    # 金融(4本)
    "8593.T": ("三菱HCキャピタル", "リース"),
    "8698.T": ("マネックスG", "証券"),
    "7186.T": ("コンコルディアFG", "銀行"),
    "8572.T": ("アコム", "消費者金融"),

    # 不動産・小売・その他(3本)
    "3038.T": ("神戸物産", "食品小売"),
    "8804.T": ("東京建物", "不動産"),
    "8876.T": ("リログループ", "不動産サービス"),
}

GLOBAL_TICKERS = {
    "^N225":  "日経225",
    "^GSPC":  "S&P500",
    "^VIX":   "VIX恐怖指数",
}

# ============================================================================
# v2.7.3 実証最適閾値
# ============================================================================

SECTOR_BNF_THRESHOLDS = {
    "ITサービス":  -8.0,  "カー用品": -8.0,  "タイヤ": -8.0,
    "レジャー": -8.0,  "保険": -8.0,  "家電量販": -8.0,
    "映画": -8.0,  "空圧制御": -8.0,  "空調": -8.0,  "重工業": -8.0,
    "ゲーム": -10.0,  "モーター": -10.0,  "リース": -10.0,
    "広告IT": -10.0,  "物流": -10.0,  "空運": -10.0,
    "自動車部品": -10.0,  "通信": -10.0,  "食品": -10.0,
    "飲料": -10.0,  "たばこ": -10.0,  "日用品": -10.0,
    "ECファッション": -12.0,  "IT": -12.0,  "ドラッグ": -12.0,
    "化学": -12.0,  "産業用ロボット": -12.0,  "石油": -12.0,
    "小売": -12.0,  "鉄道": -12.0,  "警備": -12.0,
    "医薬品": -15.0,  "SaaS": -15.0,  "医療IT": -15.0,
    "総合電機": -15.0,  "建機": -15.0,
    "フリマEC": -18.0,  "人材": -18.0,
    "投資会社": -18.0,  "電機": -18.0,
    "半導体製造装置": -22.0,  "商社": -22.0,  "銀行": -22.0,
    "電子部品": -22.0,  "自動車": -22.0,
    "半導体": -15.0,  "セキュリティ": -15.0,  "証券": -12.0,
    "鉄鋼": -15.0,  "非鉄": -15.0,  "電線": -15.0,
    "電力": -12.0,  "ガス": -12.0,  "不動産": -15.0,
    "農機": -15.0,  "海運": -18.0,  "半導体シリコン": -15.0,
    # ★v2.7.4: 新規セクター
    "エンタメ": -10.0,       # ゲームと類似
    "コンサル": -8.0,        # ITサービスと類似
    "医療機器": -12.0,       # 医薬より浅め、安定性高
    "広告": -10.0,           # 広告ITと同じ
    "食品小売": -12.0,       # 小売と同じ
    "機械": -12.0,           # 産業用ロボットと同じ
    "バイオ": -18.0,         # 高変動グロース
    "不動産サービス": -15.0,
    "消費者金融": -12.0,
    "default": -15.0,
}

# v2.7.3 適正化済み地合い倍率
REGIME_BNF_MULTIPLIER = {
    "BULLISH": 0.8,
    "NEUTRAL": 1.0,
    "BEARISH": 1.15,
    "PANIC":   1.3,
}


def get_bnf_threshold(sector, regime):
    base = SECTOR_BNF_THRESHOLDS.get(sector, SECTOR_BNF_THRESHOLDS["default"])
    mult = REGIME_BNF_MULTIPLIER.get(regime, 1.0)
    return base * mult


# ============================================================================
# テクニカル指標
# ============================================================================

def prepare_indicators(df):
    df = df.copy()
    df["MA25"] = df["Close"].rolling(25).mean()
    df["MA50"] = df["Close"].rolling(50).mean()
    df["MA150"] = df["Close"].rolling(150).mean()
    df["MA200"] = df["Close"].rolling(200).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    df["Vol20"] = df["Volume"].rolling(20).mean()
    df["BB_mid"] = df["Close"].rolling(20).mean()
    df["BB_std"] = df["Close"].rolling(20).std()
    df["BB_lower_1_5"] = df["BB_mid"] - 1.5 * df["BB_std"]
    # ATR
    high_low = df["High"] - df["Low"]
    high_close = (df["High"] - df["Close"].shift()).abs()
    low_close = (df["Low"] - df["Close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["ATR20"] = tr.rolling(20).mean()
    return df


# ============================================================================
# マーケット環境判定 (v2.7.3 と同じ)
# ============================================================================

def detect_market_regime(global_data, date):
    """BULLISH / NEUTRAL / BEARISH / PANIC の4環境判定"""
    try:
        n225 = global_data.get("^N225")
        sp500 = global_data.get("^GSPC")
        vix = global_data.get("^VIX")

        if n225 is None or sp500 is None or vix is None:
            return "NEUTRAL", "データ不足"

        n225_today = None
        sp500_today = None
        vix_today = None

        for df in [n225]:
            idx = df.index.searchsorted(pd.Timestamp(date))
            if idx > 0:
                n225_today = df.iloc[idx-1] if idx >= len(df) else df.iloc[idx]
                break
        for df in [sp500]:
            idx = df.index.searchsorted(pd.Timestamp(date))
            if idx > 0:
                sp500_today = df.iloc[idx-1] if idx >= len(df) else df.iloc[idx]
                break
        for df in [vix]:
            idx = df.index.searchsorted(pd.Timestamp(date))
            if idx > 0:
                vix_today = df.iloc[idx-1] if idx >= len(df) else df.iloc[idx]
                break

        if n225_today is None or sp500_today is None or vix_today is None:
            return "NEUTRAL", "データ不足"

        n225_close = float(n225_today["Close"])
        sp500_close = float(sp500_today["Close"])
        vix_close = float(vix_today["Close"])

        # 200日MAとの関係
        n225_ma200 = n225["Close"].rolling(200).mean()
        sp500_ma200 = sp500["Close"].rolling(200).mean()

        n225_ma_idx = n225_ma200.index.searchsorted(pd.Timestamp(date))
        sp_ma_idx = sp500_ma200.index.searchsorted(pd.Timestamp(date))

        n225_ma = float(n225_ma200.iloc[min(n225_ma_idx, len(n225_ma200)-1)])
        sp500_ma = float(sp500_ma200.iloc[min(sp_ma_idx, len(sp500_ma200)-1)])

        # 1ヶ月変化率
        n225_1m_idx = n225.index.searchsorted(pd.Timestamp(date) - timedelta(days=30))
        if n225_1m_idx < len(n225):
            n225_1m_ago = float(n225["Close"].iloc[n225_1m_idx])
            n225_1m_change = (n225_close - n225_1m_ago) / n225_1m_ago * 100
        else:
            n225_1m_change = 0

        # PANIC判定
        if vix_close > 30 and n225_1m_change < -10:
            return "PANIC", f"VIX={vix_close:.1f} & 日経1ヶ月{n225_1m_change:+.1f}%"

        # BEARISH判定
        if n225_close < n225_ma or vix_close > 25:
            return "BEARISH", f"日経<200MA or VIX={vix_close:.1f}"

        # BULLISH判定
        if n225_close > n225_ma and sp500_close > sp500_ma and vix_close < 20:
            return "BULLISH", f"日経・S&P両方>200MA, VIX={vix_close:.1f}"

        return "NEUTRAL", f"中立"

    except Exception as e:
        return "NEUTRAL", f"判定エラー: {e}"


# ============================================================================
# サーキットブレーカー判定
# ============================================================================

def check_halt_conditions(global_data, date, halt_vix=35.0, halt_n225_drop=15.0):
    """HALT発動判定 (VIX>35 or 日経1ヶ月-15%)"""
    try:
        vix = global_data.get("^VIX")
        n225 = global_data.get("^N225")
        if vix is None or n225 is None:
            return False, None

        vix_idx = vix.index.searchsorted(pd.Timestamp(date))
        if vix_idx >= len(vix):
            vix_idx = len(vix) - 1
        vix_close = float(vix["Close"].iloc[vix_idx])

        if vix_close > halt_vix:
            return True, f"VIX={vix_close:.1f} > {halt_vix}(極度のパニック)"

        # 日経1ヶ月変化率
        n225_idx = n225.index.searchsorted(pd.Timestamp(date))
        if n225_idx >= len(n225):
            n225_idx = len(n225) - 1
        n225_today = float(n225["Close"].iloc[n225_idx])

        n225_1m_idx = n225.index.searchsorted(pd.Timestamp(date) - timedelta(days=30))
        if n225_1m_idx < len(n225):
            n225_1m_ago = float(n225["Close"].iloc[n225_1m_idx])
            change = (n225_today - n225_1m_ago) / n225_1m_ago * 100
            if change < -halt_n225_drop:
                return True, f"日経1ヶ月{change:+.1f}%下落(急落)"

        return False, None
    except Exception:
        return False, None


# ============================================================================
# BNF-LITE シグナル検出 (v2.7.3 実証閾値)
# ============================================================================

def check_bnf_signal(df, sector, regime):
    if len(df) < 25:
        return False, None

    idx = len(df) - 1
    close = float(df["Close"].iloc[idx])
    ma25 = df["MA25"].iloc[idx]
    vol = df["Volume"].iloc[idx]
    vol_avg = df["Vol20"].iloc[idx]

    if pd.isna(ma25) or pd.isna(vol_avg):
        return False, None

    deviation = (close - ma25) / ma25 * 100
    threshold = get_bnf_threshold(sector, regime)
    if deviation > threshold:
        return False, None

    if vol < vol_avg * 1.1:
        return False, None

    bb_check = df["BB_lower_1_5"].iloc[idx]
    if pd.isna(bb_check) or close > bb_check:
        return False, None

    return True, {
        "entry": close,
        "stop": close * 0.95,  # -5%
        "target": ma25,  # 25日MA復帰
        "hold_days": 14,
        "deviation": deviation,
        "threshold": threshold,
    }


# ============================================================================
# MOMENTUM シグナル検出
# ============================================================================

def check_momentum_signal(df):
    if len(df) < 200:
        return False, None

    idx = len(df) - 1
    close = float(df["Close"].iloc[idx])
    high = float(df["High"].iloc[idx])
    ma50 = df["MA50"].iloc[idx]
    ma200 = df["MA200"].iloc[idx]
    vol = df["Volume"].iloc[idx]
    vol_avg = df["Vol20"].iloc[idx]

    if pd.isna(ma50) or pd.isna(ma200) or pd.isna(vol_avg):
        return False, None

    if close < ma200 or close < ma50:
        return False, None

    prev_high = df["High"].iloc[max(0, idx-20):idx].max()
    if pd.isna(prev_high) or prev_high == 0:
        return False, None

    if high < prev_high:
        return False, None

    if vol < vol_avg * 1.5:
        return False, None

    return True, {
        "entry": prev_high,
        "stop": prev_high * 0.95,
        "target": prev_high * 1.10,
        "hold_days": 10,
        "pivot": prev_high,
    }


# ============================================================================
# MINERVINI Trend Template + VCP/MA50押し目
# ============================================================================

def check_trend_template(df):
    if len(df) < 200:
        return False
    idx = len(df) - 1
    close = float(df["Close"].iloc[idx])
    ma50 = df["MA50"].iloc[idx]
    ma150 = df["MA150"].iloc[idx]
    ma200 = df["MA200"].iloc[idx]

    if pd.isna(ma50) or pd.isna(ma150) or pd.isna(ma200):
        return False
    if not (close > ma150 and close > ma200):
        return False
    if not (ma150 > ma200):
        return False
    # MA200上昇
    if idx >= 20:
        ma200_20ago = df["MA200"].iloc[idx-20]
        if pd.isna(ma200_20ago) or ma200 <= ma200_20ago:
            return False
    if not (ma50 > ma150 > ma200):
        return False
    if close <= ma50:
        return False
    low_52w = df["Low"].iloc[max(0, idx-252):idx+1].min()
    if close < low_52w * 1.25:
        return False
    high_52w = df["High"].iloc[max(0, idx-252):idx+1].max()
    if close < high_52w * 0.75:
        return False
    if idx >= 126:
        ret_6m = (close / float(df["Close"].iloc[idx-126]) - 1) * 100
        if ret_6m < 15:
            return False
    return True


def detect_vcp(df, lookback=60):
    idx = len(df) - 1
    if idx < lookback:
        return False, None

    window = df.iloc[idx-lookback:idx+1].copy()
    atr_recent = window["ATR20"].iloc[-5:].mean()
    atr_past = window["ATR20"].iloc[:10].mean()

    if pd.isna(atr_recent) or pd.isna(atr_past) or atr_past == 0:
        return False, None

    if atr_recent > atr_past * 0.90:  # v2.5+ 緩和
        return False, None

    pivot = window["High"].iloc[:-1].max()
    current_close = window["Close"].iloc[-1]

    if current_close < pivot * 0.93 or current_close > pivot * 1.10:
        return False, None

    ranges = []
    for i in range(0, lookback - 10, 10):
        high = window["High"].iloc[i:i+10].max()
        low = window["Low"].iloc[i:i+10].min()
        if high > 0:
            ranges.append((high - low) / high * 100)

    if len(ranges) < 3:
        return False, None

    last_range = ranges[-1]
    if last_range > 15:
        return False, None

    first_half_avg = sum(ranges[:len(ranges)//2]) / max(1, len(ranges)//2)
    if last_range >= first_half_avg * 0.85:
        return False, None

    return True, pivot


def detect_ma_pullback(df):
    idx = len(df) - 1
    if idx < 200:
        return False, None

    close = float(df["Close"].iloc[idx])
    low = float(df["Low"].iloc[idx])
    ma50 = df["MA50"].iloc[idx]

    if pd.isna(ma50):
        return False, None

    recent_high = df["High"].iloc[max(0, idx-20):idx].max()

    if abs(close - ma50) / ma50 > 0.03:
        return False, None

    recent_low = df["Low"].iloc[max(0, idx-10):idx+1].min()
    if recent_low > ma50 * 1.02:
        return False, None

    open_p = float(df["Open"].iloc[idx])
    if close <= open_p:
        if idx > 0 and close <= float(df["Close"].iloc[idx-1]):
            return False, None

    return True, recent_high


def check_minervini_signal(df):
    if not check_trend_template(df):
        return False, None

    idx = len(df) - 1
    high = float(df["High"].iloc[idx])
    vol = float(df["Volume"].iloc[idx])
    vol20 = df["Vol20"].iloc[idx]

    if pd.isna(vol20) or vol < vol20 * 1.3:
        return False, None

    # Route 1: VCP
    vcp_ok, pivot_v = detect_vcp(df)
    if vcp_ok and high >= pivot_v:
        return True, {
            "entry": pivot_v,
            "stop": pivot_v * 0.91,  # -9%
            "half_target": pivot_v * 1.25,
            "hold_days": 90,
            "route": "VCP",
            "pivot": pivot_v,
        }

    # Route 2: MA50 pullback
    ma_ok, pivot_ma = detect_ma_pullback(df)
    if ma_ok and high >= pivot_ma:
        return True, {
            "entry": pivot_ma,
            "stop": pivot_ma * 0.91,
            "half_target": pivot_ma * 1.25,
            "hold_days": 90,
            "route": "MA50押し目",
            "pivot": pivot_ma,
        }

    return False, None


# ============================================================================
# ポジションサイズ計算
# ============================================================================

def calc_shares(capital, risk_pct, entry, stop, strategy, regime, panic_risk_mult=0.5):
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return 0, 0
    risk_amount = capital * (risk_pct / 100)

    # v2.7 改善①: PANIC時のBNFリスク半減
    if strategy == "BNF-LITE" and regime == "PANIC":
        risk_amount *= panic_risk_mult

    shares = int(risk_amount / risk_per_share)
    # 100株単位に丸め
    shares = (shares // 100) * 100
    return shares, shares * entry


# ============================================================================
# データ取得
# ============================================================================

def fetch_global_data(days_back=400):
    end = datetime.now()
    start = end - timedelta(days=days_back)
    result = {}
    for ticker in GLOBAL_TICKERS:
        try:
            df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
            if df is not None and len(df) > 0:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                result[ticker] = df
        except Exception:
            pass
    return result


def fetch_stock_data(ticker, days_back=400):
    end = datetime.now()
    start = end - timedelta(days=days_back)
    try:
        df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


# ============================================================================
# メインスキャン
# ============================================================================

def scan(capital=1_000_000, risk_pct=1.0, progress_callback=None):
    now = datetime.now()

    # Global data取得
    global_data = fetch_global_data()
    if not global_data:
        return {
            "error": "グローバルデータ取得失敗",
            "timestamp": now.isoformat(),
        }

    # マーケット環境判定
    regime, regime_reason = detect_market_regime(global_data, now.date())

    # HALT判定
    is_halt, halt_reason = check_halt_conditions(global_data, now.date())

    # VIX と日経の現在値
    vix_now = None
    n225_now = None
    try:
        vix = global_data.get("^VIX")
        if vix is not None:
            vix_now = float(vix["Close"].iloc[-1])
        n225 = global_data.get("^N225")
        if n225 is not None:
            n225_now = float(n225["Close"].iloc[-1])
    except Exception:
        pass

    # 各銘柄スキャン
    signals = []
    scanned = 0
    failed = 0
    total = len(STOCKS)

    for ticker, (name, sector) in STOCKS.items():
        if progress_callback:
            progress_callback(scanned, total, name)

        df = fetch_stock_data(ticker)
        if df is None or len(df) < 200:
            failed += 1
            continue

        df = prepare_indicators(df)
        scanned += 1

        # BNF-LITE (PANIC/BEARISH/NEUTRAL のみ、HALT中でもBNFは発動)
        if regime in ("PANIC", "BEARISH", "NEUTRAL"):
            ok, info = check_bnf_signal(df, sector, regime)
            if ok:
                shares, cost = calc_shares(capital, risk_pct, info["entry"],
                                           info["stop"], "BNF-LITE", regime)
                if shares >= 100:
                    signals.append({
                        "strategy": "BNF-LITE",
                        "ticker": ticker,
                        "name": name,
                        "sector": sector,
                        "entry_price": info["entry"],
                        "stop_price": info["stop"],
                        "target_price": info["target"],
                        "hold_days": info["hold_days"],
                        "shares": shares,
                        "cost": cost,
                        "info": f"乖離率{info['deviation']:.1f}%(閾値{info['threshold']:.1f}%)",
                        "regime": regime,
                    })
                    continue

        # HALT中はBNF以外は停止
        if is_halt:
            continue

        # MOMENTUM (BULLISH のみ)
        if regime == "BULLISH":
            ok, info = check_momentum_signal(df)
            if ok:
                shares, cost = calc_shares(capital, risk_pct, info["entry"],
                                           info["stop"], "MOMENTUM", regime)
                if shares >= 100:
                    signals.append({
                        "strategy": "MOMENTUM",
                        "ticker": ticker,
                        "name": name,
                        "sector": sector,
                        "entry_price": info["entry"],
                        "stop_price": info["stop"],
                        "target_price": info["target"],
                        "hold_days": info["hold_days"],
                        "shares": shares,
                        "cost": cost,
                        "info": f"20日高値ブレイク ¥{info['pivot']:.0f}",
                        "regime": regime,
                    })
                    continue

        # MINERVINI (BULLISH/NEUTRAL)
        if regime in ("BULLISH", "NEUTRAL"):
            ok, info = check_minervini_signal(df)
            if ok:
                shares, cost = calc_shares(capital, risk_pct, info["entry"],
                                           info["stop"], "MINERVINI", regime)
                if shares >= 100:
                    signals.append({
                        "strategy": "MINERVINI",
                        "ticker": ticker,
                        "name": name,
                        "sector": sector,
                        "entry_price": info["entry"],
                        "stop_price": info["stop"],
                        "target_price": info["half_target"],
                        "hold_days": info["hold_days"],
                        "shares": shares,
                        "cost": cost,
                        "info": f"{info['route']} ピボット¥{info['pivot']:.0f}",
                        "regime": regime,
                    })

    return {
        "timestamp": now.isoformat(),
        "regime": regime,
        "regime_reason": regime_reason,
        "is_halt": is_halt,
        "halt_reason": halt_reason,
        "vix": vix_now,
        "n225": n225_now,
        "scanned": scanned,
        "failed": failed,
        "total_stocks": total,
        "signals": signals,
        "capital": capital,
        "risk_pct": risk_pct,
    }


# ============================================================================
# Markdown レポート生成
# ============================================================================

def generate_markdown_report(result, concise=False):
    now = datetime.fromisoformat(result["timestamp"])
    weekday_jp = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]

    lines = []
    lines.append(f"# 📊 トレードシグナル {now.strftime('%Y-%m-%d %H:%M')} ({weekday_jp})")
    lines.append("")

    # エラー処理
    if "error" in result:
        lines.append(f"⚠️ **エラー**: {result['error']}")
        return "\n".join(lines)

    # マーケット環境
    regime_emoji = {"BULLISH": "🚀", "NEUTRAL": "⚖️", "BEARISH": "⚠️", "PANIC": "🚨"}
    emoji = regime_emoji.get(result["regime"], "❓")
    lines.append(f"## {emoji} マーケット環境: **{result['regime']}**")
    lines.append(f"- 判定理由: {result['regime_reason']}")
    if result["vix"]:
        lines.append(f"- VIX: {result['vix']:.2f}")
    if result["n225"]:
        lines.append(f"- 日経平均: ¥{result['n225']:,.0f}")
    lines.append("")

    # サーキットブレーカー
    if result["is_halt"]:
        lines.append(f"## 🔴 サーキットブレーカー: **HALT**")
        lines.append(f"- 発動理由: {result['halt_reason']}")
        lines.append(f"- **BNF-LITE のみ発動可、MOMENTUM/MINERVINI は停止**")
    else:
        lines.append(f"## 🟢 サーキットブレーカー: NORMAL")
    lines.append("")

    # スキャン状況
    lines.append(f"## 📋 スキャン結果")
    lines.append(f"- 検証銘柄: {result['total_stocks']} 銘柄")
    lines.append(f"- 取得成功: {result['scanned']} 銘柄")
    lines.append(f"- 取得失敗: {result['failed']} 銘柄")
    lines.append(f"- 運用資金: ¥{result['capital']:,.0f}")
    lines.append(f"- 1トレードリスク: {result['risk_pct']}%")
    lines.append("")

    # シグナル
    signals = result["signals"]
    if not signals:
        lines.append("## ❌ 本日のシグナル: **なし**")
        lines.append("")
        lines.append("> 今日はエントリー候補がありませんでした。")
        lines.append("> 相場環境が改善するまで待機してください。")
        return "\n".join(lines)

    lines.append(f"## 🎯 本日のシグナル ({len(signals)} 件)")
    lines.append("")

    # 戦略別にグルーピング
    by_strategy = {}
    for s in signals:
        by_strategy.setdefault(s["strategy"], []).append(s)

    strategy_emoji = {"BNF-LITE": "📉", "MOMENTUM": "🚀", "MINERVINI": "📈"}

    for strat in ["BNF-LITE", "MOMENTUM", "MINERVINI"]:
        if strat not in by_strategy:
            continue
        emo = strategy_emoji.get(strat, "●")
        lines.append(f"### {emo} {strat} ({len(by_strategy[strat])} 件)")
        lines.append("")

        # テーブル
        lines.append("| 銘柄 | セクター | 買値 | 損切り | 目標 | 株数 | 必要資金 | 備考 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for s in by_strategy[strat]:
            lines.append(
                f"| {s['name']} ({s['ticker'].replace('.T','')}) "
                f"| {s['sector']} "
                f"| ¥{s['entry_price']:,.0f} "
                f"| ¥{s['stop_price']:,.0f} "
                f"| ¥{s['target_price']:,.0f} "
                f"| {s['shares']}株 "
                f"| ¥{s['cost']:,.0f} "
                f"| {s['info']} |"
            )
        lines.append("")

    # 発注用コピペブロック(SBI証券アプリ向け)
    if not concise and signals:
        lines.append("---")
        lines.append("## 📱 SBI証券 発注用メモ")
        lines.append("")
        lines.append("```")
        for i, s in enumerate(signals, 1):
            lines.append(f"【{i}】{s['name']} ({s['ticker'].replace('.T','')})")
            lines.append(f"   買い指値: ¥{s['entry_price']:,.0f}")
            lines.append(f"   逆指値:   ¥{s['stop_price']:,.0f}")
            lines.append(f"   利確目標: ¥{s['target_price']:,.0f}")
            lines.append(f"   株数:     {s['shares']}株")
            lines.append(f"   必要資金: ¥{s['cost']:,.0f}")
            lines.append(f"   保有期限: {s['hold_days']}日")
            lines.append("")
        lines.append("```")

    # 戦略の説明
    if not concise:
        lines.append("---")
        lines.append("## 📖 戦略メモ (v2.7.3)")
        lines.append("")
        lines.append("- **BNF-LITE**: セクター別実証閾値で-8〜-22%乖離を狙う逆張り(保有14日)")
        lines.append("- **MOMENTUM**: 20日高値ブレイクアウトの順張り(保有10日、+10%利確/-5%損切り)")
        lines.append("- **MINERVINI**: Trend Template 8条件通過 + VCP or MA50押し目(保有最大90日)")
        lines.append("")
        lines.append(f"- 地合い倍率: BULLISH×0.8 / NEUTRAL×1.0 / BEARISH×1.15 / PANIC×1.3")
        lines.append(f"- PANIC時のBNFはリスク半減 (panic-risk-mult=0.5)")
        lines.append("")

    lines.append("---")
    lines.append("> ⚠️ バックテスト結果は過去データです。実際の投資判断は自己責任で。")
    return "\n".join(lines)


# ============================================================================
# CLI
# ============================================================================

def parse_args():
    p = argparse.ArgumentParser(description="デイリースキャナー v2.7.3")
    p.add_argument("--capital", type=float, default=1_000_000,
                   help="運用資金(円、デフォルト100万)")
    p.add_argument("--risk", type=float, default=1.0,
                   help="1トレードリスク(パーセント、デフォルト1.0)")
    p.add_argument("--output", default=None,
                   help="出力ファイル(Markdown)。GitHub Actionsで使う")
    p.add_argument("--concise", action="store_true",
                   help="簡潔モード(説明・メモを省略)")
    p.add_argument("--json-output", default=None,
                   help="JSON形式での出力ファイル")
    return p.parse_args()


def main():
    args = parse_args()

    print(f"🎯 デイリースキャナー v2.7.3 起動", flush=True)
    print(f"   運用資金: ¥{args.capital:,.0f}", flush=True)
    print(f"   1トレードリスク: {args.risk}%", flush=True)
    print(f"", flush=True)

    def progress(n, total, name):
        if n % 10 == 0:
            print(f"  スキャン中... {n}/{total} ({name})", flush=True)

    result = scan(capital=args.capital, risk_pct=args.risk, progress_callback=progress)

    # Markdownレポート
    report = generate_markdown_report(result, concise=args.concise)

    # 標準出力にも
    print("")
    print(report)

    # ファイル出力
    if args.output:
        Path(args.output).write_text(report, encoding="utf-8")
        print(f"\n✓ Markdownレポート保存: {args.output}")

    if args.json_output:
        # JSONシリアライズ可能な形に
        json_result = {
            "timestamp": result.get("timestamp"),
            "regime": result.get("regime"),
            "is_halt": result.get("is_halt"),
            "halt_reason": result.get("halt_reason"),
            "vix": result.get("vix"),
            "n225": result.get("n225"),
            "capital": result.get("capital"),
            "signals": result.get("signals", []),
        }
        Path(args.json_output).write_text(
            json.dumps(json_result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8"
        )
        print(f"✓ JSON保存: {args.json_output}")


if __name__ == "__main__":
    main()
