# -*- coding: utf-8 -*-
"""週末版のAI朝刊 → docs/ai_weekly.json（株レーダー）

土曜: 週末版（2026-10-10〜。答え合わせと来週の想定を1本にまとめた）
      主役は「来週の地図」——方向感（上寄り/横ばい/下寄り）・世界→日本株の伝わり方・
      見立てを変える条件・日別の見どころ・セクターの風向き。今週の答え合わせは添えるだけ。
      出した方向感は翌週の土曜に機械が採点する（views。金曜終値→翌週金曜終値）。
日曜: 来週の想定（旧形式。2026-10-10 に週末版へ統合して定期実行は止めた。手動では動く）

■ 答え合わせは機械が採点する
  スタンスと実測リターンの突き合わせは ai_record.json / ai_analysis.json から
  機械的に作る。AIに任せるのは「なぜ外れたか」の文章だけ。
  こうしないと、AIが自分の成績を都合よく書ける。

■ 採点ルール（平日版の成績表と同じ）
  attack / lean_attack を出した日 → その日のリターンがプラスなら○
  defense / lean_defense を出した日 → マイナスなら○
  neutral は方向を持たないので分母に入れない（判定不能）

■ 土日は市場が動かないので、金曜引け時点の数値で作る（ページにも明記する）

使い方: python weekly.py sat|sun [出力パス]

API を使わない2段階モード（2026-10-05〜 本番。Claude Code 定期実行が文章を書く）:
  python weekly.py --prompt-out PROMPT.txt sat|sun docs/ai_weekly.json
      → 完成プロンプトを書き出して終了（APIは呼ばない）
  python weekly.py --answer ai_answer.json [--model-label "..."] sat|sun docs/ai_weekly.json
      → 回答JSONを検証（禁止語）して書き出す。回答が不正なら exit 2
  （sat の別名 review / sun の別名 outlook も受け付ける）
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
PAGES = "https://oo12takemaru-create.github.io/stock-trading-"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

# 引数は main() で解釈する（--prompt-out / --answer の追加に伴い 2026-10-05 に移動）
MODE = "sat"
OUT = Path("docs/ai_weekly.json")
MODE_ALIASES = {"review": "sat", "outlook": "sun"}
ROUTINE_LABEL = "Claude (Claude Code 定期実行)"
# --answer で読む回答JSONに最低限必要なキー（APIモードは従来どおり検証しない）
REQUIRED_KEYS = {"sat": ("title", "view", "view_reason", "points", "world", "flip", "days",
                         "sectors", "review_note", "lesson"),
                 "sun": ("summary", "scenarios", "watch")}
VIEW_JP = {"up": "上寄り", "flat": "横ばい", "down": "下寄り"}
FLAT_BAND = 1.0   # 横ばい＝週間の騰落が ±1.0% 以内（金曜終値→翌週金曜終値）

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-fable-5-1"
MODEL_NAMES = {
    "claude-fable-5-1": "Claude Fable 5.1",
    "claude-opus-5": "Claude Opus 5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-haiku-4-5": "Claude Haiku 4.5",
}

# 方向を持つスタンス（中立は採点の分母に入れない）
BULL = {"attack", "lean_attack"}
BEAR = {"defense", "lean_defense"}
STANCE_JP = {
    "attack": "強気", "lean_attack": "やや強気", "neutral": "中立",
    "lean_defense": "やや守り", "defense": "守り",
}

BANNED_PATTERNS = [
    r"\d{4}\.T", r"（\d{4}）", r"\(\d{4}\)",
    r"買うべき", r"売るべき", r"必ず上が", r"確実に上が",
    r"必ず下が", r"確実に下が", r"買い時", r"売り時", r"仕込み",
]


# --answer モードでは docs/ 配下のローカルファイルを先に読む（2026-10-05）。
#   routine（Claude Code 定期実行）のクラウド環境は外向きHTTPSが遮断されていて
#   GitHub Pages を取りに行けないため。ローカルに無ければ従来どおり Pages から取る。
#   APIモード / --prompt-out は従来どおり Pages から取る。
LOCAL_FIRST = False


def site_json(name):
    if LOCAL_FIRST:
        p = Path("docs") / name
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  {name}: ローカル読込失敗 {e}", file=sys.stderr)
    try:
        req = urllib.request.Request(f"{PAGES}/{name}", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"  {name}: 取得失敗 {e}", file=sys.stderr)
        return None


def week_range(today):
    """直近に終わった営業週（月〜金）。土曜=5・日曜=6 なので、どちらに走っても同じ金曜を指す"""
    back = today.weekday() - 4 if today.weekday() >= 5 else today.weekday() + 3
    fri = today - timedelta(days=back)
    return fri - timedelta(days=4), fri


def grade_week(rec, ana, mon, fri):
    """月〜金のスタンスと実測リターンを突き合わせる（機械採点）"""
    rows = {r["d"]: r for r in (rec or {}).get("ai", {}).get("rows", [])}
    # 見出しは ai_analysis 側の履歴から補う
    heads = {}
    if ana:
        for e in [ana.get("latest")] + (ana.get("history") or []):
            if e and e.get("date"):
                heads[e["date"]] = e
    out = []
    d = mon
    while d <= fri:
        ds = d.strftime("%Y-%m-%d")
        r = rows.get(ds)
        if r:
            st = r.get("s")
            ret = r.get("ret")
            judged = st in BULL or st in BEAR
            hit = None
            if judged and isinstance(ret, (int, float)):
                hit = (ret > 0) if st in BULL else (ret < 0)
            out.append({
                "d": ds,
                "w": "月火水木金"[d.weekday()],
                "stance": st,
                "stance_jp": STANCE_JP.get(st, st),
                "headline": (heads.get(ds) or r).get("h") or (heads.get(ds) or {}).get("headline", ""),
                "ret": ret,
                "judged": judged,
                "hit": hit,
            })
        d += timedelta(days=1)
    judged = [x for x in out if x["judged"] and x["hit"] is not None]
    hits = [x for x in judged if x["hit"]]
    return out, {
        "days": len(out),
        "judged": len(judged),
        "hits": len(hits),
        "win": round(len(hits) / len(judged) * 100, 1) if judged else None,
    }


def flow_lines():
    """週次で揃う需給。日曜版の主材料"""
    def num(v, fmt):
        return format(v, fmt) if isinstance(v, (int, float)) else "—"
    out = []
    f = site_json("investor_flow.json")
    if f and f.get("items"):
        out.append(f"投資部門別（{f.get('week', '')}・{f.get('market', '')}）")
        for r in f["items"][:6]:
            out.append(f"  {r.get('label')}: ネット{num(r.get('net_oku'), '+,.0f')}億円"
                       f"（前週{num(r.get('prev_net_oku'), '+,.0f')}億円）")
    c = site_json("cot.json")
    if c and c.get("items"):
        out.append(f"CFTC投機筋（{c.get('report_date', '')}時点の建玉）")
        for r in c["items"][:6]:
            out.append(f"  {r.get('label')}: ネット{num(r.get('net'), '+,.0f')}枚"
                       f"（前週比{num(r.get('change'), '+,.0f')}）")
    s_ = site_json("shinyo.json")
    if s_:
        out.append(f"信用取引（{s_.get('date', '')}）: 買い残 {num(s_.get('buy_oku'), ',.0f')}億円"
                   f"（前週比{num(s_.get('buy_chg_oku'), '+,.0f')}）/ 信用倍率 {num(s_.get('ratio'), '.2f')}倍")
    return out


EVENTS = [
    ("2026-09-11", "メジャーSQ(9月限) 寄付"),
    ("2026-09-11", "米CPI(8月分) 21:30"),
    ("2026-09-17", "FOMC結果発表 午前3:00"),
    ("2026-09-18", "日銀会合 結果発表 昼ごろ"),
    ("2026-10-02", "米雇用統計(9月分) 21:30"),
    ("2026-10-14", "米CPI(9月分) 21:30"),
    ("2026-10-29", "FOMC結果発表 午前3:00"),
    ("2026-10-30", "日銀会合 結果発表 昼ごろ"),
    ("2026-10-30", "次期TOPIX 初回定期入替（移行係数100%）"),
    ("2026-11-06", "米雇用統計(10月分) 22:30"),
    ("2026-11-10", "米CPI(10月分) 22:30"),
    ("2026-12-04", "米雇用統計(11月分) 22:30"),
    ("2026-12-10", "FOMC結果発表 午前4:00"),
    ("2026-12-10", "米CPI(11月分) 22:30"),
    ("2026-12-11", "メジャーSQ(12月限) 寄付"),
    ("2026-12-18", "日銀会合 結果発表 昼ごろ"),
]


def next_week_events(today):
    """翌週（月〜金）に入る大型イベント。土曜なら2日後、日曜なら翌日が月曜"""
    mon = today + timedelta(days=2 if today.weekday() == 5 else 1)
    fri = mon + timedelta(days=4)
    out = []
    for d, name in EVENTS:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        if mon <= dd <= fri:
            out.append(f"{dd:%m/%d}（{'月火水木金土日'[dd.weekday()]}） {name}")
    return out, mon, fri


MARKET_TICKERS = [
    ("^N225", "日経平均", "{:,.0f}円"), ("^GSPC", "S&P500", "{:,.0f}"),
    ("^IXIC", "ナスダック総合", "{:,.0f}"), ("^SOX", "SOX（米半導体）", "{:,.0f}"),
    ("^VIX", "VIX", "{:.1f}"), ("^TNX", "米10年債利回り", "{:.2f}%"),
    ("JPY=X", "ドル円", "{:.2f}円"), ("CL=F", "WTI原油", "{:.2f}ドル"),
    ("GC=F", "金", "{:,.0f}ドル"),
]


def market_lines():
    """今週の世界の値動き（金曜終値と週間騰落）。prep（Actions）でだけ取る。
    routine の環境は外に出られないので、--answer のときは呼ばない（プロンプトにしか使わない）"""
    if LOCAL_FIRST:
        return []
    try:
        import yfinance as yf
    except Exception:
        return ["(yfinance が無いため取得せず)"]
    out = []
    for t, label, fmt in MARKET_TICKERS:
        try:
            c = yf.Ticker(t).history(period="1mo", auto_adjust=False)["Close"].dropna()
            if len(c) < 6:
                continue
            last, prev = float(c.iloc[-1]), float(c.iloc[-6])
            chg = (last / prev - 1) * 100
            out.append(f"  {label}: {fmt.format(last)}（{c.index[-1]:%m/%d}・週間{chg:+.2f}%）")
        except Exception as e:
            print(f"  {t}: 取得失敗 {e}", file=sys.stderr)
    return out


def morning_lines(ana):
    """金曜朝のAI朝刊から、世界の流れとセクターの風向き（その朝の見立て。参考材料）"""
    L = (ana or {}).get("latest") or {}
    out = []
    if L.get("date"):
        out.append(f"（{L['date']} 朝のAI朝刊「{L.get('headline', '')}」より）")
    for w in (L.get("world_flow") or [])[:5]:
        if isinstance(w, dict):
            out.append(f"  ・{w.get('theme') or w.get('title') or ''}: {w.get('body') or ''}")
        else:
            out.append(f"  ・{w}")
    for s in (L.get("sectors") or [])[:6]:
        out.append(f"  セクター {s.get('name')} [{s.get('bias')}] {s.get('reason', '')}")
    return out


def score_lines(s3, fri):
    if not s3:
        return "(取得できず)"
    m = s3.get("metrics") or {}
    head = f"合計{s3.get('total'):+d} → 機械判定「{s3.get('stance_jp')}」（{s3.get('trade_date', '')}引け時点）"
    if s3.get("trade_date", "") < fri.isoformat():
        head += "\n  ※金曜の値動きは反映前の採点です。金曜の数字は上の「世界の値動き」を優先してください"
    if m.get("n225_close"):
        head += (f"\n  日経 {m['n225_close']:,.0f}円 / 25日線 {m.get('ma25', 0):,.0f} / "
                 f"75日線 {m.get('ma75', 0):,.0f} / 200日線 {m.get('ma200', 0):,.0f}")
    for a in s3.get("axes", []):
        head += f"\n  {a['label']}: {a['score']:+d}（" + " / ".join(a.get("notes", [])) + "）"
    return head


def next_week_days(today):
    """来週の営業日と休場日（jpholiday があれば祝日を除く）"""
    mon = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    try:
        import jpholiday
        is_hol = jpholiday.is_holiday
        hol_name = jpholiday.is_holiday_name
    except Exception:
        is_hol, hol_name = (lambda d: False), (lambda d: None)
    days, closed = [], []
    for i in range(5):
        d = mon + timedelta(days=i)
        lab = f"{d:%m/%d}（{'月火水木金'[i]}）"
        if is_hol(d):
            closed.append(f"{lab} {hol_name(d)}で休場")
        else:
            days.append(lab)
    return days, closed


EVENT_KEYS = [("米CPI", "cpi"), ("雇用統計", "payroll"), ("FOMC", "fomc"), ("日銀", "boj"),
              ("メジャーSQ", "sq"), ("SQ", "sq_minor"), ("TOPIX", "topix")]


def event_stat_lines(events):
    """来週のイベントの過去の反応（相場の暦 event_score.json）。事実の形だけで渡す"""
    es = site_json("event_score.json") or {}
    ev = es.get("events") or {}
    out = []
    for name in events:
        key = next((k for w, k in EVENT_KEYS if w in name), None)
        e = ev.get(key or "")
        if not e:
            continue
        # d0 は「反応日」の騰落（CPI・FOMC・雇用統計は翌営業日、日銀・SQは当日。event_score の caveats）
        w = (e.get("windows") or {}).get("d0") or {}
        rg = (e.get("windows") or {}).get("range") or {}
        if not w:
            continue
        ratio = (rg.get("mean_pct") / rg.get("base_mean_pct")) if rg.get("base_mean_pct") else None
        out.append(f"  {e.get('label', key)}: 過去{w.get('n')}回、反応日に日経が上がった割合 {w.get('up_rate')}%"
                   f"（イベントの無い日 {w.get('base_up_rate')}%）"
                   + (f"・値幅は平常の{ratio:.2f}倍" if ratio else "")
                   + f"・判定「{w.get('verdict', '')}」")
    return out


def n225_close_on(day, s3=None):
    """指定日（その日が休場ならそれ以前の直近営業日）の日経平均終値 → (終値, 日付文字列)。
    yfinance で日付を指定して取る（「いちばん新しい値」だと、配信の遅れで前日の値を掴むことがある）。
    取れなければ score3.json の終値を、日付が合うときだけ使う"""
    try:
        import yfinance as yf
        c = yf.Ticker("^N225").history(start=(day - timedelta(days=10)).isoformat(),
                                       end=(day + timedelta(days=1)).isoformat(),
                                       auto_adjust=False)["Close"].dropna()
        if len(c):
            return round(float(c.iloc[-1]), 2), f"{c.index[-1]:%Y-%m-%d}"
    except Exception as e:
        print(f"  ^N225 {day}: 取得失敗 {e}", file=sys.stderr)
    m = (s3 or {}).get("metrics") or {}
    if m.get("n225_close") and m.get("n225_date") == day.isoformat():
        return round(float(m["n225_close"]), 2), m["n225_date"]
    return None, None


def grade_prev_view(views, s3, today):
    """先週の週末版で出した方向感を、金曜終値で機械採点する。AIは関与しない。
    基準＝出したときの金曜終値 → 結果＝対象週の金曜（休場なら直前の営業日）の終値"""
    pend = [v for v in views if v.get("hit") is None and v.get("week_to", "9") < today.isoformat()]
    if not pend:
        return None
    v = dict(pend[-1])
    wt = datetime.strptime(v["week_to"], "%Y-%m-%d").date()
    close, cdate = n225_close_on(wt, s3)
    # 終値の日付が対象週の中にあるときだけ採点する（まだ無ければ前の週の値を掴むので未採点にする）
    if not close or not v.get("base_close") or not cdate or cdate < v.get("week_from", "9"):
        v["note"] = "対象週の終値がまだ取れていないため未採点"
        return v
    ret = round((close / v["base_close"] - 1) * 100, 2)
    hit = (ret > 0) if v["view"] == "up" else (ret < 0) if v["view"] == "down" else (abs(ret) <= FLAT_BAND)
    v.update({"close": close, "close_date": cdate, "ret": ret, "hit": hit})
    return v


def view_record(views):
    g = [v for v in views if v.get("hit") is not None]
    h = sum(1 for v in g if v["hit"])
    return {"judged": len(g), "hits": h, "win": round(h / len(g) * 100, 1) if g else None}


PROMPT_SAT = """あなたは日本株市場を専門とするマクロアナリストです。
株レーダーの「週末版」を日本語で書いてください。読者は個人投資家で、週末にこれを読んで来週の準備をします。

