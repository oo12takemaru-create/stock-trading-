# -*- coding: utf-8 -*-
"""アーサーの質問箱 Vol.2〜（2026-11-15〜）: 定番「いつ手じまいするか」の1問＋掲示板から2問（2026-11-10）

■ 毎週の問いの組み立て（qa_material.questions_for から呼ばれる。qa_questions.json の week がその日曜なら、そちらが優先）
  1. 定番の1問: 型 (a)(b)(c) を週替わり（2026-11-15 は (a)）。その週の相場の形に合う型を優先する
       温度計が今週「守り」（弱気・パニック）に変わった → (c) ／ 日経平均の今週の騰落が−3%以下 → (a)
       ／ +3%以上 → (b) ／ どれでもない・先週と同じ型になる → 週替わりの順
     (a) 大きく下げた週: 損切りの線（25日線割れ／買値から−8%／ストップ高の翌日）で手じまいした場合と持ち続けた場合
     (b) 利益が乗った株: 高値から−10%で手じまいするルールと、決算まで持つルール
     (c) 温度計が「守り」に変わった日に全部手じまいした場合
     どれも **日経平均（指数）で機械的に数えた** 統計（個別銘柄は使わない。(a) のストップ高だけは
     既存の答え合わせ docs/stophigh_hist の全銘柄の集計で、銘柄名は出さない）
  2. 掲示板から2問: board の #質問箱（kr_posts の code=0000 か、本文が「#質問箱」で始まるもの）から、
     非表示でない・通報0件・NG 語なし・URL なし・投資助言を求めていない問いを新しい順に2つ。
     ゲスト投稿（ニックネームだけ）も登録した人の投稿も区別しない（投稿者の列を見ない）。
     足りなければ「よく検索される問い」（qa_fallback.json）から週替わりで埋める
  routine は外へ出られないので、掲示板の読み出し（Supabase）も統計の計算も、全部 prep で行う。
  ingest は prep の PROMPT.txt に埋めた「qa_ctx」の行（選んだ問いと回数）を使う（掲示板が prep の後に増えても同じ問いで検査する）

使い方（手元の確認）:
  python -X utf8 qa_teiban.py              … 3つの型の統計を表示
  python -X utf8 qa_teiban.py --verify     … (a) の25日線割れを素のループで数え直す／(c) の温度計を radar_history.json と突き合わせる
"""
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
FALLBACK_FILE = HERE / "qa_fallback.json"
VOL2_FROM = date(2026, 11, 15)      # 定番＋掲示板の形の初回
TYPES = ("a", "b", "c")
TYPE_Q = {   # 問い（「いつ手じまいするか」の言い方。「売れ」「買え」は書かない）
    "a": "大きく下げた週、どの線で手じまいするか？",
    "b": "利益が乗った株は、いつ手じまいするか？",
    "c": "温度計が守りに変わった日に、手じまいするか？",
}
LOW_N = 30
H = 20                                # その後20営業日
SITE = "https://kaburadar.jp"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}


def _closes(ticker, start):
    import qa_material
    return qa_material.closes(ticker, start)


def _summ(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0, "med": None, "up": None}
    return {"n": len(v), "med": round(statistics.median(v), 2),
            "up": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)}


def _share(flags):
    f = [x for x in flags if x is not None]
    return round(100.0 * sum(1 for x in f if x) / len(f), 1) if f else None


def _pct(a, b):
    return (a / b - 1) * 100


def _fmt(s):
    if not s or not s["n"]:
        return "該当なし"
    return f"{s['n']}回・中央値{s['med']:+.2f}%・プラスだった割合{s['up']:.1f}%"


def _low(n):
    return "（回数が少ない）" if n and n < LOW_N else ""


def week_ends(idx):
    """週（月〜金）の最後の営業日の位置"""
    out = []
    for i in range(len(idx)):
        if i + 1 == len(idx) or idx[i + 1].isocalendar()[:2] != idx[i].isocalendar()[:2]:
            out.append(i)
    return out


# ------------------------------------------------------------------ (a) 大きく下げた週の損切りの線

WEEK_DROP = -5.0      # 日経平均の週の騰落（前の週末の終値→その週末の終値）
CUT = -8.0            # 買値から−8%
MA = 25
GAP = 20              # 前の回から20営業日以内の週は数えない（最初の週だけ）


