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
import re
import sys
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

JST = timezone(timedelta(hours=9))
INDEX_TICKER = "^N225"

# 戦略コード → サイト表示用の日本語ラベル(ruletrade.jp の system.html の表記に合わせる)
STRATEGY_LABELS = {
    "MOMENTUM":  "順張り（ブレイク）",
    "BNF-LITE":  "逆張り（乖離）",
    "MINERVINI": "成長株（トレンド）",
}

# reason から落とす表記:
#   ・価格(¥1260) = 実質的なエントリーライン。無料側には出さない(指示書§10-5)
#   ・(閾値-10.0%) = 本番システムの調整済みパラメータ。指示書には明記が無いが、
#     完全版で売る中身そのものなので price と同じ扱いで落とす。
_PRICE_RE = re.compile(r"\s*[¥￥]\s*[\d,]+(?:\.\d+)?")
_THRESHOLD_RE = re.compile(r"\s*[（(]\s*閾値[^）)]*[）)]")


def clean_reason(info: str) -> str:
    """info 列から価格・閾値を除いた、無料公開して良い理由テキストを返す"""
    s = _THRESHOLD_RE.sub("", _PRICE_RE.sub("", info or ""))
    return s.strip()


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
#  本番ダッシュボードの当日シグナル(無料公開分)
# ─────────────────────────────────────────────

def load_today_signals(csv_path: str, today: str) -> dict | None:
    """
    ダッシュボード本体の signals_log.csv から「無料公開して良い項目だけ」を抜き出す。
    仕様は「指示書_無料版スキャナー_サイト連携.md」§10 でサイト側と合意。

    選び方(恣意性ゼロ・誰でも再現可能であることが要件):
      1. 当日(scan_date == today)の行のみ
      2. ticker が空の行(SCAN_RECORD / (all duplicate) の記録行)は除外
      3. 同一銘柄が複数スロット(朝/昼/ザラ場)で重複したら ticker で1つに寄せる → 件数
      4. 公開する1件は「当日が初出の銘柄のうち最も早いもの」

    ★4について指示書からの変更点:
      指示書の原案は単純に「その日いちばん早い1件」だったが、シグナルは条件を
      満たす限り日をまたいで繰り越されるため、それだと同じ銘柄が数日連続で
      選ばれてしまう(実データ検証: 7/21の最早はシスメックスだが初出は7/17で、
      7/20も最早だった)。§10-1「その日に出た1件」という狙いに合わせ、
      当日が初出の銘柄に限定した。恣意性は無く再現性も保たれる。

    ★同時刻の同着があるため(実データ: 7/21の SGホールディングス と 博報堂DYHD が
      同一タイムスタンプ)、tie-break に ticker 昇順を入れて一意に定める。

    signals_log.csv が無い場合は None を返す(呼び出し側でキーごと省略 = 後方互換)。
    """
    if not os.path.exists(csv_path):
        return None
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig", dtype=str).fillna("")
    except Exception as e:
        print(f"  signals_log.csv の読み込みに失敗: {e}")
        return None
    need = {"scan_date", "scan_timestamp", "ticker", "strategy", "name", "sector", "info"}
    if not need.issubset(df.columns):
        print(f"  signals_log.csv に必要な列がありません(必要: {sorted(need)})")
        return None

    df["ticker"] = df["ticker"].str.strip()
    has_ticker = df["ticker"] != ""
    today_rows = df[df["scan_date"] == today]

    out: dict = {"signals_today_date": today}

    # HALT(危険な相場で新規建て停止)は当日行のどれかが Y なら立っているとみなす
    if "is_halt" in df.columns and (today_rows["is_halt"] == "Y").any():
        out["is_halt"] = True
        reasons = [r for r in today_rows["halt_reason"].unique() if r] if "halt_reason" in df.columns else []
        out["halt_reason"] = reasons[0] if reasons else "相場環境の悪化"
    else:
        out["is_halt"] = False

    # 当日シグナル(銘柄単位に寄せる)
    sig = today_rows[today_rows["ticker"] != ""].copy()
    sig = sig.sort_values(["scan_timestamp", "ticker"]).drop_duplicates("ticker", keep="first")
    out["signals_today_count"] = int(len(sig))

    # 当日が初出の銘柄だけに絞る(過去のどこかで出ていたものは繰り越し扱い)
    seen_before = set(df[has_ticker & (df["scan_date"] < today)]["ticker"])
    fresh = sig[~sig["ticker"].isin(seen_before)]

    if len(fresh) == 0:
        out["signal_of_day"] = None
    else:
        r = fresh.iloc[0]
        out["signal_of_day"] = {
            "scan_date":      today,
            "strategy":       r["strategy"],
            "strategy_label": STRATEGY_LABELS.get(r["strategy"], r["strategy"]),
            "code":           r["ticker"].replace(".T", ""),
            "name":           r["name"],
            "sector":         r["sector"],
            "reason":         clean_reason(r["info"]),
        }
    return out