# この記事の主役は「来週の地図」です
- 今週の値動きと世界の動きから、**来週1週間の日経平均の方向感を1つ**出してください
  （up=上寄り / flat=横ばい（週間±1%以内）/ down=下寄り）。迷っても必ずどれか1つを選ぶ
- 方向感は**来週の土曜に機械が採点し、そのまま公開します**（金曜終値→翌週の金曜終値）。
  当たり外れが残るので、根拠は数字で書いてください
- 世界で起きていることは「出来事 → 何が動くか → 日本株のどこに効くか」の順で、つながりを書く
- 「この形になったら見立てを変える」条件を、上側・下側の両方について**数値で**書く
- 今週の答え合わせは添えるだけ（review_note を1〜2文）。下の採点表は機械の確定値なので書き換えない

# 厳守
- 個別銘柄名・証券コードは書かない（セクターまで）。「買うべき」「買い時」などの売買指示は書かない
- 「必ず」「確実に」などの断定はしない。「〜寄り」「〜しやすい」「〜なら」で書く
- 入力にない事実・数字を作らない。ニュースの原因を推測で断定しない

# 今週の答え合わせ（機械が計算済み）
{table}

# 先週の週末版で出した方向感の採点（機械が計算済み）
{prev_view}

# 今週の世界の値動き（金曜終値・週間騰落）
{market}

