# -*- coding: utf-8 -*-
"""
realtime_notifier.py ― ザラ場中シグナル即時通知(メール + Discord・完全無料)

既存 daily_scanner_v2_8_0.py の scan() をそのまま流用し、
ザラ場中(平日9:00〜15:30)に一定間隔で再スキャン → 新規買いシグナルが
成立した瞬間に メール(Gmail SMTP)と Discord に同時通知する。

【無料で動く仕組み】
  データ : yfinance(日本株は約15〜20分遅延。スイング用途なら実用上問題なし)
  通知   : Gmail SMTP / Discord Webhook(いずれも無料)
  実行   : このPCのタスクスケジューラ、または GitHub Actions(PC不要・無料)

【重複排除(ゴミを溜めない設計)】
  ・ローカル既定 : notifier_seen.json(日付で自動リセット / .gitignore済)
  ・--log-csv 指定: signals_log.csv を参照し「同日・同銘柄・同戦略」は再通知しない。
                    新規シグナルだけを signals_log.csv に追記(=ダッシュボードに反映)。
                    ★毎回の空レコードは書かないので、コミットは新規発生時のみ。

────────────────────────────────────────────────────────────
■ 事前準備(初回のみ・ローカル運用時)
  1) Gmail 2段階認証 → アプリパスワード発行: https://myaccount.google.com/apppasswords
  2) Discord: サーバー設定 → 連携サービス → ウェブフック → URLをコピー
  3) 環境変数(PowerShellで一度だけ。値は自分のものに):
       setx GMAIL_USER "oo12takemaru@gmail.com"
       setx GMAIL_APP_PASSWORD "xxxxxxxxxxxxxxxx"
       setx NOTIFY_TO "oo12takemaru@gmail.com"
       setx DISCORD_WEBHOOK_URL "https://discord.com/api/webhooks/...."
     ※設定後、新しいターミナルを開き直すこと
  4) 通知テスト(両チャネルにテスト送信):
       python realtime_notifier.py --test

■ 使い方
  ローカル単発(タスクスケジューラ向け):
       python realtime_notifier.py --once
  ローカル常駐(15分間隔):
       python realtime_notifier.py
  GitHub Actions / CSV重複排除あり:
       python realtime_notifier.py --once --log-csv signals_log.csv
  送信せず判定だけ:
       python realtime_notifier.py --once --force --dry-run
────────────────────────────────────────────────────────────
"""
import os
import sys
import csv
import json
import time
import ssl
import smtplib
import argparse
from datetime import datetime, timedelta, date
from email.mime.text import MIMEText
from email.header import Header

import pandas as pd
import requests

# ── 既存スキャナー / ロガーをモジュールとして読み込む(main()は走らない) ──
import daily_scanner_v2_8_0 as ds
import signal_logger as sl  # append_log / LOG_FIELDS を再利用

SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifier_seen.json")
STRAT_EMOJI = {"BNF-LITE": "🔄", "MOMENTUM": "🚀", "MINERVINI": "🌱"}


# ============================================================================
# ① ザラ場中の「当日足」を取り込むための fetch 差し替え(monkeypatch)
# ============================================================================
def _fetch_stock_intraday(ticker, days_back=400):
    end = datetime.now() + timedelta(days=1)  # ← 翌日まで取り、当日の形成中足を含める
    start = end - timedelta(days=days_back)
    try:
        df = ds.yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
        if df is None or len(df) == 0:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception:
        return None


def _fetch_global_intraday(days_back=400):
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days_back)
    result = {}
    for ticker in ds.GLOBAL_TICKERS:
        try:
            df = ds.yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
            if df is not None and len(df) > 0:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                result[ticker] = df
        except Exception:
            pass
    return result


def enable_intraday_fetch():
    ds.fetch_stock_data = _fetch_stock_intraday
    ds.fetch_global_data = _fetch_global_intraday


# ============================================================================
# ② 重複排除 ― ローカル(JSON) / CSV(signals_log.csv)の2方式
# ============================================================================
def load_seen_json():
    today = date.today().isoformat()
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("date") == today:
                return set(data.get("keys", []))
        except Exception:
            pass
    return set()


def save_seen_json(keys):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"date": date.today().isoformat(), "keys": sorted(keys)},
                      f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠ seen保存失敗: {e}")


