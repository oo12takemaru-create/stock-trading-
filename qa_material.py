# -*- coding: utf-8 -*-
"""日曜版（来週の地図）の「アーサーの質問箱」の材料と検査（2026-11-06）

■ 何をするか
  日曜版の JSON（ai_weekly.json の weekend）に `qa` を足す。週3問まで。
  書き手は日曜の routine。答えの型（統括 11/6）は
    1. 今起きていることを一言で → 2. なぜそう言えるか（今週のニュースの見出し＋市場の数字を2〜3つ、因果を1本の線に）
    → 3. 翻訳（日常の言葉で一言） → 4. 2つの道を条件つきで（「◯◯なら〜しやすい／◯◯なら〜しやすい」。
    過去の統計は、どちらの道が重いかの根拠として1つだけ） → 5. 次に見る数字
  の5文以内・数字6つまで・260〜400字。片方の道に言い切らない。
  各道に機械で判定できる条件（来週の金曜終値→翌週金曜終値の騰落）を持たせ、翌週の土曜版で答え合わせする（judge_prev）。

■ 材料（prep が作る。routine は外へ出られない）
  - 今週のニュースの見出し: AI朝刊の prep が作った ai-prompt/<日付>/inputs.txt の「直近ニュース見出し」（媒体と日付つき・見出しだけ）
  - 市場の数字: world.json の 1日・5日・20日・年初来（日経・S&P500・SOX・米10年・ドル円・VIX など）
  - 過去の統計: 問いごとの stat（STATS）を yf_latest の終値から計算。条件なしの全期間の同じ値も並べる
  - 問い: qa_questions.json（統括が週ごとに書く差し込み口。week がその日曜でなければ質問箱は出さない）

■ 検査（ingest。落ちた問いだけ外す。日曜版の本体と他の問いは出す）
  - 数字は入力にあるものだけ（deep_material.numbers_ok）
  - 実在の評論家・投資家の名前（ハッシュ）を含まない。「〜氏」「〜長官」などの人を指す書き方は、
    その名前が入力のニュースの見出しにあるときだけ可（政策当局の人物など）
  - 「狙い」「思惑」「意図」の語があれば落とす（人物の意図の推測）
  - 片方に言い切る語（〜だろう・でしょう・はずだ・文末の「上がる」「下がる」）・買い/売り・必ず・確実・推奨・おすすめ
  - 統計の回数が30未満の問いは「回数が少ない」を必ず入れる
  - 2つの道: paths は2つ・条件は決まった指標と幅だけ・向きが逆。本文に「なら」が2つ以上

使い方（手元の確認）:
  python -X utf8 qa_material.py            … 今週の材料を表示
  python -X utf8 qa_material.py --verify   … 1問目（SOX）を別の数え方（素のループ）で数え直して突き合わせる
  python -X utf8 qa_material.py --build-payroll-dates  … qa_payroll_dates.json を作り直す（ALFRED＋BLS）
"""
import json
import re
import statistics
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
QUESTIONS_FILE = HERE / "qa_questions.json"
PAYROLL_FILE = HERE / "qa_payroll_dates.json"
RAW = "https://raw.githubusercontent.com/oo12takemaru-create/stock-trading-"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
VOL_FROM = "2026-11-08"     # Vol.1 の日曜
LOW_N = 30                  # これ未満の回数の統計を使う問いは「回数が少ない」が必須

# ------------------------------------------------------------------ 価格（yf_latest で最新の終値を埋める）

_CACHE = {}


def closes(ticker, start):
    key = (ticker, start)
    if key not in _CACHE:
        from yf_latest import history_closes
        c = history_closes(ticker, start=start, verbose=False)
        _CACHE[key] = c.dropna() if len(c) else c
    return _CACHE[key]


def _summ(vals):
    """件数・中央値・上がった割合（%）"""
    v = [x for x in vals if x is not None]
    if not v:
        return {"n": 0, "med": None, "up": None}
    return {"n": len(v), "med": round(statistics.median(v), 2),
            "up": round(100.0 * sum(1 for x in v if x > 0) / len(v), 1)}


def _fwd(c, pos, k, base_pos=None):
    """c の pos から k 本先の騰落%（足りなければ None）。base_pos を渡すとそこを基準にする"""
    b = pos if base_pos is None else base_pos
    if pos + k >= len(c) or b < 0:
        return None
    return (float(c.iloc[pos + k]) / float(c.iloc[b]) - 1) * 100


def _pos_le(c, d):
    """d 以前で最後の営業日の位置（無ければ -1）"""
    return int(c.index.searchsorted(d, side="right")) - 1


def _pos_ge(c, d):
    return int(c.index.searchsorted(d, side="left"))


def _ts(d):
    import pandas as pd
    return pd.Timestamp(d)