# 金曜朝のAI朝刊の見立て（世界の流れ・セクター）
{morning}

# 現在の機械判定（3軸スコア）
{score}

# 週次の需給
{flow}

# 来週の日程
営業日: {days}
{closed}
{events}

# 来週のイベントの過去の反応（相場の暦・事実）
{event_stats}

# 出力（JSONのみ。コードフェンスや前置きは書かない）
{{
  "title": "来週の見立てを一言で（25字以内。例: CPI週、7万円の上は重い）",
  "view": "up か flat か down",
  "view_reason": "その方向感の根拠を2〜3文。3軸スコア・需給・世界の数字を必ず引用する",
  "points": ["今週の結論を3行（各45字以内）。1行目=今週の日本株、2行目=世界の流れ、3行目=来週の見立て"],
  "world": [{{"event": "世界で起きていること（数字つき）", "path": "それが何を動かすか", "japan": "日本株のどこに、どちら向きに効きやすいか"}}],
  "flip": [{{"side": "up か down", "if": "こうなったら（数値の条件）", "then": "見立てをどう変えるか"}}],
  "days": [{{"d": "MM/DD（曜）", "watch": "その日に見るものを1文"}}],
  "sectors": [{{"name": "セクター名", "wind": "tail（追い風）か head（逆風）", "why": "理由を1文（数字つき）"}}],
  "review_note": "今週の答え合わせを1〜2文。採点表の数字に触れ、何が効いて何を外したか",
  "lesson": "来週に持ち越す教訓を1文"
}}
points は3つ。world は2〜3個。flip は2〜3個で、up と down を必ず両方入れる。
days は来週の営業日すべて（休場日は入れない）。sectors は追い風2つ・逆風2つ。JSONのみを出力すること。"""

PROMPT_SUN = """あなたは日本株市場を専門とするマクロアナリストです。
株レーダーの「来週の想定」を日本語で書いてください。

