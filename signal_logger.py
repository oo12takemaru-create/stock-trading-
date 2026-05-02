"""
================================================================================
 シグナルロガー v1.0
 daily_scanner が出力した signal.json を signals_log.csv に追記
================================================================================

GitHub Actions の workflow から呼ばれて、シグナル履歴を自動蓄積する。

実行:
  python signal_logger.py signal.json signals_log.csv

出力:
  signals_log.csv に1行ずつ追記(ヘッダー無ければ自動作成)
  既に同じシグナルがあればスキップ(重複防止)
================================================================================
"""

from __future__ import annotations
import sys
import os
import csv
import json
from datetime import datetime

LOG_FIELDS = [
    "scan_timestamp",   # スキャン実行時刻 (ISO8601)
    "scan_date",        # スキャン日 (YYYY-MM-DD)
    "scan_slot",        # 朝/昼/夕
    "regime",           # マーケット環境 (BULLISH/NEUTRAL/BEARISH/PANIC)
    "is_halt",          # サーキットブレーカー HALT 中か
    "halt_reason",      # HALT 理由
    "vix",              # VIX
    "n225",             # 日経平均
    "strategy",         # 戦略 (BNF-LITE/MOMENTUM/MINERVINI)
    "ticker",           # 銘柄コード
    "name",             # 銘柄名
    "sector",           # セクター
    "entry_price",      # シグナル時の指値
    "stop_price",       # 損切り価格
    "target_price",     # 利確目標
    "shares",           # 推奨株数
    "cost",             # 必要資金
    "hold_days",        # 保有期限
    "info",             # 補足情報
    "signal_id",        # ユニークID(scan_date + ticker + strategy)
]


def determine_slot(timestamp_str):
    """ISO8601 の時刻から 朝/昼/夕 を判定"""
    try:
        dt = datetime.fromisoformat(timestamp_str)
    except Exception:
        dt = datetime.now()
    h = dt.hour
    if h < 10:
        return "朝"
    elif h < 15:
        return "昼"
    else:
        return "夕"


def load_existing_log(csv_path):
    """既存ログから signal_id を集合として返す(重複検知用)"""
    if not os.path.exists(csv_path):
        return set()
    existing = set()
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            sid = row.get("signal_id", "")
            if sid:
                existing.add(sid)
    return existing


def append_log(csv_path, rows):
    """rowsをcsvに追記。ファイル無ければヘッダー付きで作成"""
    file_exists = os.path.exists(csv_path)
    with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if not file_exists:
            writer.writeheader()
        for row in rows:
            full = {k: row.get(k, "") for k in LOG_FIELDS}
            writer.writerow(full)


def main():
    if len(sys.argv) < 2:
        print("Usage: python signal_logger.py <signal.json> [signals_log.csv]")
        sys.exit(1)

    signal_path = sys.argv[1]
    log_path = sys.argv[2] if len(sys.argv) >= 3 else "signals_log.csv"

    if not os.path.exists(signal_path):
        print(f"エラー: {signal_path} が見つかりません")
        sys.exit(1)

    with open(signal_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    timestamp = data.get("timestamp", datetime.now().isoformat())
    scan_date = timestamp[:10]
    slot = determine_slot(timestamp)
    regime = data.get("regime", "UNKNOWN")
    is_halt = data.get("is_halt", False)
    halt_reason = data.get("halt_reason", "") or ""
    vix = data.get("vix", "")
    n225 = data.get("n225", "")
    signals = data.get("signals", [])

    # 既存ログから重複チェック用 ID を取得
    existing_ids = load_existing_log(log_path)

    # 新規シグナル行を生成
    new_rows = []
    for s in signals:
        ticker = s.get("ticker", "")
        strategy = s.get("strategy", "")
        # ユニークID: 日付 + ティッカー + 戦略 + スロット
        sid = f"{scan_date}_{ticker}_{strategy}_{slot}"
        if sid in existing_ids:
            continue  # 同じシグナル(同日同ティッカー同戦略同スロット)はスキップ

        new_rows.append({
            "scan_timestamp": timestamp,
            "scan_date": scan_date,
            "scan_slot": slot,
            "regime": regime,
            "is_halt": "Y" if is_halt else "N",
            "halt_reason": halt_reason,
            "vix": vix,
            "n225": n225,
            "strategy": strategy,
            "ticker": ticker,
            "name": s.get("name", ""),
            "sector": s.get("sector", ""),
            "entry_price": s.get("entry_price", ""),
            "stop_price": s.get("stop_price", ""),
            "target_price": s.get("target_price", ""),
            "shares": s.get("shares", ""),
            "cost": s.get("cost", ""),
            "hold_days": s.get("hold_days", ""),
            "info": s.get("info", ""),
            "signal_id": sid,
        })

    # 重複していなくてもシグナル0件のスキャンは記録(履歴として)
    if not new_rows and not signals:
        # シグナル0件でもスキャンの存在を記録
        sid = f"{scan_date}_{slot}_NOSIGNAL"
        if sid not in existing_ids:
            new_rows.append({
                "scan_timestamp": timestamp,
                "scan_date": scan_date,
                "scan_slot": slot,
                "regime": regime,
                "is_halt": "Y" if is_halt else "N",
                "halt_reason": halt_reason,
                "vix": vix,
                "n225": n225,
                "strategy": "(no signal)",
                "ticker": "",
                "name": "",
                "sector": "",
                "entry_price": "",
                "stop_price": "",
                "target_price": "",
                "shares": "",
                "cost": "",
                "hold_days": "",
                "info": "シグナルなし",
                "signal_id": sid,
            })

    if new_rows:
        append_log(log_path, new_rows)
        print(f"✓ {len(new_rows)}件のシグナルを {log_path} に追記しました")
        for r in new_rows:
            if r["ticker"]:
                print(f"  [{r['strategy']}] {r['name']} ({r['ticker']}) @ ¥{r['entry_price']}")
            else:
                print(f"  [スキャン記録] {r['scan_date']} {r['scan_slot']} {r['regime']} - シグナルなし")
    else:
        print("(新規追加なし - 全て既存シグナル)")


if __name__ == "__main__":
    main()
