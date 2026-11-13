# -*- coding: utf-8 -*-
"""AI朝刊・週末版の号ごとの記録と、週1回の振り返り一覧（2026-11-13〜）

号ごとの記録 docs/ai_feedback.json:
  号の日付・種類・号数・見出し・スタンス（見立て）・書き出しの1文・字数を、取り込み（ingest）のたびに機械が書く。
  反応の欄 m（GA4 の表示・滞在、サイトの1タップ、X の表示・反応）と note は、あとから統括が数字を足す欄。
  機械は m と note を上書きしない（同じ号を取り込み直しても残る）。自動で取れるもの（下の AI_TAPS_URL）だけ機械が入れる。

週1回の振り返り docs/ai_review/YYYY-MM-DD.md:
  日曜の週末版の取り込みのときに、直近7号の朝刊の一覧（見出し・書き出しの1文・字数・当たり外れ・分かっている反応）と
  週末版の直近、続けて使った言い回し、いまの指針を出す。統括はこれを読んで ai_style_notes.md を直す。

使い方:
  python ai_feedback.py record morning|sat|sun      取り込んだ号を記録（ingest の後）
  python ai_feedback.py review [--date YYYY-MM-DD]   振り返り一覧を docs/ai_review/ に書く
  python ai_feedback.py backfill [ai_analysis.json ...] 今ある号（履歴・記録）から記録を作る

自動で取れる反応（任意）:
  環境変数 AI_TAPS_URL に、サイトの1タップ「役に立った／いまいち」の公開の集計（JSON）を置けば取り込む。
  形: {"rows": [{"kind": "morning|sat|sun", "d": "YYYY-MM-DD", "good": 3, "bad": 1}]}。無ければ何もしない。
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ai_style

JST = timezone(timedelta(hours=9))
FEEDBACK = Path("docs/ai_feedback.json")
REVIEW_DIR = Path("docs/ai_review")
ANALYSIS = Path("docs/ai_analysis.json")
WEEKLY = Path("docs/ai_weekly.json")
WEEKLY_DAYS = Path("docs/ai_weekly_days")
RECORD = Path("docs/ai_record.json")
KEEP = 400   # 記録は新しい順に400号まで

# 統括が足す欄（null＝まだ分からない）
METRICS = {
    "ga4_views": "GA4: その号のページの表示回数（公開から7日）",
    "ga4_sec": "GA4: 平均エンゲージメント時間（秒）",
    "tap_good": "サイトの1タップ「役に立った」の人数",
    "tap_bad": "サイトの1タップ「いまいち」の人数",
    "x_imp": "X: その号を紹介した投稿の表示回数",
    "x_eng": "X: その投稿の反応（いいね＋リポスト＋返信）",
}
KIND_JP = {"morning": "朝刊", "sat": "土曜・今週の振り返り", "sun": "日曜・来週の地図"}
STANCE_JP = {"attack": "強気", "lean_attack": "やや強気", "neutral": "中立",
             "lean_defense": "やや守り", "defense": "守り"}
VIEW_JP = {"up": "上寄り", "flat": "横ばい", "down": "下寄り"}


def _now():
    return datetime.now(JST)


def first_sentence(s, limit=80):
    s = (s or "").strip()
    m = re.search(r"^.+?[。！？]", s)
    t = m.group(0) if m else s
    return t if len(t) <= limit else t[:limit] + "…"


def summarize(kind, e):
    """号の entry → 記録の1行（m と note は付けない）"""
    if kind == "morning":
        return {
            "kind": "morning", "date": e.get("date"), "issue": e.get("issue"),
            "title": e.get("headline", ""),
            "stance": e.get("stance"), "machine": e.get("machine_stance"),
            "first": first_sentence(e.get("stance_reason")),
            "watch": first_sentence(e.get("today_watch")),
            "chars": len(ai_style.prose(e, ai_style.MORNING_KEYS).replace("\n", "")),
        }
    pts = e.get("points") or []
    row = {
        "kind": kind, "date": e.get("date"), "issue": e.get("issue"),
        "title": e.get("title", ""),
        "first": first_sentence(pts[0] if pts else ""),
        "chars": len(ai_style.prose(e, ai_style.WEEKLY_KEYS).replace("\n", "")),
    }
    if kind == "sun":
        row["view"] = e.get("view")
    return row


def load(path=FEEDBACK):
    """記録を読む。無ければ空の形。壊れていたら例外（統括が足した数字を消さないため、上書きしない）"""
    p = Path(path)
    if not p.exists():
        return {"issues": []}
    d = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(d, dict) or not isinstance(d.get("issues"), list):
        raise ValueError(f"{p} の形が違う（issues の配列が無い）")
    return d


def save(fb, path=FEEDBACK):
    fb["about"] = ("AI朝刊・週末版の号ごとの記録（ai_feedback.py が書く）。m と note は統括が足す欄で、"
                   "機械は上書きしない。数字が分からない欄は null のまま")
    fb["fields"] = METRICS
    fb["updated"] = _now().isoformat(timespec="seconds")
    fb["issues"] = sorted(fb["issues"], key=lambda r: (r.get("date") or "", r.get("kind") or ""),
                          reverse=True)[:KEEP]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(fb, ensure_ascii=False, indent=1), encoding="utf-8")


def upsert(fb, row):
    """同じ号（種類・日付）があれば機械の欄だけ差し替え、m と note は残す"""
    for r in fb["issues"]:
        if r.get("kind") == row["kind"] and r.get("date") == row["date"]:
            m, note = r.get("m"), r.get("note", "")
            r.clear()
            r.update(row)
            r["m"] = {**{k: None for k in METRICS}, **(m or {})}
            r["note"] = note
            return r
    row = {**row, "m": {k: None for k in METRICS}, "note": ""}
    fb["issues"].append(row)
    return row


def import_taps(fb):
    """サイトの1タップの公開の集計（AI_TAPS_URL）があれば取り込む。無い・読めないときは何もしない"""
    url = os.environ.get("AI_TAPS_URL", "").strip()
    if not url:
        return 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "kaburadar-ai-feedback/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = (json.load(r) or {}).get("rows") or []
    except Exception as e:
        print(f"1タップの集計を読めず（空欄のまま）: {e}", file=sys.stderr)
        return 0
    n = 0
    for t in rows:
        for r in fb["issues"]:
            if r.get("kind") == t.get("kind") and r.get("date") == t.get("d"):
                for src, dst in (("good", "tap_good"), ("bad", "tap_bad")):
                    if isinstance(t.get(src), int):
                        r.setdefault("m", {})[dst] = t[src]
                n += 1
    return n


def _entry_of(kind):
    if kind == "morning":
        return json.loads(ANALYSIS.read_text(encoding="utf-8")).get("latest")
    d = json.loads(WEEKLY.read_text(encoding="utf-8"))
    return d.get("sat") if kind == "sat" else d.get("weekend")


def record(kind):
    e = _entry_of(kind)
    if not isinstance(e, dict) or not e.get("date"):
        print(f"記録する号が無い（{kind}）", file=sys.stderr)
        return None
    fb = load()
    row = upsert(fb, summarize(kind, e))
    import_taps(fb)
    save(fb)
    print(f"記録: {KIND_JP[kind]} {row['date']} 「{row['title']}」 {row['chars']}字")
    return row


def backfill(analysis_files=()):
    """今ある号から記録を作る（初回だけ）。朝刊は ai_analysis.json の latest と history（と渡されたファイル）、
    週末版は ai_weekly_days/*.json"""
    fb = load()
    n = 0
    for f in [ANALYSIS, *map(Path, analysis_files)]:
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in [d.get("latest")] + list(d.get("history") or []):
            if isinstance(e, dict) and e.get("date") and e.get("headline"):
                upsert(fb, summarize("morning", e))
                n += 1
    for p in sorted(WEEKLY_DAYS.glob("????-??-??-s??.json")):
        kind = "sat" if p.name.endswith("-sat.json") else "sun"
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if e.get("date"):
            upsert(fb, summarize(kind, e))
            n += 1
    save(fb)
    print(f"backfill: {n}件 → {len({(r['kind'], r['date']) for r in fb['issues']})}号")


# ---------------------------------------------------------------- 週1回の振り返り

def _results():
    """朝刊の当たり外れ（ai_record.json の機械採点）。{日付: (騰落%, 当たり True/False/None)}"""
    try:
        rec = json.loads(RECORD.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for r in ((rec.get("ai") or {}).get("rows") or []):
        s, ret = r.get("s"), r.get("ret")
        if not r.get("d") or not isinstance(ret, (int, float)):
            continue
        hit = (ret > 0) if s in ("attack", "lean_attack") else (ret < 0) if s in ("defense", "lean_defense") else None
        out[r["d"]] = (ret, hit)
    return out


def _m(v):
    return "—" if v is None else str(v)


def _cell(s):
    return (s or "").replace("|", "／").replace("\n", " ")


def build_review(today=None):
    today = today or _now().strftime("%Y-%m-%d")
    fb = load()
    res = _results()
    mor = [r for r in fb["issues"] if r.get("kind") == "morning" and r.get("date", "") <= today][:7]
    wk = [r for r in fb["issues"] if r.get("kind") in ("sat", "sun") and r.get("date", "") <= today][:4]
    L = [f"# AI朝刊・週末版の振り返り（{today} 機械が作成）", "",
         "統括へ: これを読んで `ai_style_notes.md` を直す（ルールの行は30行まで・各行に日付と「なぜ」）。"
         "反応の数字は `docs/ai_feedback.json` の各号の `m` に足す（— はまだ分からない欄）。", ""]
    L += ["## 朝刊 直近7号", "",
          "| 号 | 日付 | スタンス | 当たり | 見出し | 字数 | 表示 | 滞在秒 | 役に立った/いまいち | X 表示/反応 |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in mor:
        ret, hit = res.get(r["date"], (None, None))
        h = "—" if ret is None else (f"{ret:+.2f}% " + ("○" if hit else "×" if hit is False else "中立"))
        m = r.get("m") or {}
        L.append(f"| {_m(r.get('issue'))} | {r['date']} | {STANCE_JP.get(r.get('stance'), r.get('stance'))} | {h} | "
                 f"{_cell(r.get('title'))} | {r.get('chars')} | {_m(m.get('ga4_views'))} | {_m(m.get('ga4_sec'))} | "
                 f"{_m(m.get('tap_good'))}/{_m(m.get('tap_bad'))} | {_m(m.get('x_imp'))}/{_m(m.get('x_eng'))} |")
    L += ["", "### 書き出しの1文（stance_reason）と今日見るところ（today_watch）", ""]
    for r in mor:
        L.append(f"- {r['date']} 書き出し: {r.get('first', '')}")
        L.append(f"  - 見るところ: {r.get('watch', '')}")
        if r.get("note"):
            L.append(f"  - メモ: {r['note']}")
    L += ["", "## 週末版 直近", "",
          "| 日付 | 種類 | タイトル | 見立て | 字数 | 表示 | 滞在秒 | X 表示/反応 |", "|---|---|---|---|---|---|---|---|"]
    for r in wk:
        m = r.get("m") or {}
        L.append(f"| {r['date']} | {KIND_JP.get(r['kind'])} | {_cell(r.get('title'))} | "
                 f"{VIEW_JP.get(r.get('view'), '—')} | {r.get('chars')} | {_m(m.get('ga4_views'))} | "
                 f"{_m(m.get('ga4_sec'))} | {_m(m.get('x_imp'))}/{_m(m.get('x_eng'))} |")
    L += [""] + [f"- {r['date']} {KIND_JP.get(r['kind'])} 1行目: {r.get('first', '')}" for r in wk]

    # 続けて使った言い回し（プロンプトに自動で入るものと同じ数え方。ここではデータの用語を外せないので、参考）
    L += ["", "## 続けて使った言い回し（直近5号で3回以上・機械が数えた参考）", ""]
    try:
        mt = ai_style.recent_morning_texts(ANALYSIS, (datetime.fromisoformat(today) + timedelta(days=1)).strftime("%Y-%m-%d"))
        wt = ai_style.recent_weekly_texts(WEEKLY_DAYS, (datetime.fromisoformat(today) + timedelta(days=1)).strftime("%Y-%m-%d"))
        for name, ts in (("朝刊", mt), ("週末版", wt)):
            reps = ai_style.repeated_phrases(ts)
            L.append(f"- {name}: " + ("、".join(f"「{g}」{c}号" for g, c in reps) if reps else "なし"))
    except Exception as e:
        L.append(f"- 数えられず: {e}")

    L += ["", "## いまの指針（ai_style_notes.md）", ""]
    for k, name in (("morning", "朝刊"), ("sat", "土曜"), ("sun", "日曜")):
        rules = ai_style.load_rules(k)
        L.append(f"- {name}に入る行: {len(rules)}行")
    L += ["", "（週末版の号数・当たり外れは weekly.html の通算の欄を参照。この一覧は ai_feedback.py が日曜の取り込みで作る）", ""]
    return "\n".join(L)


def review(today=None):
    today = today or _now().strftime("%Y-%m-%d")
    fb = load()
    if import_taps(fb):
        save(fb)
    text = build_review(today)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    p = REVIEW_DIR / f"{today}.md"
    p.write_text(text, encoding="utf-8")
    print(f"振り返り一覧: {p}")
    return p


def main():
    import argparse
    ap = argparse.ArgumentParser(description="AI朝刊・週末版の記録と振り返り")
    ap.add_argument("cmd", choices=["record", "review", "backfill"])
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    if a.cmd == "record":
        kind = (a.rest or ["morning"])[0]
        if kind not in KIND_JP:
            ap.error("record の種類は morning / sat / sun")
        record(kind)
    elif a.cmd == "review":
        review(a.date or None)
    else:
        backfill(a.rest)


if __name__ == "__main__":
    main()