# 前提
- 土日は市場が動いていないので、**金曜引け時点の数値**で書きます
- 個別銘柄名・証券コードは書かない（セクターまで）。「買うべき」等の売買指示・断定も書かない
- データにない事実を作らない。「〜の可能性」「〜になりやすい」を使う
- シナリオは**条件と数値**で書く。「上がりそう」ではなく「◯◯を超えたら」の形にする

# 来週のイベント
{events}

# 今週末時点の需給（週次データ）
{flow}

# 現在の機械判定（3軸スコア）
{score}

# 出力（JSONのみ）
{{
  "summary": "来週の見立てを2〜3文。何が焦点かを数字を引用して書く",
  "scenarios": [
    {{"side": "attack か defense のどちらか", "name": "シナリオ名", "trigger": "こうなったら、という条件を数値で1行", "note": "そのとき何が起きやすいかを1文"}}
  ],
  "watch": "来週いちばん注目する数字や日程を1文で"
}}
scenariosは2〜3個。攻め側と守り側を必ず両方入れること。JSONのみを出力すること。"""


def call_anthropic(prompt, key):
    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=180) as r:
        res = json.load(r)
    if res.get("stop_reason") == "max_tokens":
        raise RuntimeError("max_tokens に達しました")
    text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("JSONが見つかりません")
    return json.loads(m.group(0))


def load_out():
    try:
        return json.loads(Path(OUT).read_text(encoding="utf-8"))
    except Exception:
        return {}


def validate_weekend(d):
    """週末版の形を確かめる。崩れた回答をページに出さないため"""
    if d.get("view") not in VIEW_JP:
        return f"view が up/flat/down でない: {d.get('view')}"
    if not (isinstance(d.get("points"), list) and len(d["points"]) == 3):
        return "points は3行"
    if not (isinstance(d.get("world"), list) and 1 <= len(d["world"]) <= 3):
        return "world は1〜3個"
    sides = {f.get("side") for f in d.get("flip") or [] if isinstance(f, dict)}
    if not {"up", "down"} <= sides:
        return "flip に up と down の両方が要る"
    if not (isinstance(d.get("sectors"), list) and d["sectors"]):
        return "sectors が空"
    if any(s.get("wind") not in ("tail", "head") for s in d["sectors"] if isinstance(s, dict)):
        return "sectors.wind は tail/head"
    if not (isinstance(d.get("days"), list) and d["days"]):
        return "days が空"
    return None


def check_banned(data):
    blob = json.dumps(data, ensure_ascii=False)
    for pat in BANNED_PATTERNS:
        if re.search(pat, blob):
            return f"禁止パターン検出: {pat}"
    return None


def build_prompt(today):
    """入力データを集めて完成プロンプトを作る。sat で採点対象が無ければ exit 1"""
    rec = site_json("ai_record.json")
    ana = site_json("ai_analysis.json")
    mon, fri = week_range(today)
    rows, stats = grade_week(rec, ana, mon, fri)
    flow = flow_lines()

    if MODE == "sat":
        if not rows:
            print("今週のスタンスが1件も取れませんでした", file=sys.stderr)
            sys.exit(1)
        tbl = [f"対象期間: {mon:%Y-%m-%d}（月）〜 {fri:%Y-%m-%d}（金）",
               f"判定できた日: {stats['judged']}日 / 的中 {stats['hits']}日"
               + (f" / 勝率 {stats['win']}%" if stats["win"] is not None else " / 勝率は判定不能")]
        for r in rows:
            mark = "—（中立は採点対象外）" if not r["judged"] else ("○ 的中" if r["hit"] else "× 外れ")
            tbl.append(f"  {r['d']}（{r['w']}） スタンス={r['stance_jp']} / "
                       f"日経{r['ret']:+.2f}% → {mark} / 見出し「{r['headline']}」")
        s3 = site_json("score3.json") or {}
        pv = grade_prev_view(load_out().get("views") or [], s3, today)
        if pv is None:
            pvt = "（先週は方向感を出していません。今回が初回）"
        elif pv.get("hit") is None:
            pvt = f"先週の見立て「{VIEW_JP.get(pv['view'], pv['view'])}」→ {pv.get('note', '未採点')}"
        else:
            pvt = (f"先週の見立て「{VIEW_JP.get(pv['view'], pv['view'])}」"
                   f"（基準 {pv['base_date']} 終値 {pv['base_close']:,.0f}円）→ "
                   f"{pv['close_date']} 終値 {pv['close']:,.0f}円・週間{pv['ret']:+.2f}% → "
                   + ("○ 的中" if pv["hit"] else "× 外れ"))
        ev, nm, nf = next_week_events(today)
        days, closed = next_week_days(today)
        prompt = PROMPT_SAT.format(
            table="\n".join(tbl), prev_view=pvt,
            market="\n".join(market_lines()) or "(取得できず)",
            morning="\n".join(morning_lines(ana)) or "(取得できず)",
            score=score_lines(s3, fri), flow="\n".join(flow) or "(取得できず)",
            days=" / ".join(days) or "(なし)", closed="\n".join(closed),
            events="大型イベント: " + (" / ".join(ev) if ev else "なし"),
            event_stats="\n".join(event_stat_lines(ev)) or "(該当なし)")
    else:
        ev, nm, nf = next_week_events(today)
        s3 = site_json("score3.json") or {}
        sc = (f"合計{s3.get('total'):+d} → 「{s3.get('stance_jp')}」\n"
              + "\n".join(f"  {a['label']}: {a['score']:+d}" for a in s3.get("axes", []))
              ) if s3 else "(取得できず)"
        prompt = PROMPT_SUN.format(
            events="\n".join(ev) or f"{nm:%m/%d}〜{nf:%m/%d} に大型イベントはありません",
            flow="\n".join(flow) or "(取得できず)", score=sc)
    return prompt, rows, stats, mon, fri


def write_entry(data, label, now, rows, stats, mon, fri):
    """回答 → entry 組み立て → 書き出し（API / --answer 共通）"""
    today = now.date()
    entry = {
        "kind": "weekend" if MODE == "sat" else "outlook",
        "date": today.isoformat(),
        "updated": now.isoformat(timespec="seconds"),
        "model": label,
        "week": {"from": mon.isoformat(), "to": fri.isoformat()},
        "note": "土日は市場が動かないため、金曜引け時点の数値で作成しています。",
        **data,
    }
    ev, nm, nf = next_week_events(today)
    entry["events"] = ev
    entry["next_week"] = {"from": nm.isoformat(), "to": nf.isoformat()}

    out = load_out()
    if MODE == "sat":
        entry["rows"] = rows
        entry["stats"] = stats
        entry["view_jp"] = VIEW_JP[data["view"]]
        days, closed = next_week_days(today)
        entry["closed"] = closed
        # 方向感の記録（views）。先週分を採点してから、今週分を積む。採点はここだけで行う
        s3 = site_json("score3.json") or {}
        views = out.get("views") or []
        pv = grade_prev_view(views, s3, today)
        if pv is not None and pv.get("hit") is not None:
            views = [pv if (v.get("date") == pv["date"]) else v for v in views]
        entry["prev_view"] = pv
        base, bdate = n225_close_on(fri, s3)
        views = [v for v in views if v.get("date") != today.isoformat()]   # 同じ日の再実行は置き換え
        views.append({"date": today.isoformat(), "view": data["view"],
                      "base_close": base, "base_date": bdate,
                      "week_from": nm.isoformat(), "week_to": nf.isoformat(),
                      "title": data.get("title", "")})
        out["views"] = views[-104:]
        entry["view_record"] = view_record(views)

    # 週末版と旧日曜版は別枠で保持する（片方が失敗しても他方を消さない）
    out[entry["kind"]] = entry
    out["updated"] = entry["updated"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {OUT.name}: {entry['kind']} {today}"
          + (f" 勝率{stats['win']}%（{stats['hits']}/{stats['judged']}）" if MODE == "sat" else ""))


def run_prompt_out(prompt_out):
    """APIを呼ばず、完成したプロンプトだけを書き出す（routine 用）"""
    now = datetime.now(JST)
    prompt, *_ = build_prompt(now.date())
    Path(prompt_out).write_text(prompt, encoding="utf-8")
    print(f"モード: {MODE} / プロンプトを出力: {prompt_out} ({len(prompt)}文字)")


def run_answer(answer_path, label):
    """APIを呼ばず、routine が書いた回答JSONを検証して書き出す。不正なら exit 2"""
    global LOCAL_FIRST
    LOCAL_FIRST = True
    now = datetime.now(JST)
    print(f"モデル: {label} / モード: {MODE}")
    _prompt, rows, stats, mon, fri = build_prompt(now.date())
    try:
        text = Path(answer_path).read_text(encoding="utf-8-sig")
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("JSONが見つかりません")
        data = json.loads(m.group(0))
    except Exception as e:
        print(f"回答不正: JSONとして読めない ({answer_path}): {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print("回答不正: dictでない", file=sys.stderr)
        sys.exit(2)
    for k in REQUIRED_KEYS[MODE]:
        if k not in data:
            print(f"回答不正: キー欠落: {k}", file=sys.stderr)
            sys.exit(2)
    err = check_banned(data) or (validate_weekend(data) if MODE == "sat" else None)
    if err is not None:
        print(f"回答不正: 検証NG: {err}", file=sys.stderr)
        sys.exit(2)
    write_entry(data, label, now, rows, stats, mon, fri)


def run_api():
    """従来モード: Anthropic API を呼んで生成する"""
    now = datetime.now(JST)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    prompt, rows, stats, mon, fri = build_prompt(now.date())

    print(f"モデル: {MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL)} / モード: {MODE}")
    data, err = None, None
    for i in range(3):
        try:
            cand = call_anthropic(prompt, key)
            err = check_banned(cand) or (validate_weekend(cand) if MODE == "sat" else None)
            if err is None:
                data = cand
                break
            print(f"検証NG(試行{i+1}): {err}", file=sys.stderr)
        except Exception as e:
            err = str(e)
            print(f"生成失敗(試行{i+1}): {e}", file=sys.stderr)
    if data is None:
        print(f"週末版の生成に失敗: {err}", file=sys.stderr)
        sys.exit(1)

    write_entry(data, MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL) + " (Anthropic)",
                now, rows, stats, mon, fri)


def main():
    global MODE, OUT
    import argparse
    ap = argparse.ArgumentParser(description="週末版AI朝刊")
    ap.add_argument("mode", nargs="?", default="sat", help="sat(=review) / sun(=outlook)")
    ap.add_argument("out", nargs="?", default="docs/ai_weekly.json")
    ap.add_argument("--prompt-out", help="APIを呼ばず、完成したプロンプトをこのファイルに書いて終了")
    ap.add_argument("--answer", help="APIを呼ばず、このAI回答JSONを検証して書き出す")
    ap.add_argument("--model-label", default="", help=f"--answer 時の model 表記(既定: {ROUTINE_LABEL})")
    a = ap.parse_args()
    MODE = MODE_ALIASES.get(a.mode.lower(), a.mode.lower())
    OUT = Path(a.out)
    if a.prompt_out and a.answer:
        ap.error("--prompt-out と --answer は同時に指定できません")
    if MODE not in ("sat", "sun"):
        print(f"モード不正: {a.mode}（sat / sun / review / outlook）", file=sys.stderr)
        sys.exit(1)

    if a.prompt_out:
        run_prompt_out(a.prompt_out)
    elif a.answer:
        run_answer(a.answer, a.model_label.strip() or ROUTINE_LABEL)
    else:
        run_api()


if __name__ == "__main__":
    main()