def load_seen_csv(csv_path):
    """signals_log.csv から「本日分の 日付_銘柄_戦略」を集合で返す(再通知防止)"""
    today = date.today().isoformat()
    seen = set()
    if not os.path.exists(csv_path):
        return seen
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("scan_date") == today and row.get("ticker"):
                    seen.add(f"{row['scan_date']}_{row['ticker']}_{row['strategy']}")
    except Exception as e:
        log(f"⚠ CSV読込失敗: {e}")
    return seen


def append_signals_csv(csv_path, new_signals, result):
    """新規シグナルだけを signals_log.csv に追記(空レコードは書かない=ゴミ抑制)"""
    ts = result.get("timestamp", datetime.now().isoformat())
    scan_date = ts[:10]
    rows = []
    for s in new_signals:
        rows.append({
            "scan_timestamp": ts,
            "scan_date": scan_date,
            "scan_slot": "ザラ場",
            "regime": result.get("regime", ""),
            "is_halt": "Y" if result.get("is_halt") else "N",
            "halt_reason": result.get("halt_reason", "") or "",
            "vix": result.get("vix", ""),
            "n225": result.get("n225", ""),
            "strategy": s["strategy"],
            "ticker": s["ticker"],
            "name": s["name"],
            "sector": s["sector"],
            "entry_price": s["entry_price"],
            "stop_price": s["stop_price"],
            "target_price": s["target_price"],
            "shares": s["shares"],
            "cost": s["cost"],
            "hold_days": s["hold_days"],
            "info": s["info"],
            "signal_id": f"{scan_date}_{s['ticker']}_{s['strategy']}_ザラ場",
        })
    if rows:
        sl.append_log(csv_path, rows)
        log(f"📝 signals_log.csv に {len(rows)}件追記")


# ============================================================================
# ③ 通知 ― メール(Gmail SMTP) + Discord(Webhook)
# ============================================================================
def get_mail_config():
    user = os.environ.get("GMAIL_USER")
    pw = os.environ.get("GMAIL_APP_PASSWORD")
    to = os.environ.get("NOTIFY_TO") or user
    return user, pw, to


def get_discord_url():
    return os.environ.get("DISCORD_WEBHOOK_URL")


def send_email(subject, body):
    user, pw, to = get_mail_config()
    if not user or not pw:
        return None  # 未設定(スキップ)
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as smtp:
            smtp.login(user, pw)
            smtp.sendmail(user, [to], msg.as_string())
        log(f"✉ メール送信成功 → {to}")
        return True
    except Exception as e:
        log(f"⚠ メール送信失敗: {e}")
        return False


def send_ntfy(title, body):
    """ntfy.sh プッシュ通知(スマホアプリでトピック購読するだけ・登録不要)
       日本語/絵文字対応のため JSON publish API を使用"""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return None  # 未設定(スキップ)
    try:
        res = requests.post(
            "https://ntfy.sh",
            json={
                "topic": topic,
                "title": title,
                "message": body[:3800],  # 無料枠の上限(4KB)対策
                "priority": 4,           # high: 通知音あり
                "tags": ["chart_with_upwards_trend"],
            },
            timeout=15,
        )
        if res.status_code == 200:
            log("📲 ntfy送信成功")
            return True
        log(f"⚠ ntfy送信失敗: HTTP {res.status_code} {res.text[:120]}")
        return False
    except Exception as e:
        log(f"⚠ ntfy送信失敗: {e}")
        return False


def send_discord(text):
    url = get_discord_url()
    if not url:
        return None  # 未設定(スキップ)
    try:
        # Discordは1メッセージ2000文字まで。コードブロックで等幅表示。
        content = "```\n" + text[:1900] + "\n```"
        res = requests.post(url, json={"content": content}, timeout=15)
        if res.status_code in (200, 204):
            log("💬 Discord送信成功")
            return True
        log(f"⚠ Discord送信失敗: HTTP {res.status_code} {res.text[:120]}")
        return False
    except Exception as e:
        log(f"⚠ Discord送信失敗: {e}")
        return False


def notify_all(subject, body):
    """設定済みの全チャネル(メール/Discord/ntfy)に送信。1つでも成功すれば True。"""
    results = []
    r_mail = send_email(subject, body)
    r_disc = send_discord(f"{subject}\n\n{body}")
    r_ntfy = send_ntfy(subject, body)
    for r in (r_mail, r_disc, r_ntfy):
        if r is not None:
            results.append(r)
    if not results:
        log("⚠ 通知先が未設定です(GMAIL_* / DISCORD_WEBHOOK_URL / NTFY_TOPIC のいずれも無し)")
        return False
    return any(results)


