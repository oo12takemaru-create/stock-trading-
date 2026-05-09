"""
================================================================================
 銘柄疎通チェックツール v2.0 - AI・ロボット・新興成長株版
================================================================================

最近好調なAI関連 / ロボット関連 / 新興グロース株を約45本ピックアップし、
yfinance での取得可否と流動性(1日売買代金 5億円以上)をチェックする。

実行:
  python ticker_verifier_ai.py

出力:
  verified_tickers_ai.py        ← STOCKS 辞書に追加する Python コード
  ticker_check_result_ai.csv    ← 検証結果の詳細
================================================================================
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("エラー: yfinance pandas が必要")
    sys.exit(1)

try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import track
    from rich import box
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False

# ============================================================================
# 候補銘柄(現STOCKS辞書に未登録のAI/ロボット/グロース株 約45本)
# ============================================================================

CANDIDATES = [
    # === 純粋AIプレイ(生成AI / AI-OCR / AI半導体)===
    ("6526.T", "ソシオネクスト",            "AI半導体"),
    ("4011.T", "ヘッドウォータース",        "AIサービス"),
    ("4382.T", "HEROZ",                    "AIサービス"),
    ("4488.T", "AI inside",                "AIサービス"),
    ("4180.T", "Appier Group",             "AIサービス"),
    ("4259.T", "エクサウィザーズ",          "AIサービス"),
    ("5132.T", "pluszero",                 "AIサービス"),
    ("5026.T", "トリプルアイズ",            "AIサービス"),
    ("5588.T", "ファーストアカウンティング", "AIサービス"),
    ("3993.T", "PKSHA Technology",         "AIサービス"),

    # === ロボット・FA・自動化 ===
    ("7779.T", "サイバーダイン",      "産業用ロボット"),
    ("6383.T", "ダイフク",            "産業用ロボット"),
    ("6457.T", "グローリー",          "産業用ロボット"),
    ("6645.T", "オムロン",            "FAセンサー"),
    ("6504.T", "富士電機",            "総合電機"),
    ("7741.T", "HOYA",               "光学"),

    # === 宇宙・EV・再エネ ===
    ("5595.T", "QPS研究所",           "宇宙"),
    ("9519.T", "レノバ",              "再エネ"),
    ("7211.T", "三菱自動車",          "自動車"),

    # === 新興SaaS / IT ===
    ("4477.T", "BASE",               "SaaS"),
    ("4475.T", "HENNGE",             "セキュリティ"),
    ("4434.T", "サーバーワークス",    "SaaS"),
    ("3697.T", "SHIFT",              "ITサービス"),
    ("4480.T", "メドレー",            "医療IT"),
    ("4435.T", "カオナビ",            "SaaS"),
    ("9468.T", "KADOKAWA",           "エンタメ"),
    ("4413.T", "ボードルア",          "ITサービス"),
    ("5253.T", "カバー",              "エンタメ"),
    ("9719.T", "SCSK",               "ITサービス"),
    ("4384.T", "ラクスル",            "EC"),
    ("2484.T", "出前館",              "EC"),

    # === バイオ・医療テック ===
    ("4974.T", "タカラバイオ",        "バイオ"),
    ("4565.T", "ネクセラファーマ",    "バイオ"),
    ("2150.T", "ケアネット",          "医療IT"),

    # === 半導体新興・電子部品 ===
    ("6890.T", "フェローテックHD",    "半導体"),
    ("3436.T", "SUMCO",              "半導体シリコン"),
    ("6750.T", "エレコム",            "電子部品"),
    ("6951.T", "日本電子",            "電子機器"),

    # === 中小型機械・新興産業 ===
    ("6028.T", "テクノプロHD",        "人材"),
    ("7296.T", "エフ・シー・シー",    "自動車部品"),
    ("4928.T", "Noritsu Koki",       "電機"),
    ("6027.T", "弁護士ドットコム",    "ITサービス"),

    # === その他新興グロース ===
    ("3661.T", "エムアップHD",        "ITサービス"),
    ("4424.T", "Amazia",             "ITサービス"),
    ("5246.T", "ELEMENTS",           "AIサービス"),

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ★第2弾追加(2026-05-09): AIインフラ/ロボット/防衛/新興SaaS 27本
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # === AIインフラ・データセンター・電源 ===
    ("4485.T", "JTOWER",                    "通信インフラ"),
    ("6754.T", "アンリツ",                  "計測機器"),
    ("5803.T", "フジクラ",                  "電線"),
    ("5805.T", "SWCC",                      "電線"),
    ("6967.T", "新光電気工業",              "電子部品"),
    ("6963.T", "ローム",                    "半導体"),
    ("6855.T", "日本電子材料",              "半導体"),

    # === ロボット・FA・自動化(拡張) ===
    ("6841.T", "横河電機",                  "制御機器"),
    ("6473.T", "ジェイテクト",              "自動車部品"),
    ("6770.T", "アルプスアルパイン",        "電子部品"),
    ("6440.T", "JUKI",                      "産業機械"),
    ("6724.T", "セイコーエプソン",          "電機"),

    # === 宇宙・防衛 ===
    ("9412.T", "スカパーJSAT",              "宇宙"),
    ("5631.T", "日本製鋼所",                "防衛"),
    ("6208.T", "石川製作所",                "防衛"),
    ("7259.T", "アイスペース",              "宇宙"),

    # === 新興AI・SaaS・テック ===
    ("4053.T", "Sun Asterisk",              "ITサービス"),
    ("4493.T", "サイバーセキュリティクラウド", "セキュリティ"),
    ("4448.T", "Chatwork",                  "SaaS"),
    ("9213.T", "セレス",                    "ITサービス"),
    ("4051.T", "GMOフィナンシャルゲート",   "決済"),
    ("3656.T", "KLab",                      "ゲーム"),

    # === 医療AI・バイオ ===
    ("4596.T", "窪田製薬HD",                "バイオ"),
    ("6849.T", "日本光電",                  "医療機器"),
    ("4151.T", "協和キリン",                "医薬品"),

    # === EV・自動車テック・電子部品 ===
    ("6995.T", "東海理化",                  "自動車部品"),
    ("3105.T", "日清紡HD",                  "電機"),
]

# 重複除去
seen = set()
UNIQUE_CANDIDATES = []
for ticker, name, sector in CANDIDATES:
    if ticker not in seen:
        seen.add(ticker)
        UNIQUE_CANDIDATES.append((ticker, name, sector))


def check_ticker(ticker, min_days=200):
    """yfinanceで銘柄が取得できるかチェック"""
    end = datetime.now()
    start = end - timedelta(days=400)
    try:
        df = yf.download(ticker, start=start, end=end,
                         progress=False, auto_adjust=False)
        if df is None or len(df) < min_days:
            return False, 0 if df is None else len(df), None

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        latest_close = float(df["Close"].iloc[-1])
        avg_volume = float(df["Volume"].iloc[-20:].mean())
        avg_turnover = latest_close * avg_volume

        return True, len(df), {
            "close": latest_close,
            "avg_volume": avg_volume,
            "avg_turnover_m": avg_turnover / 1_000_000,
        }
    except Exception as e:
        return False, 0, str(e)


def main():
    n = len(UNIQUE_CANDIDATES)
    print(f"AI・ロボット・新興成長株 候補: {n}本")
    print(f"yfinance で疎通確認中...\n")

    results = []
    iterator = UNIQUE_CANDIDATES
    if RICH:
        iterator = track(iterator, description="チェック中...")

    for ticker, name, sector in iterator:
        ok, days, info = check_ticker(ticker)
        results.append({
            "ticker": ticker, "name": name, "sector": sector,
            "ok": ok, "days": days, "info": info,
        })
        time.sleep(0.1)

    verified = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    liquid = [r for r in verified if isinstance(r["info"], dict)
              and r["info"].get("avg_turnover_m", 0) >= 500]
    low_liquidity = [r for r in verified if isinstance(r["info"], dict)
                     and r["info"].get("avg_turnover_m", 0) < 500]

    print(f"\n{'='*60}")
    print(f"検証結果")
    print(f"{'='*60}")
    print(f"  候補総数: {n}本")
    print(f"  ✅ 取得成功: {len(verified)}本")
    print(f"  🔥 流動性OK(5億円/日以上): {len(liquid)}本")
    print(f"  ⚠️  流動性低い: {len(low_liquidity)}本")
    print(f"  ❌ 取得失敗: {len(failed)}本\n")

    if RICH and verified:
        t = Table(title="✅ 取得成功 & 流動性OKの銘柄",
                  box=box.ROUNDED, show_lines=False)
        t.add_column("Ticker", style="cyan")
        t.add_column("銘柄名", style="white")
        t.add_column("セクター", style="magenta")
        t.add_column("終値", justify="right")
        t.add_column("日商(百万円)", justify="right")

        for r in sorted(liquid, key=lambda x: -x["info"]["avg_turnover_m"]):
            t.add_row(
                r["ticker"], r["name"], r["sector"],
                f"¥{r['info']['close']:,.0f}",
                f"{r['info']['avg_turnover_m']:,.0f}",
            )
        console.print(t)

        if low_liquidity:
            t2 = Table(title="⚠️  流動性が低い銘柄(日商5億円未満)",
                       box=box.ROUNDED)
            t2.add_column("Ticker", style="yellow")
            t2.add_column("銘柄名", style="white")
            t2.add_column("日商(百万円)", justify="right")
            for r in low_liquidity:
                t2.add_row(r["ticker"], r["name"],
                           f"{r['info']['avg_turnover_m']:,.0f}")
            console.print(t2)

        if failed:
            t3 = Table(title="❌ 取得失敗銘柄", box=box.ROUNDED)
            t3.add_column("Ticker", style="red")
            t3.add_column("銘柄名", style="white")
            t3.add_column("理由", style="dim")
            for r in failed:
                reason = str(r["info"]) if r["info"] else "データ不足"
                t3.add_row(r["ticker"], r["name"], reason[:50])
            console.print(t3)

    code_lines = [
        "# ============================================================================",
        "# 新規追加銘柄 - AI・ロボット・新興成長株(検証済み 流動性5億円/日以上)",
        "# ============================================================================",
        "ADD_TO_STOCKS = {",
    ]
    for r in sorted(liquid, key=lambda x: x["sector"] + x["ticker"]):
        code_lines.append(f'    "{r["ticker"]}": ("{r["name"]}", "{r["sector"]}"),')
    code_lines.append("}")
    py_code = "\n".join(code_lines)

    with open("verified_tickers_ai.py", "w", encoding="utf-8") as f:
        f.write(py_code)
    print(f"\n✓ 追加用Pythonコード: verified_tickers_ai.py に保存")

    csv_lines = ["ticker,name,sector,ok,days,close,avg_turnover_m"]
    for r in results:
        info = r["info"] if isinstance(r["info"], dict) else {}
        csv_lines.append(",".join([
            r["ticker"], r["name"], r["sector"],
            "OK" if r["ok"] else "NG", str(r["days"]),
            f"{info.get('close', 0):.0f}" if r["ok"] else "",
            f"{info.get('avg_turnover_m', 0):.0f}" if r["ok"] else "",
        ]))
    with open("ticker_check_result_ai.csv", "w", encoding="utf-8-sig") as f:
        f.write("\n".join(csv_lines))
    print(f"✓ 検証結果詳細: ticker_check_result_ai.csv に保存")

    print()
    print("=" * 60)
    print("次のステップ:")
    print("  1. verified_tickers_ai.py の内容を確認")
    print("  2. daily_scanner_v2_7_4.py / integrated_backtest_v2_7_4.py に追加")
    print("  3. バックテスト再実行で性能を確認")
    print("=" * 60)


if __name__ == "__main__":
    main()