def stat_exit_a(c=None):
    """大きく下げた週（日経平均が週で−5%以下）に、前の週末の終値で持っていたとして、
    ①25日線割れ（その週のうちに終値が25日移動平均を下回った最初の日）②買値から−8%（その週〜週末から20営業日のうちに
    終値が前の週末の終値の92%以下になった最初の日）で手じまいした場合と、持ち続けた場合の、手じまいの日から20営業日の違い"""
    c = c if c is not None else _closes("^N225", "1965-01-01")
    v = [float(x) for x in c.values]
    ma = c.rolling(MA).mean()
    we = week_ends(c.index)
    ev, last = [], -10**9
    for p, i in zip(we, we[1:]):
        wr = _pct(v[i], v[p])
        if wr > WEEK_DROP or i - last <= GAP:
            continue
        last = i
        base = v[p]
        row = {"d": c.index[i].strftime("%Y-%m-%d"), "wr": round(wr, 2), "lines": {}}
        k25 = next((k for k in range(p + 1, i + 1) if ma.iloc[k] == ma.iloc[k] and v[k] < float(ma.iloc[k])), None)
        k8 = next((k for k in range(p + 1, min(i + H, len(v) - 1) + 1) if _pct(v[k], base) <= CUT), None)
        for name, k in (("ma25", k25), ("cut8", k8)):
            if k is None:
                row["lines"][name] = None
                continue
            fwd = _pct(v[k + H], v[k]) if k + H < len(v) else None
            row["lines"][name] = {"d": c.index[k].strftime("%Y-%m-%d"), "exit": round(_pct(v[k], base), 2),
                                  "hold": round(_pct(v[k + H], base), 2) if fwd is not None else None,
                                  "fwd": round(fwd, 2) if fwd is not None else None}
        ev.append(row)
    out = {"id": "exit_a", "events": ev, "cond": {}, "line": {}}
    for name in ("ma25", "cut8"):
        hit = [e["lines"][name] for e in ev if e["lines"].get(name)]
        done = [h for h in hit if h["fwd"] is not None]
        out["line"][name] = {
            "hit": len(hit), "n": len(done),
            "exit": _summ([h["exit"] for h in done]), "hold": _summ([h["hold"] for h in done]),
            "fwd": _summ([h["fwd"] for h in done]),
            "exit_better": _share([h["fwd"] < 0 for h in done])}
        out["cond"][name] = {"n": len(done)}
    out["n"] = len(ev)
    out["period"] = f"{c.index[0]:%Y-%m-%d}〜{c.index[-1]:%Y-%m-%d}"
    out["last"] = ev[-1]["d"] if ev else None
    sh = stophigh_exit()
    if sh:
        out["stophigh"] = sh
        out["cond"]["stophigh"] = {"n": sh["n"]}
    out["src"] = f"日経平均の日足（{out['period']}）" + ("・ストップ高の答え合わせ（全銘柄の集計）" if sh else "")
    return out


def stophigh_exit(hist_dir=None):
    """既存の答え合わせ（docs/stophigh_hist）: ストップ高で引けた株を、翌日の寄り付きで手じまいした場合と
    5営業日持った場合（どちらもストップ高の日の終値から）。銘柄名は使わない・集計だけ"""
    d = Path(hist_dir or HERE / "docs" / "stophigh_hist")
    if not d.exists():
        return None
    ev = {}
    rows = []
    for f in sorted(d.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(ln))
            except Exception:
                continue
    for r in rows:   # stophigh.py の読み方と同じ（値幅拡大の行が優先・void で消す）
        k = (r.get("d"), r.get("c"))
        if r.get("t") == "ev":
            if k not in ev or r.get("kakudai"):
                ev[k] = dict(r)
        elif r.get("t") == "void":
            ev.pop(k, None)
    for r in rows:
        k = (r.get("d"), r.get("c"))
        if r.get("t") == "fu" and k in ev:
            ev[k]["fu"] = {x: r.get(x) for x in ("n1o", "n1c", "n5c", "skip") if x in r}
    pairs = [(e["fu"]["n1o"], e["fu"]["n5c"], e["d"]) for e in ev.values()
             if e.get("k") == "close_high" and isinstance(e.get("fu"), dict)
             and e["fu"].get("n1o") is not None and e["fu"].get("n5c") is not None]
    if not pairs:
        return None
    ds = sorted(p[2] for p in pairs)
    return {"n": len(pairs), "exit": _summ([p[0] for p in pairs]), "hold": _summ([p[1] for p in pairs]),
            "exit_better": _share([p[0] > p[1] for p in pairs]), "period": f"{ds[0]}〜{ds[-1]}"}