def format_body(new_signals, result):
    now = datetime.now()
    lines = []
    lines.append(f"🎯 新規買いシグナル {len(new_signals)}件  ({now.strftime('%m/%d %H:%M')})")
    lines.append(f"地合い: {result.get('regime', '?')}"
                 + ("  🔴HALT中" if result.get("is_halt") else ""))
    if result.get("vix"):
        lines.append(f"VIX: {result['vix']:.1f}")
    lines.append("=" * 36)
    for s in new_signals:
        em = STRAT_EMOJI.get(s["strategy"], "・")
        lines.append("")
        lines.append(f"{em} {s['name']} ({s['ticker']}) [{s['strategy']}]")
        lines.append(f"  買値   : ¥{s['entry_price']:,.1f}")
        lines.append(f"  損切り : ¥{s['stop_price']:,.1f}")
        if s["strategy"] == "MOMENTUM":
            # ★trail本番採用: +10%固定利確をやめ高値-10%トレールで伸ばす(検証済みPF 2.03)
            lines.append(f"  出口   : 高値-10%トレール(逆指値は朝ダイジェストで毎日更新)")
        else:
            lines.append(f"  目標   : ¥{s['target_price']:,.1f}")
        lines.append(f"  株数   : {s['shares']:,}株 (¥{s['cost']:,.0f})")
        lines.append(f"  根拠   : {s['info']}")
    lines.append("")
    lines.append("=" * 36)
    lines.append("※yfinance遅延(約15-20分)。発注前にSBIの板で現在値を確認。")
    return "\n".join(lines)


# ============================================================================
# ④ 市場時間ゲート / ログ
# ============================================================================
def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def market_open_now(force=False):
    if force:
        return True
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    t = now.hour * 60 + now.minute
    return (9 * 60) <= t <= (15 * 60 + 30)  # 9:00〜15:30(東証・昼休み含め通し)


# ============================================================================
# ④-2 trades.json(クラウド同期されたトレード記録)の読み込み
# ============================================================================
TRAIL_PCT = 10.0  # MOMENTUMトレール幅%(バックテスト検証済み: 8〜15%全幅でfixedに優位、10%採用)


def load_trades_json(path="trades.json"):
    """ダッシュボードが同期した trades.json から (保有中, クローズ済み) を返す"""
    if not path or not os.path.exists(path):
        return [], []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        trades = data.get("trades", data if isinstance(data, list) else [])
        opens = [t for t in trades if t.get("status") == "OPEN"]
        closed = [t for t in trades if t.get("status") == "CLOSED"]
        return opens, closed
    except Exception as e:
        log(f"⚠ trades.json 読込失敗: {e}")
        return [], []


def capital_from_trades(base_capital, path="trades.json"):
    """残高連動資金 = 初期資金 + 確定損益合計(1%リスクを実残高に追随させる)"""
    _, closed = load_trades_json(path)
    realized = sum(float(t.get("pnl") or 0) for t in closed)
    return base_capital + realized


# ============================================================================
# ④-3 朝のダイジェスト(保有のトレール逆指値を計算 → メール + stops.json)
# ============================================================================
def calc_today_stop(trade, hist_df):
    """保有1件の「今日の逆指値」を返す (eff_stop, peak)。
       MOMENTUM: max(初期ストップ, エントリー以降の高値×(1-10%)) … 切上げのみ。
       その他戦略: 登録済みストップのまま(トレール検証はMOMENTUMのみ)。"""
    entry = float(trade.get("entry_price") or 0)
    stop0 = float(trade.get("stop_price") or 0)
    strategy = (trade.get("strategy") or "").upper()
    peak = entry
    if hist_df is not None and len(hist_df):
        try:
            h = hist_df
            ed = trade.get("entry_date") or ""
            if ed:
                h = hist_df[hist_df.index >= pd.Timestamp(ed)]
            if len(h):
                peak = max(peak, float(h["High"].max()))
        except Exception:
            pass
    if strategy == "MOMENTUM" and entry > 0:
        eff = max(stop0, peak * (1 - TRAIL_PCT / 100.0))
    else:
        eff = stop0
    return round(eff, 1), round(peak, 1)


