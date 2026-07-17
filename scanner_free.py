# -*- coding: utf-8 -*-
"""
================================================================================
 無料版デイリースキャナー (scanner_free.py)
 書籍『BNFに学ぶ』掲載の基本ルールをそのまま毎日実行し、
 無料公開用に「制限を掛けた」結果JSONを生成する。
================================================================================

ルール(書籍掲載の検証と同一・BNF2検証/bnf2_verify.py と同じ定義):
  ・終値の25日移動平均線からの乖離率 <= -15% でシグナル
  ・判定は終値ベース(未来情報不使用)

無料版の制限(生成時に適用 = 公開JSONには制限後のデータしか入らない):
  ・1営業日遅れ (--delay 1): 当日ではなく前営業日の終値で判定した結果を公開
  ・乖離率ランキング上位N件のみ (--top 3)
  ・利確/損切ライン・推奨株数などの売買情報は一切含めない

出力JSONは GitHub Pages (docs/free_scanner.json) から配信し、ruletrade.jp の
閲覧ページ(サイト側チャットが実装)が読む。スキーマは
「指示書_無料版スキャナー_サイト連携.md」(2026-07-17)でサイト側と合意済み。

実行:
  python scanner_free.py                                   # 本番(yfinance取得)
  python scanner_free.py --output docs/free_scanner.json
  python scanner_free.py --cache-dir "BNF2検証/cache"      # ローカル動作確認(キャッシュのみ)

必要ライブラリ: pip install yfinance pandas numpy
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

JST = timezone(timedelta(hours=9))
INDEX_TICKER = "^N225"


# ─────────────────────────────────────────────
#  データ取得
# ─────────────────────────────────────────────

def load_tickers(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8")
    need = {"ticker", "name", "sector"}
    if not need.issubset(df.columns):
        sys.exit(f"銘柄CSVに {need} 列が必要です: {path}")
    return df


def fetch_from_cache(tickers: list[str], cache_dir: str) -> dict[str, pd.DataFrame]:
    """ローカル動作確認用: BNF2検証のキャッシュCSVを読む(ネット不要)"""
    data = {}
    for t in tickers:
        fp = os.path.join(cache_dir, t.replace("^", "_") + ".csv")
        if os.path.exists(fp):
            df = pd.read_csv(fp, index_col=0, parse_dates=True)
            if len(df) > 50:
                data[t] = df
    return data


def fetch_live(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yfinanceで直近1年分を一括取得(欠損銘柄は個別リトライ)"""
    import yfinance as yf

    data: dict[str, pd.DataFrame] = {}
    raw = yf.download(tickers, period="1y", progress=False,
                      auto_adjust=True, group_by="ticker", threads=True)
    for t in tickers:
        try:
            df = raw[t][["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(df) > 30:
                data[t] = df
                continue
        except Exception:
            pass
        # 一括取得で欠けた銘柄は個別に取り直す
        try:
            df = yf.download(t, period="1y", progress=False, auto_adjust=True)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            if len(df) > 30:
                data[t] = df
        except Exception as e:
            print(f"  取得失敗: {t} ({e})")
    return data


# ─────────────────────────────────────────────
#  スキャン本体
# ─────────────────────────────────────────────

def scan(data: dict[str, pd.DataFrame], meta: pd.DataFrame,
         threshold: float, delay: int, top_n: int) -> dict:
    n225 = data.get(INDEX_TICKER)
    if n225 is None or len(n225) < 80:
        sys.exit("日経平均(^N225)のデータが取得できませんでした")

    # 対象営業日 = 日経の最新営業日から delay 営業日さかのぼった日
    if delay >= len(n225):
        sys.exit("delayが大きすぎます")
    data_date = n225.index[-1 - delay]

    # 地合い(市況)情報 — 対象営業日時点
    n_hist = n225.loc[:data_date]["Close"]
    n_close = float(n_hist.iloc[-1])
    n_ma25 = float(n_hist.rolling(25).mean().iloc[-1])
    n_ma75 = float(n_hist.rolling(75).mean().iloc[-1])
    above25 = n_close > n_ma25
    above75 = n_close > n_ma75
    if above25 and above75:
        market_label = "強気(25日線・75日線の上)"
    elif above75:
        market_label = "中立(75日線の上・25日線の下)"
    else:
        market_label = "弱気(75日線の下)"

    # 各銘柄の乖離率(対象営業日時点)
    rows = []
    meta_map = {r["ticker"]: (r["name"], r["sector"]) for _, r in meta.iterrows()}
    for t, df in data.items():
        if t == INDEX_TICKER:
            continue
        hist = df.loc[:data_date]["Close"]
        if len(hist) < 30:
            continue
        # 対象営業日に値が無い銘柄(売買停止等)はスキップ
        if hist.index[-1] != data_date:
            continue
        close = float(hist.iloc[-1])
        ma25 = float(hist.rolling(25).mean().iloc[-1])
        if not np.isfinite(ma25) or ma25 <= 0:
            continue
        kairi = (close / ma25 - 1.0) * 100.0
        name, sector = meta_map.get(t, (t, ""))
        rows.append({
            "code": t.replace(".T", ""),
            "name": name,
            "sector": sector,
            "close": round(close, 1),
            "kairi": round(kairi, 2),
            "is_signal": bool(kairi <= threshold),
        })

    rows.sort(key=lambda r: r["kairi"])          # 乖離が深い順
    shown = rows[:top_n]                          # ★無料版制限: 上位N件のみ公開

    # 出力スキーマは「指示書_無料版スキャナー_サイト連携.md」(2026-07-17)で
    # サイト側と合意したもの。変更する場合は指示書への追記でサイト側に連絡すること。
    return {
        "target_date": data_date.strftime("%Y-%m-%d"),
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "jiai": market_label,
        "rows": [
            {
                "judge": "signal" if r["is_signal"] else "watch",
                "code": r["code"],
                "name": r["name"],
                "sector": r["sector"],
                "kairi": r["kairi"],
            }
            for r in shown
        ],
    }


# ─────────────────────────────────────────────
#  メイン
# ─────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="無料版BNFデイリースキャナー")
    ap.add_argument("--tickers", default="tickers_bnf_free.csv")
    ap.add_argument("--output", default="docs/free_scanner.json")
    ap.add_argument("--th", type=float, default=-15.0, help="乖離率閾値(%%)")
    ap.add_argument("--top", type=int, default=3, help="無料公開する件数")
    ap.add_argument("--delay", type=int, default=1, help="公開の遅延(営業日)")
    ap.add_argument("--cache-dir", default=None,
                    help="指定するとネット取得せずキャッシュCSVのみで動作(動作確認用)")
    args = ap.parse_args()

    meta = load_tickers(args.tickers)
    tickers = list(meta["ticker"]) + [INDEX_TICKER]

    print(f"銘柄数: {len(meta)} / 閾値: {args.th}% / 公開: 上位{args.top}件 / 遅延: {args.delay}営業日")
    if args.cache_dir:
        print(f"キャッシュモード: {args.cache_dir}")
        data = fetch_from_cache(tickers, args.cache_dir)
    else:
        data = fetch_live(tickers)
    print(f"データ取得: {len(data)}銘柄")

    result = scan(data, meta, args.th, args.delay, args.top)

    out_dir = os.path.dirname(args.output)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    n_sig = sum(1 for r in result["rows"] if r["judge"] == "signal")
    print(f"\n対象営業日: {result['target_date']} / 地合い: {result['jiai']}")
    print(f"公開 {len(result['rows'])}件中 シグナル{n_sig}件")
    for r in result["rows"]:
        mark = "◆シグナル" if r["judge"] == "signal" else "　監視"
        print(f"  {mark}  {r['code']} {r['name']}  乖離率 {r['kairi']:+.2f}%")
    print(f"\n出力: {args.output}")


if __name__ == "__main__":
    main()