def lines_exit_a(s):
    L = [f"条件: 日経平均が1週間（前の週末の終値→その週末の終値）で{WEEK_DROP:.0f}%以下だった週（{s['period']}・"
         f"前の回から{GAP}営業日以内の週は数えない）＝{s['n']}回。いちばん新しいのは{s['last']}の週",
         "  前の週末の終値で持っていたとして、損切りの線で手じまいした場合と持ち続けた場合（手じまいの日から"
         f"{H}営業日後）を比べた。どれも日経平均（指数）で数えた結果で、個別の株は値動きの幅が違う"]
    names = {"ma25": f"{MA}日線割れ（その週のうちに終値が{MA}日移動平均を下回った最初の日に手じまい）",
             "cut8": f"買値から{CUT:.0f}%（前の週末の終値から{CUT:.0f}%以下になった最初の日に手じまい。その週〜{H}営業日のうち）"}
    for k, nm in names.items():
        x = s["line"][k]
        L.append(f"  線{'①' if k == 'ma25' else '②'} {nm}: 線に届いた{x['hit']}回（その後{H}営業日が分かる{x['n']}回）{_low(x['n'])}")
        L.append(f"    手じまいした値段（買値から）: {_fmt(x['exit'])}")
        L.append(f"    持ち続けた場合（手じまいの日から{H}営業日後・買値から）: {_fmt(x['hold'])}")
        L.append(f"    手じまいの日から{H}営業日の日経平均: {_fmt(x['fwd'])} → 手じまいの方が良かった割合 {x['exit_better']}%")
    sh = s.get("stophigh")
    if sh:
        L.append(f"  線③ ストップ高の翌日（ストップ高で引けた株を、翌日の寄り付きで手じまい・{sh['period']}・全銘柄の集計・"
                 f"{sh['n']}回。このデータは5営業日まで）")
        L.append(f"    翌日の寄り付きで手じまい（ストップ高の日の終値から）: {_fmt(sh['exit'])}")
        L.append(f"    5営業日持った場合（同じ基準）: {_fmt(sh['hold'])} → 翌日の寄り付きの方が良かった割合 {sh['exit_better']}%")
    return L


# ------------------------------------------------------------------ (b) 利益が乗った株: 高値から−10% と 決算まで

GAIN = 10.0           # 60営業日前の終値から+10%以上＝利益が乗った
GAIN_DAYS = 60
TRAIL = -10.0         # 持ってからの高値（終値）から−10%
KESSAN_MONTHS = (2, 5, 8, 11)
KESSAN_DAY = 15       # 3月期決算の会社の発表が集まる時期の目安（各月の15日。休場なら次の営業日）


def next_kessan(d):
    """d より後の、最初の「決算の集中日の目安」（2・5・8・11月の15日）"""
    y, cands = d.year, []
    for yy in (y, y + 1):
        for m in KESSAN_MONTHS:
            x = date(yy, m, KESSAN_DAY)
            if x > d:
                cands.append(x)
    return min(cands)


def stat_exit_b(c=None):
    """週末に日経平均が60営業日前から+10%以上（利益が乗った）なら、その終値で持ったとする。
    ルールA: 持ってからの高値（終値）から−10%に届いたらその日に手じまい（届かなければ決算の目安の日まで）
    ルールB: 次の決算の集中日の目安（2・5・8・11月の15日。休場なら次の営業日）まで持つ
    前の回の持ち期間が終わるまで、次の回は数えない"""
    c = c if c is not None else _closes("^N225", "1965-01-01")
    v = [float(x) for x in c.values]
    idx = c.index
    we = week_ends(idx)
    ev, busy = [], -1
    for i in we:
        if i <= busy or i < GAIN_DAYS or _pct(v[i], v[i - GAIN_DAYS]) < GAIN:
            continue
        kd = next_kessan(idx[i].date())
        e = int(idx.searchsorted(_ts(kd), side="left"))
        if e >= len(v):
            break
        peak, stop = v[i], None
        for k in range(i + 1, e + 1):
            peak = max(peak, v[k])
            if _pct(v[k], peak) <= TRAIL:
                stop = k
                break
        a_end = stop if stop is not None else e
        ev.append({"d": idx[i].strftime("%Y-%m-%d"), "kessan": idx[e].strftime("%Y-%m-%d"),
                   "stop": idx[stop].strftime("%Y-%m-%d") if stop is not None else None,
                   "a": round(_pct(v[a_end], v[i]), 2), "b": round(_pct(v[e], v[i]), 2), "days": e - i})
        busy = e
    hit = [x for x in ev if x["stop"]]
    out = {"id": "exit_b", "events": ev, "n": len(ev), "hit": len(hit),
           "a": _summ([x["a"] for x in ev]), "b": _summ([x["b"] for x in ev]),
           "hit_a": _summ([x["a"] for x in hit]), "hit_b": _summ([x["b"] for x in hit]),
           "a_better": _share([x["a"] > x["b"] for x in hit]),
           "days": int(statistics.median([x["days"] for x in ev])) if ev else None}
    out["cond"] = {"all": {"n": len(ev)}, "hit": {"n": len(hit)}}
    out["period"] = f"{idx[0]:%Y-%m-%d}〜{idx[-1]:%Y-%m-%d}"
    out["last"] = ev[-1]["d"] if ev else None
    out["src"] = f"日経平均の日足（{out['period']}）・決算の集中日の目安（2・5・8・11月の15日）"
    return out


