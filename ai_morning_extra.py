# -*- coding: utf-8 -*-
"""AI朝刊の「ほかに無い」欄の材料と検査（2026-11-17〜）

本人の方針（11/17）: AI朝刊をサイトの一番の強みに。来た人がまた見たいと思う・気づく・他に無い中身に。
差になるのは「アーサーというキャラクター・外れも隠さない答え合わせ・ほかに無い数え上げ（1965年からの記録）・
読者の参加」。ここはその材料（prep が PROMPT.txt に入れる）と、新しい欄の検査（ingest）だけを持つ。

既存のスタンスの決め方・機械判定・答え合わせ（ai_record.py）・禁止語の検査は変えない。**足すだけ**。
どの材料も取れない日は入れない（朝刊は止めない）。材料が無い日に書かれた欄は外す。

■ 材料（ai_analysis.build_inputs が入れる節）
  - いまの場面: https://kaburadar.jp/data/bamen_now.json（サイトの Site Rebuild が焼く・日経平均の1965年以降の記録）
  - 相場の温度計: docs/thermo100.json を −20℃〜60℃ にした今日・前日・1週間前・判定の変化
  - 朝刊の成績: 前回の号の構え → 日経平均 → 当たり/外れ・今月と通算・連勝/連敗
      元は docs/ai_today_result.json（夕方の答え合わせ）→ 無ければ docs/ai_record.json
  - 読者の1タップ: 環境変数 AI_VOTE_URL の JSON（{"date","up","down"}・サイト側で形が決まったら置く）。無ければ入れない
  - 今週の問い: 週の最初の営業日に候補（機械で判定できる形だけ）→ 週の途中は途中経過 → 週の最後の営業日に機械の判定

■ 出力の欄（どれも任意。材料が無い日は省く）
  scene {title, body} / changes [..3] / one_line / arthur_note / week_question {id, q} /
  reader_vote {body}（week_answer は 11/17 の決定で朝刊では書かない＝書かれても外す）
  落ちた欄はその欄だけ外し、朝刊の本体は出す（deep と同じ）。
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
SCENE_URL = "https://kaburadar.jp/data/bamen_now.json"
DOCS = Path("docs")

# 節の見出し（材料があるかどうかを ingest が入力データから見分けるのにも使う）
H_SCENE = "## いまの場面（日経平均の1965年以降の記録・kaburadar.jp/bamen）"
H_THERMO = "## 相場の温度計（−20℃〜60℃・0℃より下ほど冷え込み）"
H_RECORD = "## 朝刊の成績（機械採点・中立の日は数えない）"
H_VOTE = "## 読者の1タップ（上で終わる／下で終わる）"
H_WEEK = "## 今週の問い（機械で判定できる形）"
WEEKQ_TAG = "週の問いの候補JSON:"
WEEKA_TAG = "週の問いの判定JSON:"

STANCE_JP = {"attack": "強気", "lean_attack": "やや強気", "neutral": "中立",
             "lean_defense": "やや守り", "defense": "守り"}
UP_ST = {"attack", "lean_attack"}
DN_ST = {"defense", "lean_defense"}

# 新しい欄の字数
SCENE_TITLE_MAX = 30
SCENE_BODY_MAX = 120
SCENE_MAX_SENT = 2
CHANGE_MAX = 40
CHANGES_MAX_N = 3
ONE_LINE_MAX = 40
ONE_LINE_MAX_NUM = 1
NOTE_MAX = 60
NOTE_RECENT = 5           # 直近この号数の arthur_note と同じ文は外す
WEEK_Q_MAX = 40
WEEK_A_MAX = 120
VOTE_MAX = 80

# 新しい欄でだけ追加で外す言葉（朝刊の本体の検査は変えない。本体の禁止語は deep_material.words_ok で当てる）
EXTRA_BANNED = ["予想", "狙い目", "有望", "チャンス", "面白い", "過熱"]

# JPX 由来の数字（投資部門別・信用残・空売り）が載っている行の目印。one_line（X・メールの件名）に入れない
JPX_MARKS = ("JPX", "投資部門", "海外投資家", "信用", "空売り", "裁定", "個人投資家", "信託銀行")


def _today(now=None):
    return (now or datetime.now(JST)).date()


def _md(d: str) -> str:
    try:
        x = date.fromisoformat(str(d)[:10])
        return f"{x.month}/{x.day}"
    except Exception:
        return str(d)


def _fresh(d: str, today: date, days: int = 7) -> bool:
    """d（YYYY-MM-DD）が今日より前で、days 日以内か"""
    try:
        x = date.fromisoformat(str(d)[:10])
    except Exception:
        return False
    return x < today and (today - x).days <= days


def _load_docs(name):
    p = DOCS / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def fetch_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (kaburadar ai-morning prep)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# ────────────────────────── 温度計の ℃（thermo100.py と同じ式） ──────────────────────────
def celsius(v):
    """0〜100 → ℃（−20〜60）。thermo100.celsius と同じ式（thermo100 は pandas を読むのでここで持つ）"""
    if v is None:
        return None
    return int(round(float(v) * 0.8 - 20))


def c_label(c) -> str:
    return f"{'−' if c < 0 else ''}{abs(c)}℃"


# ────────────────────────── 材料 ──────────────────────────
def scene_lines(today=None, doc=None, fetch=None):
    """いまの場面（bamen_now.json）。取れない・古い・当てはまる場面が無い日は []"""
    today = today or _today()
    if doc is None:
        try:
            doc = (fetch or fetch_json)(SCENE_URL)
        except Exception as e:
            print(f"いまの場面を取れず（入れない）: {e}", file=sys.stderr)
            return []
    if not isinstance(doc, dict) or not _fresh(doc.get("date"), today):
        print(f"いまの場面が古い・空（入れない）: {doc.get('date') if isinstance(doc, dict) else doc}", file=sys.stderr)
        return []
    items = [x for x in (doc.get("today") or []) if isinstance(x, dict) and x.get("name") and x.get("count")]
    if not items:
        return []
    head = f"（{_md(doc['date'])} の終値"
    if doc.get("close") is not None:
        head += f" {float(doc['close']):,.2f}円"
    if doc.get("chg") is not None:
        head += f"・前日比{float(doc['chg']):+.2f}%"
    head += "）"
    out = [H_SCENE, f"日経平均の日足の終値から機械的に数えた過去の記録{head}。この先を示すものではない（表示側が注記を添える）"]
    for x in items[:2]:
        since = str(x.get("since") or "1965")[:4]
        s = f"- {x['name']}"
        if x.get("drawdown_now") is not None:
            s += f"（{_md(doc['date'])} の終値は52週高値から{float(x['drawdown_now']):+.2f}%）"
        elif x.get("mark"):
            s += f"（{x['mark']}）"
        s += f": {since}年以降{int(x['count'])}回"
        if x.get("clusters") and x["clusters"] != x["count"]:
            s += f"（続いた日をまとめると{int(x['clusters'])}回）"
        parts = []
        for a in x.get("after") or []:
            try:
                p = f"{a['label']}に上がっていたのは{int(a['n'])}回中{int(a['up'])}回（下がった{int(a['down'])}回"
                if a.get("median") is not None:
                    p += f"・中央値{float(a['median']):+.2f}%"
                parts.append(p + "）")
            except Exception:
                continue
        if parts:
            s += "。" + "。".join(parts)
        if x.get("last_hit"):
            s += f"。この前に当てはまったのは {x['last_hit']}"
        if x.get("few"):
            s += "。※件数が少ない参考値"
        out.append(s)
    return out


def thermo_lines(today=None, doc=None):
    """相場の温度計の ℃。thermo100.json が無い・古い日は []"""
    today = today or _today()
    doc = doc if doc is not None else _load_docs("thermo100.json")
    if not isinstance(doc, dict) or doc.get("value") is None or not _fresh(doc.get("trade_date"), today):
        return []
    v = (doc.get("celsius") or {}).get("value")
    v = celsius(doc["value"]) if v is None else v
    cmp = doc.get("compare") or {}
    s = f"- {_md(doc['trade_date'])} の終値時点: {c_label(v)}（{doc.get('regime_jp', '')}）"
    for k, lab in (("yesterday", "前日"), ("week", "1週間前")):
        h = cmp.get(k)
        if h and h.get("value") is not None:
            s += f"／{lab}（{_md(h['d'])}） {c_label(celsius(h['value']))}（{h.get('regime_jp', '')}）"
    out = [H_THERMO, s]
    y = cmp.get("yesterday")
    if doc.get("regime_changed") and y:
        out.append(f"- 判定が前日の{y.get('regime_jp', '')}から{doc.get('regime_jp', '')}に変わった")
    else:
        out.append("- 判定は前日から変わっていない")
    out.append("- 帯: 総崩れ −20〜−9℃／弱気 −8〜11℃／中立 12〜27℃／強気 28〜60℃（0〜100 の温度を ℃ = 値×0.8−20 で読み替えたもの）")
    return out


def hit_of(stance, ret):
    """ai_record.py の aggregate・ai_analysis._hit と同じ判定。中立は None（数えない）。騰落0は外れ"""
    if ret is None:
        return None
    if stance in UP_ST:
        return ret > 0
    if stance in DN_ST:
        return ret < 0
    return None


def streak_of(rows):
    """直近から数えた連勝(+n)・連敗(-n)。rows は [{d, s, ret}]（日付順）。中立の日は飛ばす"""
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


def tally(rows, rec_stats=None, rec_days=()):
    """勝ち・負け。rec_stats（ai_record.json の ai.stats）があれば通算はそこに rows の新しい日（rec_days に無い日）を足す"""
    hs = [hit_of(r["s"], r["ret"]) for r in rows]
    win = sum(1 for h in hs if h is True)
    lose = sum(1 for h in hs if h is False)
    if rec_stats is None:
        return win, lose
    tw = sum(int(g.get("hit") or 0) for g in rec_stats.values())
    tl = sum(int(g.get("judged") or 0) - int(g.get("hit") or 0) for g in rec_stats.values())
    for r in rows:
        if r["d"] in rec_days:
            continue
        h = hit_of(r["s"], r["ret"])
        tw += h is True
        tl += h is False
    return tw, tl


def merged_rows(rec, result, before):
    """ai_record.json の採点の行（before より前）＋ 夕方の答え合わせ（ai_today_result.json）の日（無ければ足す）"""
    rows = [{"d": r["d"], "s": r["s"], "ret": r["ret"]} for r in ((rec or {}).get("ai") or {}).get("rows") or []
            if r.get("d") and r.get("s") and r.get("ret") is not None and r["d"] < before]
    have = {r["d"] for r in rows}
    if isinstance(result, dict) and result.get("date") and result.get("date") < before \
            and result["date"] not in have and result.get("stance") and result.get("n225_ret") is not None:
        rows.append({"d": result["date"], "s": result["stance"], "ret": result["n225_ret"]})
    return sorted(rows, key=lambda r: r["d"])


def streak_text(k: int) -> str:
    return f"{k}連勝" if k > 0 else (f"{-k}連敗" if k < 0 else "連続なし")


def record_lines(today=None, prev=None, rec=None, result=None):
    """前回の号の構え → その日の日経平均 → 当たり/外れ・今月と通算・連勝/連敗。
    review の材料（前回の構え・見出し）と重ならないよう、採点の結果だけを出す"""
    today = today or _today()
    ts = today.isoformat()
    rec = rec if rec is not None else _load_docs("ai_record.json")
    result = result if result is not None else _load_docs("ai_today_result.json")
    rows = merged_rows(rec, result, ts)
    if not rows:
        return []
    out = [H_RECORD]
    pd_ = (prev or {}).get("date")
    last = next((r for r in rows if r["d"] == pd_), None) if pd_ else None
    if pd_ and last:
        h = hit_of(last["s"], last["ret"])
        res = "当たり" if h is True else ("外れ" if h is False else "中立の日なので勝敗に数えない")
        out.append(f"- 前回（{_md(pd_)}）の構え: {STANCE_JP.get(last['s'], last['s'])} → その日の日経平均 {last['ret']:+.2f}% → {res}")
    elif pd_:
        out.append(f"- 前回（{_md(pd_)}）の号はまだ採点されていない（採点が済んでいない日は勝敗に数えない）")
    month = ts[:7]
    mw, ml = tally([r for r in rows if r["d"][:7] == month])
    days = {r["d"] for r in ((rec or {}).get("ai") or {}).get("rows") or []}
    stats = ((rec or {}).get("ai") or {}).get("stats")
    tw, tl = tally(rows, stats, days) if isinstance(stats, dict) else tally(rows)
    k = streak_of(rows)
    out.append(f"- 今月（{int(month[5:])}月）: {mw}勝{ml}敗／通算: {tw}勝{tl}敗／いまの流れ: {streak_text(k)}")
    return out


def vote_lines(today=None, doc=None, fetch=None):
    """読者の1タップの結果。AI_VOTE_URL が無い・読めない・古い日は []（欄を出さない）"""
    today = today or _today()
    if doc is None:
        url = os.environ.get("AI_VOTE_URL", "").strip()
        if not url:
            return []
        try:
            doc = (fetch or fetch_json)(url)
        except Exception as e:
            print(f"読者の1タップを取れず（入れない）: {e}", file=sys.stderr)
            return []
    try:
        up, dn = int(doc.get("up") or 0), int(doc.get("down") or 0)
    except Exception:
        return []
    if not _fresh(doc.get("date"), today) or up + dn <= 0:
        return []
    n = up + dn
    return [H_VOTE,
            f"- {_md(doc['date'])} の読者の1タップ（{n}人）: 上で終わる {up}人（{round(up / n * 100)}%）／"
            f"下で終わる {dn}人（{round(dn / n * 100)}%）"]


# ────────────────────────── 今週の問い ──────────────────────────
def week_days(today: date):
    """今日の週（月〜金）の最初と最後の営業日。営業日が無ければ (None, None)"""
    import jp_bizday
    mon = today - timedelta(days=today.weekday())
    biz = [mon + timedelta(days=i) for i in range(5) if jp_bizday.is_bizday(mon + timedelta(days=i))]
    return (biz[0], biz[-1]) if biz else (None, None)


def _ma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def week_candidates(series, monday: date):
    """問いの候補（どれも「週の判定日の終値が水準を上回るか」で機械的に判定できる形）。
    series は [(YYYY-MM-DD, 終値)]（今日より前・日付順）"""
    cl = [c for _, c in series]
    if len(cl) < 30:
        return []
    c = cl[-1]
    out = []
    ma25, ma200 = _ma(cl, 25), _ma(cl, 200)
    if ma25:
        out.append({"id": "ma25", "kind": "ma", "n": 25,
                    "rule": "判定日の終値が、その日の25日移動平均（終値の単純平均）を上回るか",
                    "now": f"直近の終値 {c:,.0f}円・25日線 {ma25:,.0f}円（いまは{'上' if c > ma25 else '下'}）"})
    if ma200:
        out.append({"id": "ma200", "kind": "ma", "n": 200,
                    "rule": "判定日の終値が、その日の200日移動平均を上回るか",
                    "now": f"直近の終値 {c:,.0f}円・200日線 {ma200:,.0f}円（いまは{'上' if c > ma200 else '下'}）"})
    prev_week = [v for d, v in series if d < monday.isoformat()]
    if prev_week:
        lv = round(prev_week[-1], 2)
        out.append({"id": "week_up", "kind": "level", "level": lv,
                    "rule": f"判定日の終値が、先週末の終値 {lv:,.2f}円 を上回るか（週でプラスか）",
                    "now": f"先週末の終値 {lv:,.2f}円"})
    floor = math.floor(c / 1000) * 1000
    out.append({"id": "hold", "kind": "level", "level": float(floor),
                "rule": f"判定日の終値が {floor:,}円 を上回るか（いまの千円台を保つか）",
                "now": f"直近の終値 {c:,.0f}円"})
    out.append({"id": "reach", "kind": "level", "level": float(floor + 1000),
                "rule": f"判定日の終値が {floor + 1000:,}円 を上回るか（1つ上の千円台に届くか）",
                "now": f"直近の終値 {c:,.0f}円"})
    return out


def judge(spec, series):
    """問いを判定する。series は判定日までの [(d, 終値)]。戻り値: {hit, asof, close, level}"""
    if not series:
        return None
    cl = [c for _, c in series]
    d, c = series[-1]
    if spec.get("kind") == "ma":
        lv = _ma(cl, int(spec["n"]))
        if lv is None:
            return None
    else:
        lv = float(spec["level"])
    return {"hit": bool(c > lv), "asof": d, "close": round(c, 2), "level": round(lv, 2)}


def find_week_question(dst, monday: date, today: date):
    """今週（monday 以降・今日より前）の号で立てた week_question。無ければ None"""
    try:
        doc = json.loads(Path(dst).read_text(encoding="utf-8"))
    except Exception:
        return None
    for e in [doc.get("latest") or {}] + list(doc.get("history") or []):
        if not isinstance(e, dict):
            continue
        d = e.get("date") or ""
        wq = e.get("week_question")
        if monday.isoformat() <= d < today.isoformat() and isinstance(wq, dict) and wq.get("id"):
            return dict(wq, set_on=wq.get("set_on") or d)
    return None


def n225_series(today: date, period="1y"):
    """日経平均の終値 [(d, 終値)]（今日より前）。取れなければ []"""
    try:
        import yfinance as yf
        from yf_latest import fill_latest
        df = yf.download("^N225", period=period, progress=False, auto_adjust=False, threads=False)
        c = fill_latest("^N225", df["Close"], verbose=False)
        return [(i.strftime("%Y-%m-%d"), float(v)) for i, v in c.items()
                if i.strftime("%Y-%m-%d") < today.isoformat() and v == v]
    except Exception as e:
        print(f"日経平均の終値を取れず（今週の問いを入れない）: {e}", file=sys.stderr)
        return []


def week_lines(today=None, dst="docs/ai_analysis.json", series=None):
    """今週の問いの材料。週の最初の営業日=候補、途中=途中経過、最後の営業日=機械の判定。
    1日しか営業日の無い週・データが取れない日は []"""
    today = today or _today()
    import jp_bizday
    first, last = week_days(today)
    if first is None or first == last or not jp_bizday.is_bizday(today):
        return []
    if today == first:
        series = series if series is not None else n225_series(today)
        cands = week_candidates(series, first)
        if not cands:
            return []
        out = [H_WEEK,
               f"今日は週の最初の号。今週の問いを1つ立てる（下の候補から id を1つ選ぶ）。正式な答えは週の最後の営業日（{_md(last.isoformat())}）の終値で、"
               "その日の夕方の答え合わせと土曜の週末版で出す（朝刊では答えを言い切らない）"]
        for x in cands:
            out.append(f"- [{x['id']}] {x['rule']}／{x['now']}")
        out.append(WEEKQ_TAG + " " + json.dumps(
            [{k: v for k, v in x.items() if k in ("id", "kind", "n", "level", "rule")} for x in cands],
            ensure_ascii=False))
        return out
    wq = find_week_question(dst, first, today)
    if not wq:
        return []
    series = series if series is not None else n225_series(today)
    j = judge(wq, series)
    if not j:
        return []
    side = "上回っている" if j["hit"] else "上回っていない"
    lv = "25日線" if wq.get("id") == "ma25" else ("200日線" if wq.get("id") == "ma200" else "水準")
    out = [H_WEEK, f"- 今週の問い（{_md(wq['set_on'])}に立てた）: {wq.get('q', '')}",
           f"- 決まり: {wq.get('rule', '')}"]
    if today == last:
        # 2026-11-17 統括の決定: 正式な答えは週の最後の営業日の終値。金曜の朝刊は途中経過として書き、判定は言い切らない
        out.append(f"- 途中経過（{_md(j['asof'])} の終値まで）: 終値 {j['close']:,.2f}円・{lv} {j['level']:,.2f}円 → いまは{side}。"
                   "正式な答えは今日の終値で決まり、夕方の答え合わせと土曜の週末版で出す。朝刊では達成・未達を言い切らない")
    else:
        out.append(f"- 途中経過（{_md(j['asof'])} の終値）: 終値 {j['close']:,.2f}円・{lv} {j['level']:,.2f}円 → いまは{side}。"
                   "触れるなら一言だけ（週の最後の営業日の号で答え合わせする）")
    return out


def week_result_lines(mon: date, fri: date, ana=None, result=None, series=None):
    """土曜の週末版（今週の振り返り）の材料: 今週の問いと、その正式な答え（週の最後の営業日の終値で機械の判定）。
    元は金曜夕方の docs/ai_today_result.json の week_question → 無ければ ai_analysis.json の問いを終値で判定。
    今週の問いが無い・終値が取れない週は []"""
    ana = ana if ana is not None else _load_docs("ai_analysis.json")
    result = result if result is not None else _load_docs("ai_today_result.json")
    _, last = week_days(mon)
    if last is None:
        return []
    wr = (result or {}).get("week_question") if isinstance(result, dict) else None
    if not (isinstance(wr, dict) and str(wr.get("asof") or "") == last.isoformat() and wr.get("hit") is not None):
        wr = None
        wq = None
        for e in [(ana or {}).get("latest") or {}] + list((ana or {}).get("history") or []):
            q = e.get("week_question") if isinstance(e, dict) else None
            if isinstance(q, dict) and q.get("id") and mon.isoformat() <= (e.get("date") or "") <= fri.isoformat():
                wq = dict(q, set_on=e["date"])
        if not wq:
            return []
        series = series if series is not None else n225_series(fri + timedelta(days=1))
        series = [x for x in series if x[0] <= fri.isoformat()]
        j = judge(wq, series)
        if not j or j["asof"] != last.isoformat():     # 週の最後の終値がまだ取れていない
            return []
        wr = dict(wq, **j)
    lv = "25日線" if wr.get("id") == "ma25" else ("200日線" if wr.get("id") == "ma200" else "水準")
    return [f"- 今週の問い（{_md(wr.get('set_on') or mon.isoformat())}の朝刊で立てた）: {wr.get('q', '')}",
            f"- 決まり: {wr.get('rule', '')}",
            f"- 答え（{_md(wr['asof'])} の終値で機械が判定）: 終値 {float(wr['close']):,.2f}円・{lv} {float(wr['level']):,.2f}円 → "
            f"{'達成' if wr['hit'] else '未達'}"]


def material_lines(dst, prev=None, today=None):
    """build_inputs に足す節（取れたものだけ）。ここで朝刊は止めない"""
    today = today or _today()
    out = []
    for name, fn in (("いまの場面", lambda: scene_lines(today)),
                     ("温度計", lambda: thermo_lines(today)),
                     ("朝刊の成績", lambda: record_lines(today, prev)),
                     ("読者の1タップ", lambda: vote_lines(today)),
                     ("今週の問い", lambda: week_lines(today, dst))):
        try:
            ls = fn()
        except Exception as e:
            print(f"{name}の材料を作れず（入れない）: {e}", file=sys.stderr)
            ls = []
        if ls:
            out += [""] + ls
    return out


# ────────────────────────── 検査（ingest） ──────────────────────────
def _tag_json(src, tag):
    for ln in (src or "").splitlines():
        if ln.startswith(tag):
            try:
                return json.loads(ln[len(tag):].strip())
            except Exception:
                return None
    return None


def jpx_numbers(src):
    """入力データのうち JPX 由来の行にだけ出てくる数字（正規化した文字列の集合）"""
    import deep_material as dm
    jpx, other = set(), set()
    in_jpx = False
    for ln in (src or "").splitlines():
        s = ln.strip()
        if s.startswith("[") or s.startswith("## "):
            in_jpx = s.startswith("[JPX")
        nums = {dm._canon(x) for x in dm._nums(ln)}
        if in_jpx or any(m in ln for m in JPX_MARKS):
            jpx |= nums
        else:
            other |= nums
    return jpx - other


def _text_err(text, src, banned_patterns=()):
    import deep_material as dm
    err = dm._check_text(text, src)
    if err:
        return err
    n = dm._norm(text)
    for w in EXTRA_BANNED:
        if w in n:
            return f"使わない言葉「{w}」"
    for pat in banned_patterns:
        if re.search(pat, text):
            return f"禁止パターン検出: {pat}"
    return None


def _str_ok(x, mx, what):
    if not isinstance(x, str) or not x.strip():
        return f"{what} が空"
    if len(x) > mx:
        return f"{what} は{mx}字まで（{len(x)}字）"
    return None


def _recent_notes(dst, today_s, n=NOTE_RECENT):
    try:
        doc = json.loads(Path(dst).read_text(encoding="utf-8"))
    except Exception:
        return []
    es = [e for e in [doc.get("latest")] + list(doc.get("history") or [])
          if isinstance(e, dict) and e.get("date") and e.get("date") < today_s]
    es.sort(key=lambda e: e["date"], reverse=True)
    return [e["arthur_note"].strip() for e in es[:n] if isinstance(e.get("arthur_note"), str)]


def check_scene(v, src, bp):
    if H_SCENE not in src:
        return "いまの場面の材料が無い日"
    if not isinstance(v, dict):
        return "scene が dict でない"
    import deep_material as dm
    err = _str_ok(v.get("title"), SCENE_TITLE_MAX, "scene.title") or _str_ok(v.get("body"), SCENE_BODY_MAX, "scene.body")
    if err:
        return err
    if dm.count_sentences(v["body"]) > SCENE_MAX_SENT:
        return f"scene.body は{SCENE_MAX_SENT}文まで"
    if "回中" not in v["body"]:
        return "scene.body に「◯回中◯回」が無い"
    return _text_err(v["title"] + "\n" + v["body"], src, bp)


def check_changes(v, src, bp):
    if not isinstance(v, list) or not (1 <= len(v) <= CHANGES_MAX_N):
        return f"changes は1〜{CHANGES_MAX_N}行"
    for x in v:
        err = _str_ok(x, CHANGE_MAX, "changes の行") or _text_err(x, src, bp)
        if err:
            return err
    return None


def check_one_line(v, src, bp):
    import deep_material as dm
    err = _str_ok(v, ONE_LINE_MAX, "one_line")
    if err:
        return err
    if dm.count_numbers(v) > ONE_LINE_MAX_NUM:
        return f"one_line の数字は{ONE_LINE_MAX_NUM}つまで"
    bad = {dm._canon(x) for x in dm._nums(v)} & jpx_numbers(src)
    if bad:
        return f"one_line に JPX 由来の数字: {', '.join(sorted(bad))}"
    return _text_err(v, src, bp)


def check_note(v, src, bp, recent=()):
    err = _str_ok(v, NOTE_MAX, "arthur_note") or _text_err(v, src, bp)
    if err:
        return err
    if v.strip() in recent:
        return "arthur_note が直近の号と同じ文"
    return None


def check_week_question(v, src, bp):
    cands = _tag_json(src, WEEKQ_TAG)
    if not cands:
        return "今週の問いを立てる日ではない（候補が入力に無い）"
    if not isinstance(v, dict):
        return "week_question が dict でない"
    c = next((x for x in cands if x.get("id") == v.get("id")), None)
    if not c:
        return f"week_question.id が候補に無い: {v.get('id')}"
    import deep_material as dm
    err = _str_ok(v.get("q"), WEEK_Q_MAX, "week_question.q") or dm.q_ok(v.get("q"), "week_question")
    if err:
        return err
    err = _text_err(v["q"], src, bp)
    if err:
        return err
    # 判定に使う決まりは機械の候補で上書きする（AI が書いた数字では判定しない）
    q = v["q"]
    v.clear()
    v.update({"id": c["id"], "q": q, **{k: c[k] for k in ("kind", "n", "level", "rule") if k in c}})
    return None


def check_week_answer(v, src, bp):
    """2026-11-17 統括の決定で、朝刊では今週の問いに答えない（正式な答えは週の最後の終値・夕方と土曜版）。
    朝刊の材料に判定の行は出ないので、week_answer は書かれても外れる"""
    m = _tag_json(src, WEEKA_TAG)
    if not m:
        return "朝刊では今週の問いに答えない（正式な答えは週の最後の終値で、夕方の答え合わせと土曜版）"
    if not isinstance(v, dict):
        return "week_answer が dict でない"
    if v.get("hit") is not m.get("hit"):
        return f"week_answer.hit が機械の判定（{m.get('hit')}）と違う"
    err = _str_ok(v.get("body"), WEEK_A_MAX, "week_answer.body") or _text_err(v["body"], src, bp)
    if err:
        return err
    body = v["body"]
    v.clear()
    v.update({"q": m.get("q") or "", "hit": m["hit"], "body": body,
              "asof": m.get("asof"), "close": m.get("close"), "level": m.get("level"), "id": m.get("id")})
    return None


def check_vote(v, src, bp):
    if H_VOTE not in src:
        return "読者の1タップの材料が無い日"
    if not isinstance(v, dict):
        return "reader_vote が dict でない"
    return _str_ok(v.get("body"), VOTE_MAX, "reader_vote.body") or _text_err(v["body"], src, bp)


EXTRA_KEYS = ("scene", "changes", "one_line", "arthur_note", "week_question", "week_answer", "reader_vote")


def check_extras(data, src, dst="docs/ai_analysis.json", today_s=None, banned_patterns=()):
    """新しい欄を1つずつ検査し、落ちた欄だけ外す（本体は出す）。戻り値: {欄: 落ちた理由}"""
    if not isinstance(data, dict):
        return {}
    today_s = today_s or _today().isoformat()
    src = src or ""
    dropped = {}
    checks = {
        "scene": lambda v: check_scene(v, src, banned_patterns),
        "changes": lambda v: check_changes(v, src, banned_patterns),
        "one_line": lambda v: check_one_line(v, src, banned_patterns),
        "arthur_note": lambda v: check_note(v, src, banned_patterns, _recent_notes(dst, today_s)),
        "week_question": lambda v: check_week_question(v, src, banned_patterns),
        "week_answer": lambda v: check_week_answer(v, src, banned_patterns),
        "reader_vote": lambda v: check_vote(v, src, banned_patterns),
    }
    for k in EXTRA_KEYS:
        if k not in data:
            continue
        if not src:
            err = "入力データが無いので数字を確かめられない"
        else:
            try:
                err = checks[k](data[k])
            except Exception as e:
                err = f"検査中の例外: {e}"
        if err:
            data.pop(k, None)
            dropped[k] = err
            print(f"警告: {k} を検査で外した（本体は出す）: {err}", file=sys.stderr)
    return dropped


# ────────────────────────── プロンプトに足す指示 ──────────────────────────
EXTRA_GUIDE = """# 新しい欄（材料がある日だけ書く・無い日はキーごと省く）
どの欄も、入力データにある数字だけで書く。検査に落ちた欄はその欄だけ載らない（朝刊の本体は載る）。
先の値動きを言い当てる言い方・期待をあおる言い方はしない（本体の厳守事項と同じ）。
- one_line: 今日の結論を1文（40字まで・数字は1つまで）。スマホの最初の画面とメールの件名・X に使う。投資部門別・信用残など JPX 由来の数字は入れない。毎日書く
- arthur_note: アーサー自身の一言（60字まで・です・ます）。前回が外れなら反省を一言、当たりでもおごらない。直近の号と同じ文は載らない。毎日書く
- changes: 昨日から変わったことを最大3行（各40字まで）。温度計の ℃・構え・その日の主役のうち、実際に変わったものだけ。変わっていなければ「変わっていない」も1行でよい
- scene: 入力に「いまの場面」の節がある日だけ。title（30字まで）と body（1〜2文・120字まで）。body に「1965年以降◯回、◯◯に上がっていたのは◯回中◯回」の形を入れる。注記（この先を示すものではない）は表示側が添えるので書かない
- week_question: 入力に「今週の問い」の候補がある日（週の最初の号）だけ。候補の id を1つ選び、q に読み手向けの問いを1つ（40字まで・末尾は「？」か「か」）。例「今週、日経平均は25日線を回復できるか」。判定は機械がするので、候補の決まりと違う問いにしない
- 週の途中と週の最後の号で「今週の問い」の途中経過が入力にあれば、arthur_note か today_watch で一言だけ触れてよい（新しい欄は作らない）。正式な答えは週の最後の終値で決まるので、朝刊では達成・未達を言い切らない
- reader_vote: 入力に「読者の1タップ」の節がある日だけ。読者の見立てと実際の結果（またはアーサーの構え）を並べて1〜2文（80字まで）"""

SCHEMA_EXTRA = """,
  "one_line": "今日の結論を1文（40字まで・数字は1つまで）",
  "arthur_note": "アーサー自身の一言（60字まで）",
  "changes": ["昨日から変わったこと（各40字まで・最大3行）"],
  "scene": {"title": "いまの場面の見出し（30字まで）", "body": "1965年以降◯回・◯回中◯回の形で1〜2文（120字まで）"},
  "week_question": {"id": "候補の id", "q": "今週の問い（40字まで）"},
  "reader_vote": {"body": "読者の1タップと結果を並べて（80字まで）"}"""
