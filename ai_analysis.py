# -*- coding: utf-8 -*-
"""AI相場分析ジェネレーター(株レーダー kaburadar.jp 用)

毎朝、客観データを集めてLLMに渡し、個人トレーダー向けの市況分析JSONを
docs/ai_analysis.json に出力する。GitHub Actionsから実行される(PC不要)。

入力データ:
  1. 主要指数・VIX・ドル円・米10年債・金・原油の1日/5日変化
  2. CME日経先物と現物の乖離 = 寄り付き想定
  3. 前営業日に資金が流入したセクター(出来高急増×上昇・当サイト集計)
  4. 運営者の自動売買システムの地合い判定
  5. 今後7日以内の大型イベント
  6. 当サイト独自の機関投資家データ(CFTC投機筋・JPX投資部門別)
  7. 前回のAI分析(答え合わせ用)
  8. NHK/Yahooの経済・国際ニュース見出し

ガードレール:
  - 個別銘柄の言及・推奨は禁止(プロンプトで強制 + 出力検証で弾く)
  - セクター単位まで。断定でなく可能性の表現
  - 投資助言ではない旨をサイト側で常時表示

使い方:
  ANTHROPIC_API_KEY=xxx python ai_analysis.py docs/ai_analysis.json
  GEMINI_API_KEY=xxx    python ai_analysis.py docs/ai_analysis.json
  (両方あればANTHROPIC_API_KEYを優先)
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
PAGES = "https://oo12takemaru-create.github.io/stock-trading-"
DOCS = Path("docs")

# モデルは環境変数で差し替え可能(コストと品質のトレードオフ)
#   claude-opus-5    $5 / $25 per 1M tokens (既定・最高品質)
#   claude-sonnet-5  $3 / $15 (2026-08-31まで導入価格 $2 / $10)
#   claude-haiku-4-5 $1 / $5  (最安)
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-opus-5"
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip() or "gemini-2.5-flash"

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

# 大型イベント(kaburadar/events.jsと同期。年1回程度の更新でよい)
EVENTS = [
    ("2026-08-07", "米雇用統計(7月分) 21:30"),
    ("2026-08-12", "米CPI(7月分) 21:30"),
    ("2026-09-04", "米雇用統計(8月分) 21:30"),
    ("2026-09-11", "メジャーSQ(9月限) 寄付"),
    ("2026-09-11", "米CPI(8月分) 21:30"),
    ("2026-09-17", "FOMC結果発表 午前3:00"),
    ("2026-09-18", "日銀会合 結果発表 昼ごろ"),
    ("2026-10-02", "米雇用統計(9月分) 21:30"),
    ("2026-10-14", "米CPI(9月分) 21:30"),
    ("2026-10-29", "FOMC結果発表 午前3:00"),
    ("2026-10-30", "日銀会合 結果発表 昼ごろ"),
    ("2026-11-06", "米雇用統計(10月分) 22:30"),
    ("2026-11-10", "米CPI(10月分) 22:30"),
    ("2026-12-04", "米雇用統計(11月分) 22:30"),
    ("2026-12-10", "FOMC結果発表 午前4:00"),
    ("2026-12-10", "米CPI(11月分) 22:30"),
    ("2026-12-11", "メジャーSQ(12月限) 寄付"),
    ("2026-12-18", "日銀会合 結果発表 昼ごろ"),
]


# ---------------------------------------------------------------- データ取得

def load_site_json(name):
    """docs/配下のJSONを読む。Actions内ではcheckout済みのローカルを優先し、
    無ければGitHub Pages経由で取りに行く(ローカル実行時のフォールバック)"""
    p = DOCS / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ローカル読込失敗 {name}: {e}", file=sys.stderr)
    try:
        req = urllib.request.Request(f"{PAGES}/{name}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:
        print(f"取得失敗 {name}: {e}", file=sys.stderr)
        return None


def fetch_market():
    lines = []
    try:
        import yfinance as yf
    except ImportError:
        return ["(yfinance未導入)"]
    for ticker, label in MARKET_TICKERS.items():
        try:
            df = yf.download(ticker, period="10d", progress=False, auto_adjust=False)
            if df is None or len(df) < 2:
                continue
            closes = df["Close"].dropna()
            if len(closes) < 2:
                continue

            def val(i):
                v = closes.iloc[i]
                return float(v.item() if hasattr(v, "item") else v)

            last, prev = val(-1), val(-2)
            d1 = (last / prev - 1) * 100 if prev else 0
            s = f"{label}: {last:,.2f} (前日{d1:+.1f}%"
            if len(closes) >= 6:
                p5 = val(-6)
                if p5:
                    s += f" / 5日{(last / p5 - 1) * 100:+.1f}%"
            s += ")"
            lines.append(s)
        except Exception:
            lines.append(f"{label}: (取得失敗)")
    return lines


def futures_gap_lines():
    """CME日経先物(円建てNIY=F)と現物の乖離 → 寄り付きの目安"""
    try:
        import yfinance as yf
        fut = yf.download("NIY=F", period="5d", progress=False, auto_adjust=False)
        cash = yf.download("^N225", period="5d", progress=False, auto_adjust=False)
        if fut is None or cash is None or len(fut) == 0 or len(cash) == 0:
            return ["(先物データ取得失敗)"]

        def last(df):
            v = df["Close"].dropna().iloc[-1]
            return float(v.item() if hasattr(v, "item") else v)

        f, c = last(fut), last(cash)
        gap = (f / c - 1) * 100
        return [
            f"CME日経先物(円建て): {f:,.0f}円 / 現物終値: {c:,.0f}円 / 乖離 {gap:+.2f}%",
            f"→ 先物ベースの寄り付き想定: 現物比{gap:+.1f}%程度({f:,.0f}円近辺)でのスタートが示唆される",
        ]
    except Exception as e:
        return [f"(先物データ取得失敗: {e})"]


def regime_line():
    d = load_site_json("radar.json")
    if not d:
        return "システム地合い判定: 取得失敗"
    s = (f"システム地合い判定: {d.get('regime')} / VIX {d.get('vix')} / "
         f"日経 {d.get('n225')} / 買い候補 {d.get('signal_count')}件 / 更新 {d.get('updated')}")
    if d.get("is_halt"):
        s += f" / ★サーキットブレーカー発動中({d.get('halt_reason')})"
    return s


def flow_ranking_lines():
    """前営業日の資金流入(出来高急増×上昇)のセクター分布。
    ※銘柄名はAIに出力させないため、セクター集計のみ渡す"""
    d = load_site_json("heatmap.json")
    if not d:
        return ["(取得失敗)"]
    ranked = [i for i in d.get("items", []) if i.get("r") and i.get("c", 0) > 0]
    ranked.sort(key=lambda x: -x["r"])
    top = ranked[:20]
    if not top:
        return ["(該当なし)"]
    secs = {}
    for i in top:
        secs.setdefault(i["s"], []).append(i)
    lines = [f"出来高急増×上昇の上位20銘柄のセクター分布(データ更新 {d.get('updated', '')[:16]}):"]
    for s, arr in sorted(secs.items(), key=lambda kv: -len(kv[1])):
        mx = max(a["r"] for a in arr)
        lines.append(f"  {s}: {len(arr)}銘柄 (最大出来高{mx:.1f}倍)")
    return lines


def upcoming_events():
    today = datetime.now(JST).date()
    lines = []
    for d, label in EVENTS:
        ev = date.fromisoformat(d)
        delta = (ev - today).days
        if 0 <= delta <= 7:
            when = "今日" if delta == 0 else ("明日" if delta == 1 else f"{delta}日後({ev.month}/{ev.day})")
            lines.append(f"{when}: {label}")
    return lines or ["(7日以内の大型イベントなし)"]


def institutional_lines():
    """CFTC投機筋 + JPX投資部門別のサマリー(当サイト独自集計)"""
    lines = []
    cot = load_site_json("cot.json")
    if cot:
        lines.append(f"[CFTC投機筋ポジション {cot.get('report_date')}時点]")
        for it in cot.get("items", []):
            s = f"  {it['label']}: ネット{it['net']:+,}枚"
            if it.get("change") is not None:
                s += f" (前週比{it['change']:+,})"
            lines.append(s)
    flow = load_site_json("investor_flow.json")
    if flow:
        lines.append(f"[JPX投資部門別売買 {flow.get('week', '')}]")
        for it in flow.get("items", []):
            s = f"  {it['label']}: ネット{it['net_oku']:+,}億円"
            if it.get("prev_net_oku") is not None:
                s += f" (前週{it['prev_net_oku']:+,}億円)"
            lines.append(s)
    return lines or ["(取得失敗)"]


def prev_analysis(dst):
    """前回のAI分析(答え合わせ用)。出力先ファイルのlatestを見る"""
    try:
        old = json.loads(Path(dst).read_text(encoding="utf-8"))
        L = old.get("latest")
        if not L:
            return None, ["(前回分析なし)"]
        return L, [
            f"日付: {L.get('date')}",
            f"スタンス: {L.get('stance')}",
            f"見出し: {L.get('headline')}",
            f"根拠: {L.get('stance_reason', '')[:200]}",
            f"注目点: {L.get('today_watch', '')[:200]}",
        ]
    except Exception:
        return None, ["(前回分析なし)"]


def fetch_rss(max_per_feed=8):
    out = []
    for label, url in RSS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                root = ET.fromstring(r.read())
            n = 0
            for it in root.findall(".//item"):
                t = it.find("title")
                if t is not None and t.text:
                    out.append(f"[{label}] {t.text.strip()}")
                    n += 1
                if n >= max_per_feed:
                    break
        except Exception as e:
            out.append(f"[{label}] (取得失敗: {e})")
    return out


def build_inputs(dst):
    now = datetime.now(JST)
    prev, prev_lines = prev_analysis(dst)
    parts = [
        f"# AI朝刊 入力データ ({now.strftime('%Y-%m-%d %H:%M')} JST)",
        "",
        "## 市場の値動き",
        *fetch_market(),
        "",
        "## 運営者システムの地合い判定",
        regime_line(),
        "",
        "## 日経先物(CME)と寄り付き想定",
        *futures_gap_lines(),
        "",
        "## 前営業日に資金が流入したセクター(出来高急増×上昇・当サイト集計)",
        *flow_ranking_lines(),
        "",
        "## 今後7日以内の大型イベント(日本時間)",
        *upcoming_events(),
        "",
        "## 機関投資家・投機筋データ(当サイト独自集計・週次)",
        *institutional_lines(),
        "",
        "## 前回のAI分析(答え合わせ用)",
        *prev_lines,
        "",
        "## 直近ニュース見出し",
        *fetch_rss(),
    ]
    return "\n".join(parts), prev


# ---------------------------------------------------------------- プロンプト

PROMPT_TEMPLATE = """あなたは日本株市場を専門とするマクロアナリストです。個人トレーダー向けに、今朝の市況分析を日本語で作成してください。