def lines_exit_b(s):
    return [
        f"条件: 週末の日経平均が{GAIN_DAYS}営業日前の終値から+{GAIN:.0f}%以上（利益が乗った）の週に、その終値で持っていたとする"
        f"（{s['period']}・前の回の持ち期間が終わるまで次の回は数えない）＝{s['n']}回{_low(s['n'])}。いちばん新しいのは{s['last']}",
        f"  ルールA: 持ってからの高値（終値）から{TRAIL:.0f}%に届いた日に手じまい（届かなければ決算の目安の日まで持つ）",
        f"  ルールB: 次の決算の集中日の目安（2・5・8・11月の{KESSAN_DAY}日。休場なら次の営業日）まで持つ。持った期間の中央値は{s['days']}営業日",
        f"  全部の回: ルールA {_fmt(s['a'])} ／ ルールB {_fmt(s['b'])}",
        f"  高値から{TRAIL:.0f}%に届いた回（＝2つのルールの結果が分かれた回）: {s['hit']}回{_low(s['hit'])}。"
        f"ルールA {_fmt(s['hit_a'])} ／ ルールB {_fmt(s['hit_b'])} → ルールAの方が良かった割合 {s['a_better']}%",
        "  どれも日経平均（指数）で数えた結果で、個別の株は値動きの幅も決算の日も違う",
    ]


# ------------------------------------------------------------------ (c) 温度計が「守り」に変わった日

DEFENSE = ("BEARISH", "PANIC")    # 温度計の 弱気・パニック＝守りの側


def regime_series(n225=None, spx=None, vix=None):
    """温度計（地合い判定）の今のルール（2026-10-24〜・daily_scanner_v2_8_0.py と同じ）を、過去の指数で日ごとに計算する。
    PANIC: VIX>30 かつ 日経の22営業日前比<−10% ／ BEARISH: 日経<200日線 か VIX>25 ／
    BULLISH: 日経>200日線・>50日線・S&P500>200日線・VIX<20・日経の22営業日前比>0 ／ それ以外 NEUTRAL"""
    import pandas as pd
    n225 = n225 if n225 is not None else _closes("^N225", "1989-01-01")
    spx = spx if spx is not None else _closes("^GSPC", "1989-01-01")
    vix = vix if vix is not None else _closes("^VIX", "1990-01-01")
    ma200, ma50 = n225.rolling(200).mean(), n225.rolling(50).mean()
    chg = (n225 / n225.shift(21) - 1) * 100
    s200 = (spx > spx.rolling(200).mean()).where(spx.rolling(200).mean().notna())
    s_al = s200.reindex(n225.index.union(s200.index)).ffill().reindex(n225.index)
    v_al = vix.reindex(n225.index.union(vix.index)).ffill().reindex(n225.index)
    out = {}
    for d in n225.index:
        if pd.isna(ma200[d]) or pd.isna(v_al[d]) or pd.isna(s_al[d]) or d < vix.index[0]:
            continue
        vx, ch = float(v_al[d]), float(chg[d]) if not pd.isna(chg[d]) else 0.0
        cl = float(n225[d])
        if vx > 30 and ch < -10:
            r = "PANIC"
        elif cl <= float(ma200[d]) or vx > 25:
            r = "BEARISH"
        elif cl > float(ma50[d]) and bool(s_al[d]) and vx < 20 and ch > 0:
            r = "BULLISH"
        else:
            r = "NEUTRAL"
        out[d] = r
    return out