def _fmt(s, unit="%"):
    if not s or not s["n"]:
        return "該当なし"
    return f"{s['n']}回・中央値{s['med']:+.2f}{unit}・上がった割合{s['up']:.1f}%"


# ------------------------------------------------------------------ 1. 半導体指数が1日で−8%以下の日

SOX_DROP = -8.0
SOX_GAP = 20      # 20営業日以内に続いた日は最初の日だけ
HORIZONS = (5, 20, 60)


def sox_events(sox):
    r = sox.pct_change() * 100
    ev, last = [], -10**9
    for i in range(1, len(sox)):
        # 前に数えた日から20営業日以内の下げは重複として数えない（最初の日だけ）
        if r.iloc[i] <= SOX_DROP and i - last > SOX_GAP:
            ev.append(i)
            last = i
    return ev, r


def stat_sox_drop():
    sox = closes("^SOX", "1994-01-01")
    n225 = closes("^N225", "1994-01-01")
    ev, r = sox_events(sox)
    out = {"id": "sox_drop", "events": [], "cond": {}, "all": {}}
    for i in ev:
        d = sox.index[i]
        row = {"d": d.strftime("%Y-%m-%d"), "r1": round(float(r.iloc[i]), 2)}
        # 日経: 下げた米国の日と同じ日付の東京の終値（米国の下げの前）を基準に、k 東京営業日後
        j = _pos_le(n225, d)
        for k in HORIZONS:
            row[f"sox{k}"] = _fwd(sox, i, k)
            row[f"nk{k}"] = _fwd(n225, j, k) if j >= 0 else None
        out["events"].append(row)
    s0 = sox.index[0]
    nk = n225[n225.index >= s0]
    for k in HORIZONS:
        out["cond"][f"sox{k}"] = _summ([e[f"sox{k}"] for e in out["events"]])
        out["cond"][f"nk{k}"] = _summ([e[f"nk{k}"] for e in out["events"]])
        out["all"][f"sox{k}"] = _summ([_fwd(sox, i, k) for i in range(len(sox))])
        out["all"][f"nk{k}"] = _summ([_fwd(nk, i, k) for i in range(len(nk))])
    out["n"] = len(ev)
    out["period"] = f"{sox.index[0]:%Y-%m-%d}〜{sox.index[-1]:%Y-%m-%d}"
    out["last"] = out["events"][-1]["d"] if ev else None
    out["src"] = f"SOX指数・日経平均の日足（{out['period']}）"
    return out


def lines_sox_drop(s):
    L = [f"条件: SOX指数（米半導体）の1日の騰落が{SOX_DROP:.0f}%以下の日（{s['period']}・"
         f"{SOX_GAP}営業日以内に続いた日は最初の日だけ）＝{s['n']}回。いちばん新しいのは{s['last']}"]
    for k in HORIZONS:
        L.append(f"  {k}営業日後のSOX: この条件 {_fmt(s['cond'][f'sox{k}'])} ／ 条件なしの全期間 {_fmt(s['all'][f'sox{k}'])}")
    for k in HORIZONS:
        L.append(f"  {k}営業日後の日経平均: この条件 {_fmt(s['cond'][f'nk{k}'])} ／ 条件なしの全期間 {_fmt(s['all'][f'nk{k}'])}")
    L.append("  （日経平均は、米国の下げの日と同じ日付の東京の終値から数えた東京の営業日）")
    return L


# ------------------------------------------------------------------ 2. 米国株が大きく下げた週の雇用統計の日

PAY_PRE = 5        # 発表前の5営業日
PAY_DROP = -4.0


def payroll_dates():
    try:
        d = json.loads(PAYROLL_FILE.read_text(encoding="utf-8"))
        return [x["d"] for x in d["dates"]]
    except Exception as e:
        print(f"  {PAYROLL_FILE.name} を読めない: {e}", file=sys.stderr)
        return []