# 厳守事項(違反は機械検証で弾かれます)
- 個別銘柄名・証券コードは絶対に出さない。言及はセクター・業種単位まで
- 「買うべき」「売るべき」「必ず上がる」「確実に上がる」等の断定・売買指示は書かない
- 与えられた客観データにない事実を創作しない。「〜の可能性」「〜になりやすい」の表現を使う

# 書き方の方針
- 当サイト独自の素材を積極的に織り込む: 投機筋ポジションの偏り(巻き戻しリスク)、海外投資家の売買転換、
  前営業日の資金流入セクター(sectorsの根拠に使える)、先物ギャップ(today_watchで寄り付き想定として使う)
- 資金流入データはセクター単位で言及する。個別銘柄名は入力に含まれていても絶対に出力しない
- stance_reasonやbodyでは、必ず入力データの具体的な数字を引用する
- reviewは、前回分析が入力にある場合は必須。外れた時こそ正直に書く(信頼の源泉)

# 今朝の客観データ

{inputs}

# 出力
次のJSONスキーマちょうどの形で出力してください。日本語で書き、JSON以外は一切出力しないこと。

{{
  "headline": "今日の相場を一言で(30字以内)",
  "stance": "attack | neutral | defense のいずれか(attack=リスクを取りやすい環境, neutral=中立, defense=守りを固めたい環境)",
  "stance_reason": "そのスタンスの根拠を2〜3文で。データの数字を引用する。システムの地合い判定と矛盾する場合はその理由も説明する",
  "review": {{"prev_date": "前回の日付", "prev_stance": "前回のstance", "result": "前回の見立てが実際どうだったかを1〜2文で正直に検証。市場データの数字で答え合わせする。当たった・外れた・まだ判定できない、を率直に"}},
  "today_watch": "今日どこを見るべきか(チェックポイント)を1〜2文。先物ベースの寄り付き想定・大型イベントの時刻・注目している数字を具体的に",
  "world_flow": [
    {{"theme": "大きな流れのタイトル(金利・地政学・為替など)", "body": "それが日本株にどう効きそうかを1〜2文で"}}
  ],
  "sectors": [
    {{"name": "セクター名", "bias": "up | down | watch", "reason": "理由を1文で"}}
  ],
  "caution": "今日特に気をつけたいことを1〜2文で"
}}

