# -*- coding: utf-8 -*-
"""週末版のAI朝刊 → docs/ai_weekly.json（株レーダー）

土曜: 今週の答え合わせ（月〜金のスタンス × 実際の日経の値動き）
日曜: 来週の想定（イベント・週次需給・シナリオ）

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

MODE = (sys.argv[1] if len(sys.argv) > 1 else "sat").lower()
OUT = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/ai_weekly.json")

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
]


def site_json(name):
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


PROMPT_SAT = """あなたは日本株市場を専門とするマクロアナリストです。
株レーダーの「今週の答え合わせ」を日本語で書いてください。

# 前提
- 下の採点表は**すでに機械が計算した確定値**です。数字を書き換えてはいけません
- あなたの仕事は「なぜそうなったか」の説明だけです
- **外れた日こそ正直に書いてください。**言い訳をせず、何を読み違えたのかを書く
- 個別銘柄名・証券コードは書かない（セクターまで）。売買指示・断定も書かない

# 今週の採点（機械が計算済み）
{table}

# 今週の需給（週次データ）
{flow}

# 出力（JSONのみ）
{{
  "summary": "今週を2〜3文で総括。勝率の数字に触れ、どの判断が効いてどれが外れたかを書く",
  "misses": [
    {{"date": "外した日(YYYY-MM-DD)", "why": "なぜ外れたかを1〜2文。何を読み違えたのかを具体的に"}}
  ],
  "lesson": "来週に持ち越す教訓を1文で"
}}
missesは最大2件。全部当たっていた場合は空配列にし、lessonにその旨を書く。JSONのみを出力すること。"""

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


def check_banned(data):
    blob = json.dumps(data, ensure_ascii=False)
    for pat in BANNED_PATTERNS:
        if re.search(pat, blob):
            return f"禁止パターン検出: {pat}"
    return None


def main():
    now = datetime.now(JST)
    today = now.date()
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

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
        prompt = PROMPT_SAT.format(table="\n".join(tbl), flow="\n".join(flow) or "(取得できず)")
    else:
        ev, nm, nf = next_week_events(today)
        s3 = site_json("score3.json") or {}
        sc = (f"合計{s3.get('total'):+d} → 「{s3.get('stance_jp')}」\n"
              + "\n".join(f"  {a['label']}: {a['score']:+d}" for a in s3.get("axes", []))
              ) if s3 else "(取得できず)"
        prompt = PROMPT_SUN.format(
            events="\n".join(ev) or f"{nm:%m/%d}〜{nf:%m/%d} に大型イベントはありません",
            flow="\n".join(flow) or "(取得できず)", score=sc)

    print(f"モデル: {MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL)} / モード: {MODE}")
    data, err = None, None
    for i in range(3):
        try:
            cand = call_anthropic(prompt, key)
            err = check_banned(cand)
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

    entry = {
        "kind": "review" if MODE == "sat" else "outlook",
        "date": today.isoformat(),
        "updated": now.isoformat(timespec="seconds"),
        "model": MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL) + " (Anthropic)",
        "week": {"from": mon.isoformat(), "to": fri.isoformat()},
        "note": "土日は市場が動かないため、金曜引け時点の数値で作成しています。",
        **data,
    }
    if MODE == "sat":
        entry["rows"] = rows
        entry["stats"] = stats
    else:
        ev, nm, nf = next_week_events(today)
        entry["events"] = ev
        entry["next_week"] = {"from": nm.isoformat(), "to": nf.isoformat()}

    # 土曜版と日曜版は別枠で保持する（片方が失敗しても他方を消さない）
    out = {}
    try:
        out = json.loads(Path(OUT).read_text(encoding="utf-8"))
    except Exception:
        pass
    out[entry["kind"]] = entry
    out["updated"] = entry["updated"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {OUT.name}: {entry['kind']} {today}"
          + (f" 勝率{stats['win']}%（{stats['hits']}/{stats['judged']}）" if MODE == "sat" else ""))


if __name__ == "__main__":
    main()