def stat_payroll_drop():
    spx = closes("^GSPC", "1950-01-01")
    n225 = closes("^N225", "1960-01-01")
    last = spx.index[-1]
    rows = []
    for ds in payroll_dates():
        d = _ts(ds)
        if d > last:
            continue
        p = _pos_ge(spx, d)            # 発表日（休場＝聖金曜日などは次の営業日）
        if p >= len(spx) or p - 1 - PAY_PRE < 0:
            continue
        pre = p - 1                    # 発表の前の営業日
        row = {"d": ds,
               "pre5": (float(spx.iloc[pre]) / float(spx.iloc[pre - PAY_PRE]) - 1) * 100,
               "day": _fwd(spx, pre, 1),
               "d5": _fwd(spx, p, 5, base_pos=pre)}
        # 日経: 発表日の翌東京営業日（発表は日本時間の夜）。前の東京営業日の終値からの騰落
        q = int(n225.index.searchsorted(d, side="right"))
        row["nk"] = _fwd(n225, q - 1, 1) if 0 < q < len(n225) and n225.index[0] <= d else None
        rows.append(row)
    cond = [r for r in rows if r["pre5"] <= PAY_DROP]
    out = {"id": "payroll_drop", "rows_n": len(rows), "events": [
        {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()} for r in cond]}
    out["cond"] = {k: _summ([r[k] for r in cond]) for k in ("day", "d5", "nk")}
    out["all"] = {k: _summ([r[k] for r in rows]) for k in ("day", "d5", "nk")}
    out["n"] = len(cond)
    out["period"] = f"{rows[0]['d']}〜{rows[-1]['d']}" if rows else ""
    out["src"] = (f"米雇用統計の発表日{len(rows)}回（{out['period']}・BLS の日程と ALFRED の PAYEMS 初出日）・S&P500・日経平均の日足")
    nxt = [x for x in payroll_dates() if _ts(x) > last]
    out["next"] = nxt[0] if nxt else None
    return out


def lines_payroll_drop(s):
    L = [f"条件: 米雇用統計の発表日のうち、発表前の{PAY_PRE}営業日でS&P500が{PAY_DROP:.0f}%以下だった回"
         f"（発表日{s['rows_n']}回・{s['period']}）＝{s['n']}回"]
    if s["events"]:
        L.append("  当てはまった回: " + "、".join(f"{e['d']}（前の5営業日{e['pre5']:+.2f}%）" for e in s["events"][-6:])
                 + (f" ほか{len(s['events']) - 6}回" if len(s["events"]) > 6 else ""))
    L.append(f"  発表日当日のS&P500: この条件 {_fmt(s['cond']['day'])} ／ 全部の発表日 {_fmt(s['all']['day'])}")
    L.append(f"  発表日から5営業日後のS&P500（発表前日の終値から）: この条件 {_fmt(s['cond']['d5'])} ／ 全部の発表日 {_fmt(s['all']['d5'])}")
    L.append(f"  翌東京営業日の日経平均: この条件 {_fmt(s['cond']['nk'])} ／ 全部の発表日 {_fmt(s['all']['nk'])}")
    if s.get("next"):
        L.append(f"  次の米雇用統計の発表日: {s['next']}（BLS の日程）")
    return L


# ------------------------------------------------------------------ 3. 米国の中間選挙のあと

# 1950年以降の米国中間選挙の投票日（連邦法 2 U.S.C. §7 の「11月の第1月曜の翌日の火曜」。
# 各回の日付は米連邦選挙委員会（FEC）の Federal Elections の各年版と一致を確認）
MIDTERMS = ["1950-11-07", "1954-11-02", "1958-11-04", "1962-11-06", "1966-11-08", "1970-11-03",
            "1974-11-05", "1978-11-07", "1982-11-02", "1986-11-04", "1990-11-06", "1994-11-08",
            "1998-11-03", "2002-11-05", "2006-11-07", "2010-11-02", "2014-11-04", "2018-11-06",
            "2022-11-08"]
MIDTERM_NOW = "2026-11-03"


def election_day(y):
    """11月の第1月曜の翌日の火曜"""
    d = date(y, 11, 1)
    while d.weekday() != 0:
        d += timedelta(days=1)
    return d + timedelta(days=1)


def _season(c, d0, y):
    """投票日（休場なら前の営業日）の終値 → 年末の終値／1年後の終値"""
    b = _pos_le(c, _ts(d0))
    if b < 0 or c.index[b].year != y:
        return None, None
    e = _pos_le(c, _ts(f"{y}-12-31"))
    ye = (float(c.iloc[e]) / float(c.iloc[b]) - 1) * 100 if e > b else None
    one = _pos_ge(c, _ts(d0) + timedelta(days=365))
    yr = (float(c.iloc[one]) / float(c.iloc[b]) - 1) * 100 if one < len(c) else None
    return ye, yr