world_flowは2〜4個、sectorsは3〜5個。JSONのみを出力すること。"""


# ---------------------------------------------------------------- 検証

BANNED_PATTERNS = [
    r"\d{4}\.T", r"（\d{4}）", r"\(\d{4}\)",  # 証券コード
    r"買うべき", r"売るべき", r"必ず上が", r"確実に上が",
]


def validate(data):
    if not isinstance(data, dict):
        return "dictでない"
    for k in ("headline", "stance", "stance_reason", "world_flow", "sectors", "caution", "today_watch"):
        if k not in data:
            return f"キー欠落: {k}"
    if data.get("review"):
        for k in ("prev_date", "prev_stance", "result"):
            if k not in data["review"]:
                return f"reviewのキー欠落: {k}"
    if data["stance"] not in ("attack", "neutral", "defense"):
        return f"stance不正: {data['stance']}"
    if not (1 <= len(data["world_flow"]) <= 6) or not (1 <= len(data["sectors"]) <= 8):
        return "配列サイズ不正"
    blob = json.dumps(data, ensure_ascii=False)
    for pat in BANNED_PATTERNS:
        if re.search(pat, blob):
            return f"禁止パターン検出: {pat}"
    return None


# ---------------------------------------------------------------- LLM呼び出し

def _post_json(url, body, headers, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _extract_json(text):
    """```json フェンス等が付いていても中のJSONを取り出す"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def call_anthropic(prompt, key):
    # 注意(Claude 5世代の仕様):
    #  - temperature / top_p / top_k は送ると400エラーになるので付けない
    #  - thinkingは既定でオン。max_tokensは「思考+本文」の合計上限なので
    #    JSONが途中で切れないよう十分な余裕を取る
    res = _post_json(
        "https://api.anthropic.com/v1/messages",
        {
            "model": ANTHROPIC_MODEL,
            "max_tokens": 16000,
            "output_config": {"effort": "medium"},
            "messages": [{"role": "user", "content": prompt}],
        },
        {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    if res.get("stop_reason") == "refusal":
        raise RuntimeError("安全性フィルタにより生成が拒否されました")
    if res.get("stop_reason") == "max_tokens":
        raise RuntimeError("max_tokens に達して出力が途中で切れました")
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    return _extract_json(text)


def call_gemini(prompt, key):
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           + GEMINI_MODEL + f":generateContent?key={key}")
    res = _post_json(
        url,
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "response_mime_type": "application/json",
                "temperature": 0.4,
                "maxOutputTokens": 4096,
            },
        },
        {"Content-Type": "application/json"},
    )
    text = res["candidates"][0]["content"]["parts"][0]["text"]
    return _extract_json(text)


def pick_provider():
    ak = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if ak:
        return "Claude " + ANTHROPIC_MODEL, lambda p: call_anthropic(p, ak)
    gk = os.environ.get("GEMINI_API_KEY", "").strip()
    if gk:
        return "Gemini " + GEMINI_MODEL, lambda p: call_gemini(p, gk)
    return None, None


# ---------------------------------------------------------------- メイン

def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/ai_analysis.json"

    label, call = pick_provider()
    if call is None:
        print("ANTHROPIC_API_KEY も GEMINI_API_KEY も未設定", file=sys.stderr)
        sys.exit(1)
    print(f"使用モデル: {label}")

    now = datetime.now(JST)
    date_str = now.strftime("%Y-%m-%d")

    inputs, prev = build_inputs(dst)
    print(f"入力データ: {len(inputs)}文字")
    if len(inputs) < 300:
        print("入力データが少なすぎるため中止", file=sys.stderr)
        sys.exit(1)
    # make_x_post.py が数字を拾えるよう入力データを残す(デバッグにも使う)
    Path("inputs.txt").write_text(inputs, encoding="utf-8")

    prompt = PROMPT_TEMPLATE.format(inputs=inputs)

    data = None
    err = None
    for attempt in range(3):
        try:
            cand = call(prompt)
            err = validate(cand)
            if err is None:
                data = cand
                break
            print(f"検証NG(試行{attempt + 1}): {err}", file=sys.stderr)
        except Exception as e:
            err = str(e)
            print(f"生成失敗(試行{attempt + 1}): {e}", file=sys.stderr)
    if data is None:
        print(f"AI分析の生成に失敗: {err}", file=sys.stderr)
        sys.exit(1)

    entry = {
        "date": date_str,
        "updated": now.isoformat(timespec="seconds"),
        "model": label,
        **data,
    }

    # 既存の履歴を引き継ぐ(直近7件)
    history = []
    try:
        old = json.loads(Path(dst).read_text(encoding="utf-8"))
        history = old.get("history", [])
        if prev and prev.get("date") != date_str:
            history = [prev] + history
        history = history[:7]
    except Exception:
        pass

    out = {"latest": entry, "history": history}
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"AI分析を出力: {date_str} stance={data['stance']} sectors={len(data['sectors'])}")


if __name__ == "__main__":
    main()
