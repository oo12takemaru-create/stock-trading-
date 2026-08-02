"""AI相場分析ジェネレーター(株レーダー kaburadar.jp 用)

毎朝、客観データ(指数・金利・為替・商品・VIX・地合い判定・ニュース見出し)を集めて
Gemini に渡し、個人トレーダー向けの朝の市況分析JSONを docs/ai_analysis.json に出力する。

ガードレール:
  - 個別銘柄の言及・推奨は禁止(プロンプトで強制+出力検証)
  - セクター単位まで。断定でなく可能性の表現
  - 投資助言ではない旨をサイト側で常時表示

使い方: GEMINI_API_KEY=xxx python ai_analysis.py docs/ai_analysis.json
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + GEMINI_MODEL + ":generateContent?key={key}"
)

RSS_FEEDS = [
    ("NHK 経済",  "https://www3.nhk.or.jp/rss/news/cat5.xml"),
    ("NHK 国際",  "https://www3.nhk.or.jp/rss/news/cat6.xml"),
    ("Yahoo 経済", "https://news.yahoo.co.jp/rss/topics/business.xml"),
    ("Yahoo 国際", "https://news.yahoo.co.jp/rss/topics/world.xml"),
]

MARKET_TICKERS = {
    "^N225":  "日経平均",
    "^GSPC":  "S&P500",
    "^IXIC":  "ナスダック",
    "^VIX":   "VIX",
    "JPY=X":  "ドル円",
    "^TNX":   "米10年債利回り",
    "GC=F":   "金先物",
    "CL=F":   "WTI原油",
}


def fetch_rss_headlines(max_per_feed=8):
    headlines = []
    for label, url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
            # RSS2.0 (channel/item/title) と RDF 両対応
            items = root.findall(".//item")
            count = 0
            for it in items:
                t = it.find("title")
                if t is not None and t.text:
                    headlines.append(f"[{label}] {t.text.strip()}")
                    count += 1
                if count >= max_per_feed:
                    break
        except Exception as e:
            print(f"RSS取得失敗 {label}: {e}", file=sys.stderr)
    return headlines


def fetch_market_moves():
    """yfinanceで主要指標の1日/5日変化率を取得"""
    try:
        import yfinance as yf
    except ImportError:
        return []
    lines = []
    for ticker, label in MARKET_TICKERS.items():
        try:
            df = yf.download(ticker, period="10d", progress=False, auto_adjust=False)
            if df is None or len(df) < 2:
                continue
            closes = df["Close"]
            if hasattr(closes, "iloc") and hasattr(closes.iloc[-1], "item"):
                last = float(closes.iloc[-1].item() if hasattr(closes.iloc[-1], "item") else closes.iloc[-1])
            else:
                last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2].item() if hasattr(closes.iloc[-2], "item") else closes.iloc[-2])
            d1 = (last / prev - 1) * 100 if prev else 0
            d5 = None
            if len(closes) >= 6:
                p5 = float(closes.iloc[-6].item() if hasattr(closes.iloc[-6], "item") else closes.iloc[-6])
                d5 = (last / p5 - 1) * 100 if p5 else None
            s = f"{label}: {last:,.2f} (前日{d1:+.1f}%"
            if d5 is not None:
                s += f" / 5日{d5:+.1f}%"
            s += ")"
            lines.append(s)
        except Exception as e:
            print(f"市場データ取得失敗 {label}: {e}", file=sys.stderr)
    return lines


def load_regime():
    try:
        with open("docs/radar.json", encoding="utf-8") as f:
            d = json.load(f)
        return f"システム地合い判定: {d.get('regime')} / VIX {d.get('vix')} / 買い候補 {d.get('signal_count')}件 (更新 {d.get('updated')})"
    except Exception:
        return "システム地合い判定: データなし"


PROMPT_TEMPLATE = """あなたは日本株市場を専門とするマクロアナリストです。個人トレーダー向けに、今朝の市況分析を日本語で作成してください。