def stat_midterm():
    spx = closes("^GSPC", "1950-01-01")
    n225 = closes("^N225", "1960-01-01")
    assert all(election_day(int(d[:4])).isoformat() == d for d in MIDTERMS)
    out = {"id": "midterm", "cond": {}, "all": {}, "events": []}
    for name, c in (("spx", spx), ("nk", n225)):
        ye, yr, aye, ayr = [], [], [], []
        for d in MIDTERMS:
            a, b = _season(c, d, int(d[:4]))
            ye.append(a)
            yr.append(b)
        for y in range(1950, 2026):
            a, b = _season(c, election_day(y).isoformat(), y)
            aye.append(a)
            ayr.append(b)
        out["cond"][f"{name}_ye"], out["cond"][f"{name}_1y"] = _summ(ye), _summ(yr)
        out["all"][f"{name}_ye"], out["all"][f"{name}_1y"] = _summ(aye), _summ(ayr)
        if name == "spx":
            out["events"] = [{"d": d, "ye": round(a, 2) if a is not None else None,
                              "1y": round(b, 2) if b is not None else None} for d, a, b in zip(MIDTERMS, ye, yr)]
    out["n"] = len(MIDTERMS)
    # 今回（2026-11-03）の投票日から直近まで
    b = _pos_le(spx, _ts(MIDTERM_NOW))
    out["now"] = {"spx": round((float(spx.iloc[-1]) / float(spx.iloc[b]) - 1) * 100, 2),
                  "to": spx.index[-1].strftime("%Y-%m-%d")}
    bn = _pos_le(n225, _ts(MIDTERM_NOW))
    out["now"]["nk"] = round((float(n225.iloc[-1]) / float(n225.iloc[bn]) - 1) * 100, 2)
    out["now"]["nk_to"] = n225.index[-1].strftime("%Y-%m-%d")
    out["src"] = "米国中間選挙の投票日19回（1950〜2022年・2 U.S.C. §7／FEC）・S&P500（1950年〜）・日経平均（1965年〜）の日足"
    return out


def lines_midterm(s):
    c, a = s["cond"], s["all"]
    return [
        f"条件: 1950年以降の米国中間選挙の投票日（{s['n']}回。日経平均は1965年からの{c['nk_ye']['n']}回）。"
        "基準は投票日（休場なら前の営業日）の終値",
        f"  投票日→年末のS&P500: 中間選挙の年 {_fmt(c['spx_ye'])} ／ 全部の年の同じ時期（1950〜2025年） {_fmt(a['spx_ye'])}",
        f"  投票日→1年後のS&P500: 中間選挙の年 {_fmt(c['spx_1y'])} ／ 全部の年の同じ時期 {_fmt(a['spx_1y'])}",
        f"  投票日→年末の日経平均: 中間選挙の年 {_fmt(c['nk_ye'])} ／ 全部の年の同じ時期 {_fmt(a['nk_ye'])}",
        f"  投票日→1年後の日経平均: 中間選挙の年 {_fmt(c['nk_1y'])} ／ 全部の年の同じ時期 {_fmt(a['nk_1y'])}",
        f"  今回の投票日は{MIDTERM_NOW}。そこから直近までS&P500 {s['now']['spx']:+.2f}%（{s['now']['to']}まで）・"
        f"日経平均 {s['now']['nk']:+.2f}%（{s['now']['nk_to']}まで）",
    ]


STATS = {
    "sox_drop": (stat_sox_drop, lines_sox_drop),
    "payroll_drop": (stat_payroll_drop, lines_payroll_drop),
    "midterm": (stat_midterm, lines_midterm),
}


def low_n(s):
    """その問いで使う条件つきの統計の、いちばん少ない回数"""
    ns = [v["n"] for v in (s.get("cond") or {}).values() if v.get("n")]
    return min(ns) if ns else s.get("n", 0)


# ------------------------------------------------------------------ 今週のニュースの見出し

NEWS_HEAD = "## 直近ニュース見出し"
NEWS_MAX = 90


def week_news(mon, fri, fetch=None):
    """月〜金の AI朝刊の入力（ai-prompt/<日付>/inputs.txt）から見出しを集める。
    同じ見出しは最初に出た日の日付で1つだけ。並びは新しい日が先"""
    fetch = fetch or _raw_inputs
    seen, days = set(), []
    d = mon
    while d <= fri:
        day = []
        txt = fetch(d.isoformat())
        if txt and NEWS_HEAD in txt:
            sec = txt.split(NEWS_HEAD, 1)[1].split("\n## ", 1)[0]
            for ln in sec.splitlines():
                m = re.match(r"\[([^\]]+)\]\s*(.+)$", ln.strip())
                if not m or "取得失敗" in m.group(2) or m.group(2).startswith("【動画】"):
                    continue
                t = m.group(2).strip()
                if t in seen:
                    continue
                seen.add(t)
                day.append(f"[{d:%m/%d} {m.group(1)}] {t}")
        days.append(day)
        d += timedelta(days=1)
    out = [x for day in reversed(days) for x in day]
    return out[:NEWS_MAX]