def stat_exit_c(n225=None, reg=None):
    """温度計が守り（弱気・パニック）に変わった日（前の営業日は守りでない）の終値で全部手じまいした場合。
    ①その日から20営業日の日経平均（前の回から20営業日以内の変わり目は数えない）
    ②守りが終わった日（守りでなくなった最初の日）の終値で戻ったとして、その間の日経平均の騰落
    ③期間全体: ずっと持ち続けた場合と、守りの日の翌日を持たなかった場合の年率と最大の下げ"""
    n225 = n225 if n225 is not None else _closes("^N225", "1989-01-01")
    reg = reg if reg is not None else regime_series(n225=n225)
    days = [d for d in n225.index if d in reg]
    v = {d: float(n225[d]) for d in days}
    sw, spells, last = [], [], -10**9
    pos = {d: i for i, d in enumerate(days)}
    i = 1
    while i < len(days):
        d = days[i]
        if reg[d] in DEFENSE and reg[days[i - 1]] not in DEFENSE:
            j = i
            while j < len(days) and reg[days[j]] in DEFENSE:
                j += 1
            end = days[j] if j < len(days) else None
            spells.append({"d": d.strftime("%Y-%m-%d"), "len": j - i,
                           "end": end.strftime("%Y-%m-%d") if end is not None else None,
                           "chg": round(_pct(v[end], v[d]), 2) if end is not None else None})
            if i - last > GAP:
                fwd = _pct(v[days[i + H]], v[d]) if i + H < len(days) else None
                sw.append({"d": d.strftime("%Y-%m-%d"), "fwd": round(fwd, 2) if fwd is not None else None})
                last = i
            i = j
            continue
        i += 1
    # 期間全体（その日の判定で、翌日の値動きを持つか決める）
    import math
    lh, lr, eq_h, eq_r, pk_h, pk_r, dd_h, dd_r = 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0
    for a, b in zip(days, days[1:]):
        g = v[b] / v[a]
        lh += math.log(g)
        eq_h *= g
        if reg[a] not in DEFENSE:
            lr += math.log(g)
            eq_r *= g
        pk_h, pk_r = max(pk_h, eq_h), max(pk_r, eq_r)
        dd_h, dd_r = min(dd_h, eq_h / pk_h - 1), min(dd_r, eq_r / pk_r - 1)
    yrs = (days[-1] - days[0]).days / 365.25 if days else 0
    done = [x for x in sw if x["fwd"] is not None]
    ended = [x for x in spells if x["chg"] is not None]
    out = {"id": "exit_c", "n": len(sw), "switches": len(spells),
           "fwd": _summ([x["fwd"] for x in done]), "exit_better": _share([x["fwd"] < 0 for x in done]),
           "spell": _summ([x["chg"] for x in ended]),
           "spell_exit_better": _share([x["chg"] < 0 for x in ended]) if ended else None,   # 守りの間に下げていれば、戻る値段が安い＝手じまいの方が良かった
           "spell_len": int(statistics.median([x["len"] for x in ended])) if ended else None,
           "cagr_hold": round((math.exp(lh / yrs) - 1) * 100, 2) if yrs else None,
           "cagr_rule": round((math.exp(lr / yrs) - 1) * 100, 2) if yrs else None,
           "dd_hold": round(dd_h * 100, 1), "dd_rule": round(dd_r * 100, 1),
           "def_share": round(100.0 * sum(1 for d in days if reg[d] in DEFENSE) / len(days), 1) if days else None,
           "events": sw, "spells_last": spells[-5:]}
    out["cond"] = {"fwd": {"n": len(done)}}
    out["period"] = f"{days[0]:%Y-%m-%d}〜{days[-1]:%Y-%m-%d}" if days else ""
    out["last"] = spells[-1]["d"] if spells else None
    out["now"] = reg[days[-1]] if days else None
    out["src"] = f"日経平均・S&P500・VIXの日足（{out['period']}）で温度計の今のルールを日ごとに計算"
    return out