def run_digest(trades_path="trades.json", stops_path="stops.json",
               base_capital=1_000_000, dry_run=False, force=False):
    """朝ダイジェスト: 保有の現在値/含み損益/今日の逆指値を1通のメールに。
       同時に stops.json を書き出してダッシュボードにも反映する。"""
    # 同日二重送信ガード(外部クロック+GitHub遅延スケジュールの両発火対策)
    if not force and not dry_run and os.path.exists(stops_path):
        try:
            with open(stops_path, encoding="utf-8") as f:
                prev_updated = json.load(f).get("updated", "") or ""
            if prev_updated[:10] == datetime.now().strftime("%Y-%m-%d"):
                log("本日分のダイジェストは送信済み → スキップ(--force で再送可)")
                return
        except Exception:
            pass

    opens, closed = load_trades_json(trades_path)
    realized = sum(float(t.get("pnl") or 0) for t in closed)
    capital = base_capital + realized

    # 地合い(前日終値ベース)
    regime, vix_now = "UNKNOWN", None
    try:
        gd = ds.fetch_global_data()
        regime, _ = ds.detect_market_regime(gd, datetime.now().date())
        vix = gd.get("^VIX")
        if vix is not None and len(vix):
            v = float(vix["Close"].iloc[-1])
            vix_now = v if v == v else None
    except Exception as e:
        log(f"⚠ 地合い取得失敗: {e}")

    # 前回の逆指値(切上げ表示用)
    prev = {}
    if os.path.exists(stops_path):
        try:
            with open(stops_path, encoding="utf-8") as f:
                prev = json.load(f).get("stops", {})
        except Exception:
            pass

    stops = {}
    pos_lines = []
    raised = 0
    alerts = []
    for t in opens:
        ticker = t.get("ticker") or ""
        name = t.get("name") or ticker
        shares = int(t.get("shares") or 0)
        entry = float(t.get("entry_price") or 0)
        strategy = (t.get("strategy") or "?")
        em = STRAT_EMOJI.get(strategy, "・")

        hist = None
        cur = None
        try:
            hist = ds.yf.download(ticker, period="6mo", interval="1d",
                                  progress=False, auto_adjust=False)
            if hist is not None and len(hist):
                if isinstance(hist.columns, pd.MultiIndex):
                    hist.columns = hist.columns.get_level_values(0)
                cur = float(hist["Close"].dropna().iloc[-1])
        except Exception:
            pass

        eff_stop, peak = calc_today_stop(t, hist)
        tid = str(t.get("id") or ticker)
        prev_stop = None
        try:
            prev_stop = float(prev.get(tid, {}).get("stop") or 0) or None
        except Exception:
            pass

        stops[tid] = {"ticker": ticker, "name": name, "strategy": strategy,
                      "stop": eff_stop, "peak": peak,
                      "cur": round(cur, 1) if cur else None}

        pos_lines.append("")
        pos_lines.append(f"{em} {name} ({ticker.replace('.T','')}) [{strategy}] {shares}株")
        if cur and entry > 0:
            upnl = (cur - entry) * shares
            upct = (cur - entry) / entry * 100
            sign = "+" if upnl >= 0 else ""
            pos_lines.append(f"  現在値 ¥{cur:,.1f}  含み {sign}¥{upnl:,.0f} ({sign}{upct:.1f}%)")
        if eff_stop > 0:
            mark = ""
            if prev_stop and eff_stop > prev_stop:
                mark = f" ⬆ 切上げ(昨日 ¥{prev_stop:,.1f})"
                raised += 1
            pos_lines.append(f"  📌 今日の逆指値: ¥{eff_stop:,.1f}{mark}")
            if cur and cur <= eff_stop:
                alerts.append(f"🚨 {name}: 現在値が逆指値以下。寄りでの決済を検討")
        else:
            pos_lines.append("  (ストップ未設定 → ダッシュボードで設定推奨)")

    # 本文組み立て
    now = datetime.now()
    wd = ["月", "火", "水", "木", "金", "土", "日"][now.weekday()]
    lines = [f"☀️ 朝のダイジェスト {now.strftime('%m/%d')}({wd})"]
    lines.append(f"地合い: {regime}" + (f" / VIX {vix_now:.1f}" if vix_now else ""))
    if regime in ("BEARISH", "PANIC"):
        lines.append(f"⚠️ 地合い悪化中。新規は慎重に、保有は撤退も検討。")
    lines.append(f"推定残高: ¥{capital:,.0f}(確定損益 {'+' if realized>=0 else ''}¥{realized:,.0f})")
    lines.append("=" * 36)
    if opens:
        lines.append(f"━━ 保有 {len(opens)}件(SBIで逆指値を確認/訂正)━━")
        lines.extend(pos_lines)
        if alerts:
            lines.append("")
            lines.extend(alerts)
    else:
        lines.append("保有ポジションなし(trades.json 未同期の場合はダッシュボードで同期)")
    lines.append("")
    lines.append("=" * 36)
    lines.append("※MOMENTUMは高値-10%トレール。逆指値は切上げのみ(下げない)。")
    lines.append("※価格は前日終値ベース。発注前にSBIの板で確認。")
    body = "\n".join(lines)

    subject = f"☀️ 朝ダイジェスト 保有{len(opens)}件" + (f"・逆指値切上げ{raised}件" if raised else "") + f" ({regime})"

    # stops.json 書き出し(ダッシュボードの「今日の逆指値」表示用)
    if not dry_run:
        try:
            with open(stops_path, "w", encoding="utf-8") as f:
                json.dump({"updated": now.isoformat(timespec="seconds"),
                           "regime": regime, "stops": stops}, f, ensure_ascii=False)
            log(f"📌 stops.json 書き出し: {len(stops)}件")
        except Exception as e:
            log(f"⚠ stops.json 保存失敗: {e}")

    if dry_run:
        print("\n" + "-" * 50)
        print(f"Subject: {subject}")
        print(body)
        print("-" * 50 + "\n")
        return

    notify_all(subject, body)