# 厳守事項
- 個別銘柄名・証券コードは絶対に出さない。言及はセクター・業種単位まで
- 「必ず上がる」等の断定をしない。「〜の可能性」「〜になりやすい」の表現を使う
- 与えられた客観データに基づく。データにない事実を創作しない
- 特定の売買を指示しない(「買うべき」「売るべき」禁止)。判断材料の整理に徹する

# 今朝の客観データ({date} 日本時間)

## 市場の値動き
{market}

## 運営者の自動売買システムの判定
{regime}

## 直近のニュース見出し
{news}

# 出力
次のJSONスキーマで出力してください。日本語で書いてください。

{{
  "headline": "今日の相場を一言で(30字以内)",
  "stance": "attack | neutral | defense のいずれか(attack=リスクを取りやすい環境, neutral=中立, defense=守りを固めたい環境)",
  "stance_reason": "そのスタンスの根拠を2〜3文で。数字を引用する",
  "world_flow": [
    {{"theme": "大きな流れのタイトル(金利・地政学・為替など)", "body": "それが日本株にどう効きそうかを1〜2文で"}}
  ],
  "sectors": [
    {{"name": "セクター名", "bias": "up | down | watch", "reason": "理由を1文で"}}
  ],
  "caution": "今日特に気をつけたいことを1〜2文で"
}}

world_flowは2〜4個、sectorsは3〜5個。JSONのみを出力すること。"""


BANNED_PATTERNS = [
    r"\d{4}\.T", r"（\d{4}）", r"\(\d{4}\)",  # 証券コード
    r"買うべき", r"売るべき", r"必ず上が", r"確実に上が",
]


def validate(data):
    if not isinstance(data, dict):
        return "dictでない"
    for k in ("headline", "stance", "stance_reason", "world_flow", "sectors", "caution"):
        if k not in data:
            return f"キー欠落: {k}"
    if data["stance"] not in ("attack", "neutral", "defense"):
        return f"stance不正: {data['stance']}"
    if not (1 <= len(data["world_flow"]) <= 6) or not (1 <= len(data["sectors"]) <= 8):
        return "配列サイズ不正"
    blob = json.dumps(data, ensure_ascii=False)
    for pat in BANNED_PATTERNS:
        if re.search(pat, blob):
            return f"禁止パターン検出: {pat}"
    return None


def call_gemini(prompt, key):
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.4,
            "maxOutputTokens": 2048,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_URL.format(key=key),
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.load(r)
    text = res["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/ai_analysis.json"
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        print("GEMINI_API_KEY が未設定", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(JST)
    date_str = now.strftime("%Y-%m-%d")

    news = fetch_rss_headlines()
    market = fetch_market_moves()
    regime = load_regime()

    if not market and not news:
        print("入力データが空のため中止", file=sys.stderr)
        sys.exit(1)

    prompt = PROMPT_TEMPLATE.format(
        date=now.strftime("%Y-%m-%d %H:%M"),
        market="\n".join(market) or "(取得失敗)",
        regime=regime,
        news="\n".join(news) or "(取得失敗)",
    )

    data = None
    err = None
    for attempt in range(2):
        try:
            cand = call_gemini(prompt, key)
            err = validate(cand)
            if err is None:
                data = cand
                break
            print(f"検証NG(試行{attempt+1}): {err}", file=sys.stderr)
        except Exception as e:
            err = str(e)
            print(f"生成失敗(試行{attempt+1}): {e}", file=sys.stderr)
    if data is None:
        print(f"AI分析の生成に失敗: {err}", file=sys.stderr)
        sys.exit(1)

    entry = {
        "date": date_str,
        "updated": now.isoformat(timespec="seconds"),
        **data,
    }

    # 既存の履歴を引き継ぐ(直近7件)
    history = []
    try:
        with open(dst, encoding="utf-8") as f:
            old = json.load(f)
        prev_latest = old.get("latest")
        history = old.get("history", [])
        if prev_latest and prev_latest.get("date") != date_str:
            history = [prev_latest] + history
        history = history[:7]
    except Exception:
        pass

    out = {"latest": entry, "history": history}
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"AI分析を出力: {date_str} stance={data['stance']} sectors={len(data['sectors'])}")


if __name__ == "__main__":
    main()