def lines_exit_c(s):
    jp = {"BULLISH": "強気", "NEUTRAL": "中立", "BEARISH": "弱気", "PANIC": "パニック"}
    return [
        "条件: 温度計（地合い判定）の今のルール（日経平均の200日線・50日線・1か月の騰落、S&P500の200日線、VIX）を、"
        f"過去の指数で日ごとに計算し（{s['period']}）、「守り」（弱気かパニック）に変わった日（前の営業日は守りでない）の"
        f"終値で全部手じまいしたとする。変わった日は{s['switches']}回。いちばん新しいのは{s['last']}（今の判定は{jp.get(s['now'], s['now'])}）",
        f"  ① 変わった日から{H}営業日の日経平均（前の回から{GAP}営業日以内の変わり目は数えない・{s['n']}回）: "
        f"{_fmt(s['fwd'])} → 手じまいの方が良かった割合 {s['exit_better']}%",
        f"  ② 守りが終わった日の終値で戻ったとして、守りの間の日経平均（{s['spell']['n']}回・守りの長さの中央値{s['spell_len']}営業日）: "
        f"{_fmt(s['spell'])} → 手じまいの方が良かった割合 {s['spell_exit_better']}%",
        f"  ③ 期間全体: ずっと持ち続けた場合 年率{s['cagr_hold']:+.2f}%・最大の下げ{s['dd_hold']:.1f}% ／ "
        f"守りの日の翌日を持たなかった場合 年率{s['cagr_rule']:+.2f}%・最大の下げ{s['dd_rule']:.1f}%（守りの日は全体の{s['def_share']}%）",
        "  どれも日経平均（指数）で数えた結果で、配当・手数料・税は入れていない。個別の株は値動きの幅が違う",
    ]


EXIT_STATS = {"exit_a": (stat_exit_a, lines_exit_a), "exit_b": (stat_exit_b, lines_exit_b),
              "exit_c": (stat_exit_c, lines_exit_c)}


def _ts(d):
    import pandas as pd
    return pd.Timestamp(d)


# ------------------------------------------------------------------ 型の選び方

def week_shape(mon, fri, n225=None, radar_items=None):
    """今週の相場の形: 日経平均の週の騰落（前の週末の終値→金曜の終値）と、温度計が今週「守り」に変わったか"""
    wk = None
    try:
        c = n225 if n225 is not None else _closes("^N225", (mon - timedelta(days=20)).isoformat())
        b = int(c.index.searchsorted(_ts(mon), side="left")) - 1
        e = int(c.index.searchsorted(_ts(fri), side="right")) - 1
        if b >= 0 and e > b:
            wk = round(_pct(float(c.iloc[e]), float(c.iloc[b])), 2)
    except Exception as ex:
        print(f"  定番の型: 日経の週の騰落を取れず: {ex}", file=sys.stderr)
    if radar_items is None:
        try:
            radar_items = json.loads((HERE / "docs" / "radar_history.json").read_text(encoding="utf-8"))["items"]
        except Exception:
            radar_items = []
    before = [x for x in radar_items if x.get("d", "") < mon.isoformat()]
    inweek = [x for x in radar_items if mon.isoformat() <= x.get("d", "") <= fri.isoformat()]
    switched = bool(before and before[-1].get("regime") not in DEFENSE
                    and any(x.get("regime") in DEFENSE for x in inweek))
    return wk, switched