# ─────────────────────────────────────────────
#  スキャン本体
# ─────────────────────────────────────────────

def jiai_label(n_hist: pd.Series) -> str:
    """日経終値の系列から地合いラベルを作る(最終行時点で判定)"""
    close = float(n_hist.iloc[-1])
    ma25 = float(n_hist.rolling(25).mean().iloc[-1])
    ma75 = float(n_hist.rolling(75).mean().iloc[-1])
    if close > ma25 and close > ma75:
        return "強気(25日線・75日線の上)"
    if close > ma75:
        return "中立(75日線の上・25日線の下)"
    return "弱気(75日線の下)"


def scan(data: dict[str, pd.DataFrame], meta: pd.DataFrame,
         threshold: float, delay: int, top_n: int) -> tuple[dict, dict]:
    n225 = data.get(INDEX_TICKER)
    if n225 is None or len(n225) < 80:
        sys.exit("日経平均(^N225)のデータが取得できませんでした")

    # 対象営業日 = 日経の最新営業日から delay 営業日さかのぼった日
    if delay >= len(n225):
        sys.exit("delayが大きすぎます")
    data_date = n225.index[-1 - delay]

    # 地合い(市況)情報 — 対象営業日時点
    market_label = jiai_label(n225.loc[:data_date]["Close"])

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
    result = {
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

    # 地合い専用JSON(指示書§8-2) — こちらは遅延なし = 最新終値時点で判定する。
    # 地合いは「上位3銘柄・1営業日遅れ」という無料版の制限とは別物のため。
    live_date = n225.index[-1]
    jiai_live = {
        "target_date": live_date.strftime("%Y-%m-%d"),
        "generated_at": result["generated_at"],
        "jiai": jiai_label(n225["Close"]),
        "nikkei_close": round(float(n225["Close"].iloc[-1]), 2),
    }
    return result, jiai_live


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
    ap.add_argument("--signals-log", default="signals_log.csv",
                    help="本番ダッシュボードのシグナル履歴CSV(当日1件の抽出元)")
    ap.add_argument("--jiai-output", default="docs/market_jiai.json",
                    help="遅延なし地合い専用JSONの出力先(指示書§8-2)")
    ap.add_argument("--today", default=None,
                    help="当日シグナルの対象日(既定=実行時のJST日付。検証用に上書き可)")
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

    result, jiai_live = scan(data, meta, args.th, args.delay, args.top)

    # ★指示書§10: 本番ダッシュボードの「当日1件」+「当日の総件数」を追加。
    #   既存キーは消さず追加するだけ = サイト側が段階的に移行できる後方互換方式。
    today = args.today or datetime.now(JST).strftime("%Y-%m-%d")
    today_info = load_today_signals(args.signals_log, today)
    if today_info is None:
        print(f"\n⚠ {args.signals_log} が読めないため signal_of_day は出力しません")
        print("  (サイト側は従来の rows にフォールバックします)")
    else:
        result.update(today_info)

    def write_json(path: str, obj: dict) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)

    write_json(args.output, result)
    write_json(args.jiai_output, jiai_live)

    n_sig = sum(1 for r in result["rows"] if r["judge"] == "signal")
    print(f"\n[無料版スキャナー] 対象営業日: {result['target_date']} / 地合い: {result['jiai']}")
    print(f"公開 {len(result['rows'])}件中 シグナル{n_sig}件")
    for r in result["rows"]:
        mark = "◆シグナル" if r["judge"] == "signal" else "　監視"
        print(f"  {mark}  {r['code']} {r['name']}  乖離率 {r['kairi']:+.2f}%")

    if today_info is not None:
        print(f"\n[本番シグナル] {today} のシグナル {result['signals_today_count']}件"
              + ("  ※HALT中" if result.get("is_halt") else ""))
        sod = result.get("signal_of_day")
        if sod:
            print(f"  公開する1件: {sod['code']} {sod['name']} ({sod['strategy_label']}) — {sod['reason']}")
        else:
            print("  公開する1件: なし(当日初出の銘柄が無い日)")

    print(f"\n[地合い専用] {jiai_live['target_date']} / {jiai_live['jiai']}"
          f" / 日経 {jiai_live['nikkei_close']:,.2f}")
    print(f"\n出力: {args.output} / {args.jiai_output}")


if __name__ == "__main__":
    main()
