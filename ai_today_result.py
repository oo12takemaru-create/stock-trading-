# -*- coding: utf-8 -*-
"""引け後の答え合わせを早く出す → docs/ai_today_result.json（2026-11-17〜）

■ 何か
  その日の朝刊の構え → 日経平均の結果 → 当たり/外れ を、15:30 の引けのあとできるだけ早く（16:00 前後）出す。
  夕方にもサイトへ戻ってくる理由をつくる（本人方針 11/17「外れも隠さない答え合わせ」）。
  正式の成績表は今までどおり ai_record.py（19:00 台・docs/ai_record.json）。ここは速報で、
  **判定のしかたは ai_record.py と同じ**（強気寄り=上昇で当たり・守り寄り=下落で当たり・中立は数えない・騰落0は外れ）。
  ai_record.py・朝刊の採点・スコアボードは一切変えない。LLM は使わない（機械の計算だけ）。

■ 入力
  docs/ai_analysis.json（今日の号の構え）・docs/ai_record.json（過去の構えと通算の集計）・
  Yahoo の ^N225 日足（直近3か月・yf_latest.fill_latest で最新日の空欄を埋める）

■ 書かない日（前のファイルをそのまま残す）
  休場日・15:35 JST より前・今日の朝刊が無い日・今日の終値がまだ取れない日

■ 使い方
  python ai_today_result.py                  # docs/ai_today_result.json を書く
  python ai_today_result.py --dry-run        # 書かずに表示
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

JST = timezone(timedelta(hours=9))
DOCS = REPO / "docs"
OUT_NAME = "ai_today_result.json"
READY = time(15, 35)      # 引け（15:30）の5分後から

STANCE_JP = {"attack": "強気", "lean_attack": "やや強気", "neutral": "中立",
             "lean_defense": "やや守り", "defense": "守り"}
UP_ST = {"attack", "lean_attack"}
DN_ST = {"defense", "lean_defense"}


def hit_of(stance, ret):
    """ai_record.py の aggregate・ai_analysis._hit と同じ。中立は None（数えない）。騰落0は外れ"""
    if ret is None:
        return None
    if stance in UP_ST:
        return ret > 0
    if stance in DN_ST:
        return ret < 0
    return None


def streak_of(rows):
    """直近から数えた連勝(+n)・連敗(-n)。中立の日は飛ばす（数えないので連続も切らない）"""
    n, sign = 0, None
    for r in reversed(rows):
        h = hit_of(r["s"], r["ret"])
        if h is None:
            continue
        if sign is None:
            sign = h
        if h != sign:
            break
        n += 1
    return (n if sign else -n) if n else 0


def streak_jp(k):
    return f"{k}連勝" if k > 0 else (f"{-k}連敗" if k < 0 else "")


def returns(closes):
    """[(d, 終値)]（日付順）→ {d: 当日の騰落%}（前営業日の終値から）"""
    out = {}
    for (d0, c0), (d1, c1) in zip(closes, closes[1:]):
        out[d1] = (c1 / c0 - 1) * 100
    return out


def stances_of(rec, doc):
    """日付 → 構え。ai_record.json の採点の行と、ai_analysis.json の latest / history から"""
    st = {}
    for r in ((rec or {}).get("ai") or {}).get("rows") or []:
        if r.get("d") and r.get("s"):
            st[r["d"]] = r["s"]
    for e in [(doc or {}).get("latest") or {}] + list((doc or {}).get("history") or []):
        if isinstance(e, dict) and e.get("date") and e.get("stance"):
            st.setdefault(e["date"], e["stance"])
    return st


def _ma(vals, n):
    return sum(vals[-n:]) / n if len(vals) >= n else None


def week_question_result(doc, today: date, closes):
    """週の最後の営業日なら、今週の問い（朝刊の week_question）を今日の終値で判定する。無ければ None"""
    try:
        import jp_bizday
    except Exception:
        return None
    mon = today - timedelta(days=today.weekday())
    biz = [mon + timedelta(days=i) for i in range(5) if jp_bizday.is_bizday(mon + timedelta(days=i))]
    if not biz or today != biz[-1]:
        return None
    wq = None
    for e in [(doc or {}).get("latest") or {}] + list((doc or {}).get("history") or []):
        q = e.get("week_question") if isinstance(e, dict) else None
        if isinstance(q, dict) and q.get("id") and mon.isoformat() <= (e.get("date") or "") <= today.isoformat():
            wq, set_on = q, e["date"]
    if not wq:
        return None
    vals = [c for _, c in closes]
    if wq.get("kind") == "ma":
        lv = _ma(vals, int(wq.get("n") or 0)) if wq.get("n") else None
    else:
        lv = wq.get("level")
    if lv is None:
        return None
    c = vals[-1]
    return {"id": wq["id"], "q": wq.get("q", ""), "set_on": set_on, "asof": today.isoformat(),
            "close": round(c, 2), "level": round(float(lv), 2), "hit": bool(c > float(lv)),
            "rule": wq.get("rule", "")}


def build(doc, rec, closes, now: datetime):
    """答え合わせを組み立てる。書かない日は (None, 理由)"""
    today = now.date()
    ts = today.isoformat()
    L = (doc or {}).get("latest") or {}
    if L.get("date") != ts or not L.get("stance"):
        return None, f"今日（{ts}）の朝刊が無い（latest={L.get('date')}）"
    if not closes or closes[-1][0] != ts or len(closes) < 2:
        return None, f"今日の終値がまだ無い（最新 {closes[-1][0] if closes else '—'}）"
    rets = returns(closes)
    ret = round(rets[ts], 2)
    stance = L["stance"]
    machine = L.get("machine_stance")
    hit = hit_of(stance, ret)

    st = stances_of(rec, doc)
    rows = [{"d": d, "s": st[d], "ret": round(rets[d], 2)} for d in sorted(rets) if d in st and d <= ts]
    month = ts[:7]
    mrows = [r for r in rows if r["d"][:7] == month]
    mh = [hit_of(r["s"], r["ret"]) for r in mrows]
    rec_rows = {r.get("d") for r in ((rec or {}).get("ai") or {}).get("rows") or []}
    stats = ((rec or {}).get("ai") or {}).get("stats") or {}
    tw = sum(int(g.get("hit") or 0) for g in stats.values())
    tl = sum(int(g.get("judged") or 0) - int(g.get("hit") or 0) for g in stats.values())
    for r in rows:           # ai_record.json にまだ無い日（今日など）だけ足す
        if r["d"] in rec_rows:
            continue
        h = hit_of(r["s"], r["ret"])
        tw += h is True
        tl += h is False
    k = streak_of(rows)
    out = {
        "date": ts,
        "updated": now.isoformat(timespec="seconds"),
        "issue": L.get("issue"),
        "headline": L.get("headline", ""),
        "stance": stance,
        "stance_jp": STANCE_JP.get(stance, stance),
        "machine_stance": machine,
        "n225_prev_close": round(closes[-2][1], 2),
        "n225_close": round(closes[-1][1], 2),
        "n225_ret": ret,
        "hit": hit,
        "result_jp": "当たり" if hit is True else ("外れ" if hit is False else "中立の日（勝敗に数えない）"),
        "machine_hit": hit_of(machine, ret) if machine else None,
        "month": {"month": month, "win": sum(1 for h in mh if h is True),
                  "lose": sum(1 for h in mh if h is False), "neutral": sum(1 for h in mh if h is None)},
        "total": {"win": tw, "lose": tl},
        "streak": k,
        "streak_jp": streak_jp(k),
        "basis": "朝刊の構え（寄り付き前に公開）と、その日の日経平均の騰落（終値÷前営業日の終値）を機械で照合。"
                 "強気寄りの日は上昇で当たり・守り寄りの日は下落で当たり・中立の日は数えない（ai_record.py と同じ）。",
        "note": "引け後の速報（Yahoo の日足）。正式の成績表は ai_record.json（19時台に更新）。",
    }
    wr = week_question_result(doc, today, closes)
    if wr:
        out["week_question"] = wr
    return out, None


def n225_closes(today: date):
    import yfinance as yf
    from yf_latest import fill_latest
    df = yf.download("^N225", period="3mo", progress=False, auto_adjust=False, threads=False)
    c = fill_latest("^N225", df["Close"], verbose=False)
    return [(i.strftime("%Y-%m-%d"), float(v)) for i, v in c.items()
            if v == v and i.strftime("%Y-%m-%d") <= today.isoformat()]


def _load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--docs", default=str(DOCS))
    ap.add_argument("--force-time", action="store_true", help="15:35 前でも動かす（手元の確認用）")
    a = ap.parse_args()
    docs = Path(a.docs)
    now = datetime.now(JST)

    import jp_bizday
    if not jp_bizday.is_bizday(now.date()):
        print(f"休場日（{now.date()}）→ 書かない")
        return
    if now.time() < READY and not a.force_time:
        print(f"引け前（{now:%H:%M} JST）→ 書かない")
        return
    data, why = build(_load(docs / "ai_analysis.json"), _load(docs / "ai_record.json"),
                      n225_closes(now.date()), now)
    if data is None:
        print(f"{why} → 書かない")
        return
    print(json.dumps(data, ensure_ascii=False, indent=1))
    if a.dry_run:
        return
    out = docs / OUT_NAME
    old = _load(out) or {}
    same = {k: v for k, v in old.items() if k != "updated"} == {k: v for k, v in data.items() if k != "updated"}
    if same:
        print("前回と同じ中身 → 書かない")
        return
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"書いた: {out.name}")


if __name__ == "__main__":
    main()