# ============================================================================
# ⑤ 現在値の書き出し(ダッシュボードの prices.json 用)
# ============================================================================
def dump_prices(path):
    """全銘柄の最新値を一括取得して prices.json に書き出す(ダッシュボードの現在値表示用)"""
    tickers = list(ds.STOCKS.keys())
    prices = {}
    try:
        data = ds.yf.download(tickers, period="2d", interval="1d",
                              auto_adjust=False, progress=False,
                              group_by="ticker", threads=True)
    except Exception as e:
        log(f"⚠ prices一括取得失敗: {e}")
        return
    multi = isinstance(getattr(data, "columns", None), pd.MultiIndex)
    for tk in tickers:
        try:
            sub = data[tk] if multi else data
            close = sub["Close"].dropna()
            if len(close):
                prices[tk] = round(float(close.iloc[-1]), 1)
        except Exception:
            pass
    payload = {
        "updated": datetime.now().isoformat(timespec="seconds"),
        "count": len(prices),
        "prices": prices,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        log(f"💹 prices.json 書き出し: {len(prices)}銘柄")
    except Exception as e:
        log(f"⚠ prices.json 保存失敗: {e}")


# ============================================================================
# ⑥ 1サイクル(スキャン → 新規シグナルだけ通知)
# ============================================================================
def run_once(capital, risk_pct, dry_run=False, force=False, log_csv=None, prices_json=None):
    if not market_open_now(force=force):
        log("市場時間外のためスキップ(--force で強制実行可)")
        return

    if prices_json and not dry_run:
        dump_prices(prices_json)

    use_csv = bool(log_csv)
    seen = load_seen_csv(log_csv) if use_csv else load_seen_json()
    log(f"スキャン開始 …(重複排除: {'CSV ' + log_csv if use_csv else 'JSON'} / 既通知{len(seen)}件)")

    def progress(n, total, name):
        if n % 50 == 0 and n > 0:
            log(f"  …{n}/{total}")

    result = ds.scan(capital=capital, risk_pct=risk_pct, progress_callback=progress)
    if "error" in result:
        log(f"⚠ スキャン失敗: {result['error']}")
        return

    scan_date = result.get("timestamp", datetime.now().isoformat())[:10]
    signals = result.get("signals", [])

    def is_new(s):
        if use_csv:
            return f"{scan_date}_{s['ticker']}_{s['strategy']}" not in seen
        return f"{s['ticker']}_{s['strategy']}" not in seen

    new_signals = [s for s in signals if is_new(s)]
    log(f"スキャン完了: 地合い={result.get('regime')} "
        f"全{len(signals)}件 / 新規{len(new_signals)}件")

    if not new_signals:
        return

    for s in new_signals:
        log(f"  ★新規: {s['name']} ({s['ticker']}) [{s['strategy']}] @¥{s['entry_price']:,.1f}")

    subject = f"🎯 新規買いシグナル {len(new_signals)}件 ({result.get('regime')})"
    body = format_body(new_signals, result)

    if dry_run:
        log("--dry-run: 通知せず本文表示")
        print("\n" + "-" * 50)
        print(f"Subject: {subject}")
        print(body)
        print("-" * 50 + "\n")
        return

    if notify_all(subject, body):
        if use_csv:
            append_signals_csv(log_csv, new_signals, result)
        else:
            for s in new_signals:
                seen.add(f"{s['ticker']}_{s['strategy']}")
            save_seen_json(seen)


# ============================================================================
# ⑥ 常駐ループ
# ============================================================================
def run_loop(capital, risk_pct, interval_min, dry_run=False, force=False, log_csv=None, prices_json=None):
    log(f"常駐監視を開始(間隔{interval_min}分 / 平日9:00〜15:30)。Ctrl+Cで停止。")
    while True:
        try:
            run_once(capital, risk_pct, dry_run=dry_run, force=force,
                     log_csv=log_csv, prices_json=prices_json)
        except KeyboardInterrupt:
            log("停止しました。")
            break
        except Exception as e:
            log(f"⚠ サイクル例外: {e}")
        time.sleep(interval_min * 60)


# ============================================================================
def parse_args():
    p = argparse.ArgumentParser(description="ザラ場シグナル即時通知(メール+Discord)")
    p.add_argument("--once", action="store_true", help="1回だけ実行して終了")
    p.add_argument("--interval", type=int, default=15, help="ループ間隔(分・デフォルト15)")
    p.add_argument("--capital", type=float, default=1_000_000, help="運用資金(円)")
    p.add_argument("--risk", type=float, default=1.0, help="1トレードリスク(%)")
    p.add_argument("--log-csv", default=None,
                   help="signals_log.csv で重複排除&追記(GitHub Actions向け)")
    p.add_argument("--prices-json", default=None,
                   help="全銘柄の最新値を prices.json に書き出す(ダッシュボードの現在値表示用)")
    p.add_argument("--digest", action="store_true",
                   help="朝ダイジェスト: 保有の逆指値(トレール)を計算してメール+stops.json")
    p.add_argument("--trades-json", default="trades.json",
                   help="トレード記録(クラウド同期)ファイル")
    p.add_argument("--stops-json", default="stops.json",
                   help="今日の逆指値の書き出し先")
    p.add_argument("--capital-from-trades", action="store_true",
                   help="資金を 初期資金+確定損益 に自動連動(推奨株数の計算が実残高に追随)")
    p.add_argument("--dry-run", action="store_true", help="通知せず判定のみ表示")
    p.add_argument("--force", action="store_true", help="市場時間ゲートを無視")
    p.add_argument("--test", action="store_true", help="全チャネルにテスト通知")
    return p.parse_args()


def main():
    args = parse_args()

    if args.test:
        ok = notify_all("✅ テスト通知 (realtime_notifier)",
                        "これは realtime_notifier.py の接続テストです。\n"
                        "メール / Discord のうち設定済みチャネルに届きます。")
        sys.exit(0 if ok else 1)

    if args.digest:
        # 朝ダイジェストは前日終値ベース(intraday差し替え不要)
        run_digest(trades_path=args.trades_json, stops_path=args.stops_json,
                   base_capital=args.capital, dry_run=args.dry_run, force=args.force)
        return

    enable_intraday_fetch()

    capital = args.capital
    if args.capital_from_trades:
        capital = capital_from_trades(args.capital, args.trades_json)
        log(f"💰 残高連動資金: ¥{capital:,.0f}(初期¥{args.capital:,.0f}+確定損益)")

    if args.once:
        run_once(capital, args.risk, dry_run=args.dry_run,
                 force=args.force, log_csv=args.log_csv, prices_json=args.prices_json)
    else:
        run_loop(capital, args.risk, args.interval,
                 dry_run=args.dry_run, force=args.force,
                 log_csv=args.log_csv, prices_json=args.prices_json)


if __name__ == "__main__":
    main()