def _raw_inputs(ds):
    try:
        req = urllib.request.Request(f"{RAW}/ai-prompt/{ds}/inputs.txt", headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", "replace")
    except Exception as e:
        print(f"  ai-prompt/{ds} の見出しを取れず: {e}", file=sys.stderr)
        return ""


# ------------------------------------------------------------------ 道の条件（来週の騰落で機械が判定する）

PATH_T = {"N225": ("^N225", "日経平均", "%"), "SOX": ("^SOX", "SOX指数", "%"), "SPX": ("^GSPC", "S&P500", "%"),
          "USDJPY": ("JPY=X", "ドル円", "%"), "US10Y": ("^TNX", "米10年金利", "%pt")}
PATH_V = {"%": (1, 2, 3, 5, 7, 10, 15, 20), "%pt": (0.1, 0.2, 0.3)}


def path_cond_jp(p):
    t = PATH_T.get(p.get("t"))
    if not t:
        return ""
    v = p["v"]
    return f"{t[1]}が来週{v:+g}{t[2]}{'以上' if p['op'] == '>=' else '以下'}"


def path_rule_lines():
    return ["道の条件（来週の金曜終値→翌週の金曜終値の騰落で、翌週の土曜に機械が判定する）に使える指標と幅:",
            "  指標 t: " + " / ".join(f"{k}＝{v[1]}" for k, v in PATH_T.items()),
            "  幅 v（符号つき）: 1, 2, 3, 5, 7, 10, 15, 20%（米10年金利だけ 0.1, 0.2, 0.3%pt）"]


# ------------------------------------------------------------------ 問い（差し込み口）

def questions_for(sunday):
    """qa_questions.json の week がこの日曜なら、その問いを返す（無ければ []＝質問箱は出さない）"""
    try:
        q = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if q.get("week") != sunday:
        return []
    return [x for x in q.get("items") or [] if x.get("id") and x.get("q")][:3]


def build(today, mon, fri, world_lines=None):
    """材料と機械の確定値。戻り値: (プロンプトに足す文, ctx)。問いが無ければ ("", None)"""
    qs = questions_for(today.isoformat())
    if not qs:
        return "", None
    ctx = {"items": {}, "vol": None}
    L = ["# 質問箱の材料（当サイトの集計・ニュースは見出しだけ）"]
    for x in qs:
        st = STATS.get(x.get("stat") or "")
        L.append(f"## 問い {x['id']}: {x['q']}")
        if st:
            try:
                s = st[0]()
                L += st[1](s)
                ctx["items"][x["id"]] = {"q": x["q"], "n": s.get("n"), "low": low_n(s), "src": s.get("src"),
                                        "stat": x.get("stat")}
            except Exception as e:
                print(f"  質問箱 {x['id']} の統計を作れず（この問いは出さない）: {e}", file=sys.stderr)
                L.append("  （統計を作れなかった。この問いは書かない）")
        else:
            ctx["items"][x["id"]] = {"q": x["q"], "n": None, "low": None, "src": x.get("src") or "", "stat": None}
    L.append("## 市場の数字（世界の値動きの地図）")
    L += world_lines or ["  (取得できず)"]
    L.append(f"## 今週（{mon:%m/%d}〜{fri:%m/%d}）のニュースの見出し（AI朝刊の入力・[日付 媒体] 見出し。本文は無い）")
    L += week_news(mon, fri) or ["  (取得できず)"]
    L += path_rule_lines()
    return "\n".join(L), ctx


QA_GUIDE = """# アーサーの質問箱（qa）の書き方
株レーダーのAIキャラクター「アーサー」が、読者の問いに答える節です（見出しに「アーサーの見立て」と出ます）。下の「質問箱の材料」にある問いに1つずつ答えます。
過去の統計を読み上げるだけでなく、**今週のニュースの見出しと市場の数字から、今起きていることを読み解く**のが主役です。

## 1問の形（5文以内・全体260〜400字・数字6つまで）
- 1文目＝今起きていることを一言で（例「半導体の急落は、AIへの期待が金利の上昇に押し戻された動きです。」）
- 2文目＝なぜそう言えるか: 今週のニュースの見出しと市場の数字を2〜3つ、因果の1本の線でつなぐ（見出しは「◯/◯の報道（媒体）」のように出所が分かる形で）
- 3文目＝翻訳: 専門用語や数字の意味を日常の言葉で一言（数字は使わない）
- 4文目＝2つの道を条件つきで: 「◯◯なら〜しやすく、◯◯なら〜しやすい」。片方に言い切らない。過去の統計（材料の「この条件」と「条件なし」）は、**どちらの道が重いかの根拠として1つだけ**添える
- 5文目＝「次に見るのは〜」で始める、次に見る数字
- 文は「。」で数えて5つまで。数字（%・円・回・日付・期間なども1つ）は6つまで
- 統計の回数が30未満なら、本文に「回数が少ない」を必ず入れる（例「回数が少ないので参考の幅ですが」）

## 守ること（外れた問いだけ機械の検査で載りません）
- 数字は入力にあるものだけ。入力に無い過去の出来事・数字は、知っていても書かない
- 実在の評論家・投資家の名前、「〜氏」「〜流」は書かない。政策当局の人物の名前は、入力の見出しにあるものだけ可
- 人物の意図・狙いを推測しない。「狙い」「思惑」「意図」は使わない。発言は見出しにあるものだけを「〜と述べた」の形で
- 「〜だろう」「〜でしょう」「〜はずだ」、文末の「上がる」「下がる」のような言い切りは書かない（「〜しやすい」「〜の道」で書く）
- 「推奨」「おすすめ」「必ず」「確実」は書かない。「買い」「売り」は語の一部でも使わない（売買代金・空売り・買い越し・売り越し・買い残・売り残・自社株買い は可）
- 個別銘柄名・証券コードは書かない

## 出力（日曜版の JSON に qa キーを足す）
"qa": {"items": [
  {"id": "材料の問いの id", "q": "見出しの問い（30字以内・末尾は？か「か」。例「半導体の急落は、ここからどう転びうるか？」）",
   "a": ["1文目", "2文目", "3文目", "4文目", "5文目"],
   "paths": [{"name": "道の名前（12字以内）", "t": "SOX", "op": ">=", "v": 5},
             {"name": "もう1つの道", "t": "SOX", "op": "<=", "v": -5}]}]}
- paths は2つ。4文目の2つの道と同じ中身にする。t・v は材料の「道の条件」にある指標と幅だけ。op は ">=" か "<="（2つで向きを逆に）
- 1問の答えが検査に落ちても、日曜版の本体と他の問いは出る。qa のために本体を削ったり変えたりしない"""


# ------------------------------------------------------------------ 検査

INTENT_RE = re.compile(r"狙い|思惑|意図")
ASSERT_RE = re.compile(r"だろう|でしょう|はずだ|に違いない|(?:上が|下が)る(?:。|$)|(?:上が|下が)ります(?:。|$)"
                       r"|(?:上昇|下落|反発|続落)する(?:。|$)|(?:上昇|下落|反発|続落)します(?:。|$)")
QA_LEN = (260, 400)
QA_MAX_SENT = 5
QA_MAX_NUM = 6
LOW_WORD = "回数が少ない"
NAME_NUM_RE = re.compile(r"S&P500|ナスダック100|NASDAQ100|日経225|米?10年(?:金利|債利回り|債)|TOPIX500")


def _news_part(source):
    m = re.search(r"のニュースの見出し（AI朝刊の入力[^\n]*\n(.*?)(?:\n道の条件|\Z)", source, re.S)
    return m.group(1) if m else ""


def names_ok_qa(text, source):
    """deep_material.names_ok と同じ。ただし「〜氏」「〜長官」などは、その名前が入力の見出しにあれば可"""
    import deep_material as dm
    news = dm._norm(_news_part(source))
    t = dm._norm(text)
    # 見出しにある人の呼び方は、検査の前に取り除く（政策当局の人物など）
    for m in re.finditer(r"([一-龥ァ-ヶー]{1,10})(?:氏|さん|先生|長官|議長|総裁|大統領|首相|財務相|長官)", t):
        if m.group(1) in news and len(m.group(1)) >= 2:
            t = t.replace(m.group(0), "〈見出しの人物〉")
    return dm.names_ok(t)


def validate_item(it, meta, source):
    """1問の検査。戻り値: エラー（合格なら None）"""
    import deep_material as dm
    if not isinstance(it, dict):
        return "qa の問いが dict でない"
    if not meta:
        return f"材料に無い問い id: {it.get('id')}"
    if meta.get("stat") and meta.get("low") is None:
        return "統計を作れなかった問い"
    q, a, paths = it.get("q"), it.get("a"), it.get("paths")
    err = dm.q_ok(q, "qa")
    if err:
        return err
    if not (isinstance(a, list) and 1 <= len(a) <= QA_MAX_SENT and all(isinstance(x, str) and x.strip() for x in a)):
        return f"qa.a は1〜{QA_MAX_SENT}文の配列"
    body = "".join(x.strip() for x in a)
    if not (QA_LEN[0] <= len(body) <= QA_LEN[1]):
        return f"qa の答えは{QA_LEN[0]}〜{QA_LEN[1]}字（{len(body)}字）"
    # 指標の名前に入っている数字（S&P500・米10年金利 など）は数字の個数に数えない
    err = dm.shape_ok(NAME_NUM_RE.sub("〈指標〉", dm._norm(body)), QA_MAX_SENT, QA_MAX_NUM, "qa の答え")
    if err:
        return err
    # 2つの道
    if not (isinstance(paths, list) and len(paths) == 2 and all(isinstance(p, dict) for p in paths)):
        return "paths は2つ"
    for p in paths:
        t = PATH_T.get(p.get("t"))
        if not t:
            return f"paths.t は {'/'.join(PATH_T)}"
        if p.get("op") not in (">=", "<="):
            return "paths.op は >= か <="
        v = p.get("v")
        if not isinstance(v, (int, float)) or abs(v) not in PATH_V[t[2]] or v == 0:
            return f"paths.v は決まった幅だけ（{t[1]}）"
        if (p["op"] == ">=") != (v > 0):
            return "paths の向きと符号が合わない（>= は＋、<= は−）"
        if not isinstance(p.get("name"), str) or not (1 <= len(p["name"]) <= 12):
            return "paths.name は1〜12字"
    if paths[0]["op"] == paths[1]["op"]:
        return "2つの道は向きを逆にする"
    if body.count("なら") < 2:
        return "2つの道（「◯◯なら〜」を2つ）が無い"
    if meta.get("low") is not None and meta["low"] < LOW_N and LOW_WORD not in body:
        return f"統計の回数が{meta['low']}回（{LOW_N}未満）なのに「{LOW_WORD}」が無い"
    m = INTENT_RE.search(body + q)
    if m:
        return f"人物の意図の推測「{m.group(0)}」"
    m = ASSERT_RE.search(body)
    if m:
        return f"言い切り「{m.group(0)}」"
    names = " ".join(p["name"] for p in paths)
    bad = dm.numbers_ok(q + "\n" + body, source)
    if bad:
        return f"入力に無い数字: {', '.join(bad[:5])}"
    return names_ok_qa(q + "\n" + body + "\n" + names, source) or dm.words_ok(q + "\n" + body + "\n" + names)


def check_qa(data, ctx, source, extra=None):
    """data["qa"] を検査し、落ちた問いだけ外す。全部落ちたら qa ごと外す。
    通った問いには機械の確定値（n・src・道の条件の文）を足す。戻り値: 落ちた理由のリスト"""
    if "qa" not in data:
        return []
    qa = data["qa"]
    if not ctx or not isinstance(qa, dict) or not isinstance(qa.get("items"), list):
        data.pop("qa", None)
        print("警告: qa を外した（今週は問いが無いか、形が不正）", file=sys.stderr)
        return ["形"]
    keep, errs, seen = [], [], set()
    for it in qa["items"][:3]:
        iid = it.get("id") if isinstance(it, dict) else None
        meta = (ctx.get("items") or {}).get(iid)
        e = ("同じ問いが2つ" if iid in seen else None) or validate_item(it, meta, source) \
            or (extra(it) if extra else None)
        if e:
            errs.append(f"{iid}: {e}")
            print(f"警告: 質問箱の {iid} だけ外した: {e}", file=sys.stderr)
            continue
        seen.add(iid)
        it = {k: it[k] for k in ("id", "q", "a", "paths")}
        it["a"] = [x.strip() for x in it["a"]]
        for p in it["paths"]:
            p["cond_jp"] = path_cond_jp(p)
        it["n"] = meta.get("n")
        it["src"] = meta.get("src")
        keep.append(it)
    if not keep:
        data.pop("qa", None)
        print("警告: 質問箱の問いがすべて落ちたので qa を外した", file=sys.stderr)
    else:
        data["qa"] = {"vol": ctx.get("vol"), "items": keep}
    return errs


# ------------------------------------------------------------------ 翌週の土曜の答え合わせ

def judge_prev(weekend, fri):
    """先週の日曜版の qa の道を、来週（＝今週）の金曜終値→今週の金曜終値で判定する。
    戻り値: qa_check（無ければ None）"""
    qa = (weekend or {}).get("qa")
    nw = (weekend or {}).get("next_week") or {}
    if not qa or not nw.get("from") or nw.get("to") != fri.isoformat():
        return None
    start = _ts(nw["from"])
    out = {"vol": qa.get("vol"), "from_date": weekend.get("date"), "week": nw, "items": []}
    for it in qa.get("items") or []:
        res = []
        for p in it.get("paths") or []:
            t = PATH_T.get(p.get("t"))
            val = None
            try:
                c = closes(t[0], (start - timedelta(days=20)).strftime("%Y-%m-%d"))
                b = _pos_le(c, start - timedelta(days=1))
                e = _pos_le(c, _ts(nw["to"]))
                if b >= 0 and e > b and c.index[e] >= start:
                    a0, a1 = float(c.iloc[b]), float(c.iloc[e])
                    val = round(a1 - a0, 3) if t[2] == "%pt" else round((a1 / a0 - 1) * 100, 2)
            except Exception as ex:
                print(f"  質問箱の答え合わせ {p.get('t')} を取れず: {ex}", file=sys.stderr)
            hit = None if val is None else (val >= p["v"] if p["op"] == ">=" else val <= p["v"])
            res.append({"name": p.get("name"), "cond_jp": p.get("cond_jp") or path_cond_jp(p),
                        "val": val, "unit": t[2] if t else "%", "hit": hit})
        hits = [r["name"] for r in res if r["hit"]]
        if any(r["hit"] is None for r in res):
            verdict = "判定できず（終値が取れなかった）"
        elif len(hits) == 1:
            verdict = f"「{hits[0]}」の条件に届いた"
        elif len(hits) == 2:
            verdict = "両方の条件に届いた"
        else:
            verdict = "どちらの道の条件にも届かなかった（間の動き）"
        out["items"].append({"id": it.get("id"), "q": it.get("q"), "paths": res, "verdict": verdict})
    return out


# ------------------------------------------------------------------ 雇用統計の日程（一度だけ作ってコミットする）

ALFRED_PAGE = "https://alfred.stlouisfed.org/series/downloaddata?seid=PAYEMS"
CORRECTION_DAYS = 15   # 前の版から15日以内の版は訂正（2002-12-09・2003-10-10・2020-05-11）として除く


def build_payroll_dates():
    """2012年までは ALFRED の PAYEMS の版の日付（＝初出の発表日）、2013年からは BLS の日程（event_dates.json）"""
    req = urllib.request.Request(ALFRED_PAGE, headers=UA)
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    vint = sorted(set(re.findall(r'<option value="(\d{4}-\d{2}-\d{2})"', html)))
    kept = []
    for v in vint:
        if kept and (date.fromisoformat(v) - date.fromisoformat(kept[-1])).days < CORRECTION_DAYS:
            continue
        kept.append(v)
    ev = json.loads((HERE / "event_dates.json").read_text(encoding="utf-8"))
    bls = [x["d"] for x in ev["payroll"]["dates"]]
    dates = [{"d": v, "source": "ALFRED PAYEMS vintage"} for v in kept if v < "2013-01-01"]
    dates += [{"d": x["d"], "source": x.get("source", "BLS")} for x in ev["payroll"]["dates"]]
    # 重なる期間（2013年〜）で ALFRED と BLS が一致するか
    al13 = {v for v in kept if v >= "2013-01-01"}
    bl13 = {b for b in bls if b <= (kept[-1] if kept else "")}
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "note": ("米雇用統計（Employment Situation）の発表日。2012年までは ALFRED（セントルイス連銀）の PAYEMS の"
                    f"版の日付（前の版から{CORRECTION_DAYS}日以内の訂正版は除く）、2013年からは BLS の公式日程（event_dates.py）"),
           "sources": [ALFRED_PAGE, "https://www.bls.gov/schedule/"],
           "check_2013": {"alfred_only": sorted(al13 - bl13), "bls_only": sorted(bl13 - al13),
                          "both": len(al13 & bl13)},
           "dates": dates}
    PAYROLL_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{PAYROLL_FILE.name}: {len(dates)}回（ALFRED {sum(1 for d in dates if d['source'].startswith('ALFRED'))}・"
          f"BLS {len(bls)}）／2013年〜の突き合わせ {out['check_2013']}")