def choose_type(sunday, wk, switched, prev):
    """戻り値: (型, 選んだ理由)。prev＝先週の定番の型（無ければ None）"""
    k = ((sunday - VOL2_FROM).days // 7) % 3
    rot = TYPES[k]
    if sunday == VOL2_FROM:
        return "a", "初回は(a)"
    shape = None
    if switched:
        shape, why = "c", "温度計が今週「守り」に変わった"
    elif wk is not None and wk <= -3:
        shape, why = "a", f"日経平均が今週{wk:+.2f}%"
    elif wk is not None and wk >= 3:
        shape, why = "b", f"日経平均が今週{wk:+.2f}%"
    if shape and shape != prev:
        return shape, why
    if rot == prev:
        rot = TYPES[(k + 1) % 3]
    return rot, "週替わり"


# ------------------------------------------------------------------ 掲示板の #質問箱

QA_CODE = "0000"
QA_TAG = "#質問箱"
Q_LEN = (8, 140)
URL_RE = re.compile(r"https?://|www\.|\.(?:com|jp|net|io)\b", re.I)
# 投資助言を求めている問い（この銘柄を買うか・いつ売るか・何を買えばいいか など）は選ばない
ADVICE_RE = re.compile(
    r"買うべき|売るべき|買い時|売り時|買っていい|売っていい|買っても|売っても|買った(?:ほう|方)が|売った(?:ほう|方)が"
    r"|買えば|売れば|何を買|どれを買|どの銘柄|おすすめ|オススメ|お勧め|推奨|儲か|もうか|目標株価|ナンピン|損切りすべき"
    r"|持ち続けるべき|手放すべき|利確すべき|上がりますか|下がりますか|上がると思|下がると思|上がるでしょう|下がるでしょう"
    r"|教えてください.*銘柄|銘柄.*教えて|爆上げ|テンバガー候補|仕手")
CODE_RE = re.compile(r"(?<![0-9０-９])[1-9１-９][0-9０-９]{2}[0-9A-ZＡ-Ｚ０-９](?![0-9０-９年月日%％円万億兆件回人ドル倍枚株週])")
NG_FALLBACK = ["死ね", "殺す", "絶対上がる", "必ず儲かる", "イナゴ", "爆上げ確定"]


def ng_words():
    try:
        req = urllib.request.Request(f"{SITE}/data/ng_words.txt", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            t = r.read().decode("utf-8", "replace")
        w = [s.strip() for s in t.splitlines() if s.strip() and not s.strip().startswith("#")]
        return w or NG_FALLBACK
    except Exception as e:
        print(f"  掲示板: NG 語の一覧を取れず（既定の6語で見る）: {e}", file=sys.stderr)
        return NG_FALLBACK


def clean_post(body):
    t = re.sub(r"\s+", " ", str(body or "").replace(QA_TAG, "")).strip()
    return t


def post_ok(text, ng):
    """掲示板の問いを選んでよいか。戻り値: 落とす理由（よければ None）"""
    if not (Q_LEN[0] <= len(text) <= Q_LEN[1]):
        return f"長さ {len(text)}字"
    if URL_RE.search(text):
        return "URL"
    for w in ng:
        if w and w in text:
            return "NG 語"
    m = ADVICE_RE.search(text)
    if m:
        return f"投資助言を求める「{m.group(0)}」"
    if CODE_RE.search(text):
        return "証券コード"
    try:
        import deep_material as dm
        if dm.names_ok(text):
            return "人名"
    except Exception:
        pass
    return None


def fetch_board_posts(limit=60):
    """Supabase の kr_posts から #質問箱 の投稿（非表示でない・通報0件）を新しい順に。
    投稿者の列（user_id / guest_id）は読まない＝ゲストも登録した人も同じに扱う。鍵が無ければ []"""
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        print("  掲示板: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が無いので読まない（よく検索される問いで埋める）", file=sys.stderr)
        return None
    orq = urllib.parse.quote(f"(code.eq.{QA_CODE},body.like.{QA_TAG}*)", safe="(),.*")
    q = (f"select=id,code,body,created_at,report_count&hidden=eq.false&or={orq}"
         f"&order=created_at.desc&limit={limit}")
    try:
        req = urllib.request.Request(f"{base}/rest/v1/kr_posts?{q}",
                                     headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"  掲示板: 読めず（よく検索される問いで埋める）: {e}", file=sys.stderr)
        return None


def board_questions(posts, ng, sunday, used=(), want=2):
    """投稿から問いを新しい順に want 個。戻り値: (選んだ問い, 落とした数の内訳)"""
    out, drop, seen = [], {}, set(used)
    for p in posts or []:
        if (p.get("report_count") or 0) > 0:
            drop["通報あり"] = drop.get("通報あり", 0) + 1
            continue
        t = clean_post(p.get("body"))
        why = post_ok(t, ng)
        if not why and t in seen:
            why = "同じ問い"
        if why:
            k = why.split("「")[0]
            drop[k] = drop.get(k, 0) + 1
            continue
        seen.add(t)
        d = str(p.get("created_at") or "")[:10]
        out.append({"id": f"board{p.get('id')}", "q": t, "kind": "board", "post_id": p.get("id"), "posted": d,
                    "src": f"掲示板の #質問箱（{d[5:7]}/{d[8:10]}の投稿）" if len(d) == 10 else "掲示板の #質問箱"})
        if len(out) >= want:
            break
    return out, drop


def fallback_questions(sunday, want, have=()):
    """よく検索される問い（qa_fallback.json）から週替わりで want 個"""
    try:
        items = json.loads(FALLBACK_FILE.read_text(encoding="utf-8"))["items"]
    except Exception as e:
        print(f"  よく検索される問いを読めず: {e}", file=sys.stderr)
        return []
    if not items or want <= 0:
        return []
    k = max(0, (sunday - VOL2_FROM).days // 7) * 2
    out, have = [], set(have)
    for j in range(len(items)):
        it = items[(k + j) % len(items)]
        if it["q"] in have:
            continue
        out.append({"id": it["id"], "q": it["q"], "kind": "search", "src": it.get("src", "よく検索される問い")})
        if len(out) >= want:
            break
    return out


def prev_teiban(path=None):
    """先週の日曜版の定番の型（ai_weekly.json の weekend.qa の id が exit_◯）"""
    try:
        o = json.loads(Path(path or HERE / "docs" / "ai_weekly.json").read_text(encoding="utf-8"))
        for it in ((o.get("weekend") or {}).get("qa") or {}).get("items") or []:
            m = re.match(r"exit_([abc])$", str(it.get("id")))
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def weekly_questions(sunday, mon, fri, posts=None, ng=None, shape=None, prev="?"):
    """その日曜の3問（定番1＋掲示板2。足りない分はよく検索される問い）。戻り値: (問いのリスト, 選び方のメモ)"""
    wk, switched = shape if shape is not None else week_shape(mon, fri)
    prev = prev_teiban() if prev == "?" else prev
    t, why = choose_type(sunday, wk, switched, prev)
    qs = [{"id": f"exit_{t}", "q": TYPE_Q[t], "stat": f"exit_{t}", "kind": "teiban"}]
    if posts is None:
        posts = fetch_board_posts()
    bq, drop = board_questions(posts, ng if ng is not None else (ng_words() if posts else NG_FALLBACK), sunday)
    fq = fallback_questions(sunday, 2 - len(bq), have=[x["q"] for x in bq])
    note = {"type": t, "why": why, "week_n225": wk, "switched": switched, "prev": prev,
            "board_posts": len(posts or []), "board_pick": len(bq), "board_drop": drop, "search_pick": len(fq)}
    return qs + bq + fq, note


# ------------------------------------------------------------------ 別の数え方（確認用）

def verify(n225=None):
    """(a) の25日線割れを、pandas の rolling を使わない素のループで数え直す。(c) の温度計を radar_history.json と突き合わせる"""
    c = n225 if n225 is not None else _closes("^N225", "1965-01-01")
    s = stat_exit_a(c)
    v = [float(x) for x in c.values]
    wk = [x.isocalendar()[:2] for x in c.index]
    ends = [i for i in range(len(v)) if i + 1 == len(v) or wk[i + 1] != wk[i]]
    cnt, fw, last = 0, [], -10**9
    for a, b in zip(ends, ends[1:]):
        if (v[b] / v[a] - 1) * 100 > WEEK_DROP or b - last <= GAP:
            continue
        last = b
        cnt += 1
        for k in range(a + 1, b + 1):
            if k >= MA - 1 and v[k] < sum(v[k - MA + 1:k + 1]) / MA:
                if k + H < len(v):
                    fw.append((v[k + H] / v[k] - 1) * 100)
                break
    ok_a = cnt == s["n"] and _summ(fw) == s["line"]["ma25"]["fwd"]
    print(f"(a) 素のループ: {cnt}回・25日線割れの後20営業日 {_summ(fw)} / 本体: {s['n']}回・{s['line']['ma25']['fwd']} → "
          f"{'一致' if ok_a else '不一致'}")
    reg = regime_series()
    try:
        items = json.loads((HERE / "docs" / "radar_history.json").read_text(encoding="utf-8"))["items"]
    except Exception:
        items = []
    m = t = 0
    diff = []
    for x in items:
        d = _ts(x["d"])
        if d in reg:
            t += 1
            if (reg[d] in DEFENSE) == (x["regime"] in DEFENSE):
                m += 1
            else:
                diff.append(f"{x['d']} 公開{x['regime']}/計算{reg[d]}")
    print(f"(c) 温度計: radar_history.json と同じ日 {t}日のうち「守りか否か」が一致 {m}日" + (f"（違い: {', '.join(diff[:8])}）" if diff else ""))
    return ok_a


if __name__ == "__main__":
    if "--verify" in sys.argv:
        sys.exit(0 if verify() else 1)
    for k, (f, ln) in EXIT_STATS.items():
        s = f()
        print(f"## {k}")
        print("\n".join(ln(s)))