# ------------------------------------------------------------------ 別の数え方（確認用）

def verify_sox():
    """1問目を、pandas を使わない素のループ（終値の配列だけ）で数え直す"""
    sox = closes("^SOX", "1994-01-01")
    n225 = closes("^N225", "1994-01-01")
    sd, sv = [x.date() for x in sox.index], [float(x) for x in sox.values]
    nd, nv = [x.date() for x in n225.index], [float(x) for x in n225.values]
    ev, last = [], None
    for i in range(1, len(sv)):
        if (sv[i] / sv[i - 1] - 1) * 100 <= SOX_DROP:
            if last is None or i - last > SOX_GAP:
                ev.append(i)
                last = i
    res = {}
    for k in HORIZONS:
        a = [(sv[i + k] / sv[i] - 1) * 100 for i in ev if i + k < len(sv)]
        b = []
        for i in ev:
            j = max(x for x in range(len(nd)) if nd[x] <= sd[i])
            if j + k < len(nv):
                b.append((nv[j + k] / nv[j] - 1) * 100)
        res[f"sox{k}"] = _summ(a)
        res[f"nk{k}"] = _summ(b)
    s = stat_sox_drop()
    ok = all(res[k] == s["cond"][k] for k in res) and len(ev) == s["n"]
    print(f"素のループ: {len(ev)}回 / 本体: {s['n']}回 → {'一致' if ok else '不一致'}")
    for k in res:
        print(f"  {k}: ループ {res[k]} / 本体 {s['cond'][k]}")
    return ok


if __name__ == "__main__":
    if "--build-payroll-dates" in sys.argv:
        build_payroll_dates()
    elif "--verify" in sys.argv:
        sys.exit(0 if verify_sox() else 1)
    else:
        for k, (f, ln) in STATS.items():
            s = f()
            print("\n".join(ln(s)))
            print(f"  （最少の回数 {low_n(s)}）")
