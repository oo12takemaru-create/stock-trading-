# -*- coding: utf-8 -*-
"""週末版のAI朝刊 → docs/ai_weekly.json（株レーダー）

2026-11-07〜 週末版を土日の2本に分けた（2026-10-31 本人GO・統括方針 10/31 の節）
土曜（sat）: 「今週の振り返り」→ ai_weekly.json の `sat`
      主役は今週の話——日ごとの出来事・世界→日本の伝わり方・強かった業種／弱かった業種。
      先週日曜の見立ての採点（○×と通算）を1ブロック。外れたら何を読み違えたかを2〜3行（miss）。
      土曜には見立てを出さない（二重に採点しない）。
日曜（sun）: 「来週の地図」→ ai_weekly.json の `weekend`（キー名は 10/10〜の週末版のまま。Substack の材料も同じ）
      方向感（上寄り/横ばい/下寄り）・世界→日本株の伝わり方・見立てを変える条件・日別の見どころ・セクターの風向き。
      出した方向感は翌週の土曜に機械が採点する（views。金曜終値→翌週金曜終値）。採点の対象は日曜の見立てだけ。
記録: ingest のたびに docs/ai_weekly_days/<その週末の土曜>-sat|sun.json を積み、index.json を作り直す
      （kaburadar の gen_weekly.py が weekly/YYYY-MM-DD.html に焼く）。
（2026-10-10〜10-31 は土曜1本の週末版＝来週の地図。記録では日曜版の扱い。10/31 分の採点は 11/7 の土曜）

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
  python weekly.py --reindex sat docs/ai_weekly.json
      → docs/ai_weekly_days/index.json だけを作り直す（移行・手直し用）
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
REQUIRED_KEYS = {"sat": ("title", "points", "days", "world", "sectors", "miss", "lesson"),
                 "sun": ("title", "view", "view_reason", "points", "world", "flip", "days",
                         "sectors", "lesson")}
VIEW_JP = {"up": "上寄り", "flat": "横ばい", "down": "下寄り"}
FLAT_BAND = 1.0   # 横ばい＝週間の騰落が ±1.0% 以内（金曜終値→翌週金曜終値）
DAYS_DIR = "ai_weekly_days"   # docs/ai_weekly_days/<週末の土曜>-sat|sun.json（記録ページの元）

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
    # 2026-10-31 週末版を別ページにしたとき足した: ページに「推奨」「おすすめ」「買い」「売り」を出さない
    r"推奨", r"おすすめ", r"オススメ",
]
# 「買い」「売り」は語の一部でも出さない（円買い・戻り売り・売りが先行 も不可）。
# ただしサイト側（kaburadar の gen_stocks.py の ALLOW）と同じ需給の名詞だけは、先に取り除いてから調べる
BUYSELL_ALLOW = ["空売り", "買い残", "売り残", "買い戻し", "信用買い", "信用売り",
                 "売買", "買い越し", "売り越し", "売上"]
WORD_RULE = ("「推奨」「おすすめ」「買い」「売り」は、語の一部としても使わない"
             "（円買い・戻り売り・売りが先行・買い持ち も不可。言い換え例: 円高方向の動き／戻りの鈍さ／下げが先行／"
             "円の上昇に賭けた建玉）。使ってよいのは 売買代金・空売り・買い越し・売り越し・買い残・売り残 だけ")


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


def weekend_sat(today):
    """その週末の土曜（記録の単位）。土曜はその日、日曜は前日。平日に手で走らせたら直前の土曜"""
    return today - timedelta(days=(today.weekday() - 5) % 7)


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
    """週次で揃う需給"""
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
    """翌週（月〜金）に入る大型イベント。土曜なら2日後、日曜なら翌日が月曜。
    相場の暦（event_score.json の schedule）にあればそれを、無ければ上の手書きの表を使う"""
    mon = today + timedelta(days=2 if today.weekday() == 5 else 1)
    fri = mon + timedelta(days=4)
    out, seen = [], set()
    es = site_json("event_score.json") or {}
    for s in es.get("schedule") or []:
        try:
            dd = datetime.strptime(s.get("d", ""), "%Y-%m-%d").date()
        except ValueError:
            continue
        if mon <= dd <= fri:
            out.append(f"{dd:%m/%d}（{'月火水木金土日'[dd.weekday()]}） {s.get('t', '')} {s.get('time', '')}".rstrip())
            seen.add((dd, s.get("k")))
    keys = {"米CPI": "cpi", "雇用統計": "payroll", "FOMC": "fomc", "日銀": "boj", "メジャーSQ": "sq"}
    for d, name in EVENTS:
        dd = datetime.strptime(d, "%Y-%m-%d").date()
        k = next((v for w, v in keys.items() if w in name), None)
        if mon <= dd <= fri and (dd, k) not in seen:
            out.append(f"{dd:%m/%d}（{'月火水木金土日'[dd.weekday()]}） {name}")
    out.sort()
    return out, mon, fri


def kessan_lines(mon, fri):
    """来週の決算発表の社数（決算カレンダー kessan.json）。社名は渡さない（個別銘柄に触れないため）"""
    k = site_json("kessan.json") or {}
    out = []
    for d in k.get("days") or []:
        if mon.isoformat() <= d.get("d", "") <= fri.isoformat():
            out.append(f"  {d['d'][5:].replace('-', '/')}（{d.get('w', '')}） 決算発表 {d.get('n', 0)}社"
                       f"（うち主要{d.get('major', 0)}社）" + ("・発表が集中する日" if d.get("rush") else ""))
    return out


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
        import yfinance as yf  # noqa: F401
    except Exception:
        return ["(yfinance が無いため取得せず)"]
    out = []
    for t, label, fmt in MARKET_TICKERS:
        try:
            from yf_latest import history_closes
            c = history_closes(t, period="1mo")
            if len(c) < 6:
                continue
            last, prev = float(c.iloc[-1]), float(c.iloc[-6])
            chg = (last / prev - 1) * 100
            out.append(f"  {label}: {fmt.format(last)}（{c.index[-1]:%m/%d}・週間{chg:+.2f}%）")
        except Exception as e:
            print(f"  {t}: 取得失敗 {e}", file=sys.stderr)
    return out


def world5():
    """世界の値動きの地図（world.json）の5日の騰落。土曜の材料。(まとめ1行, 明細の行) を返す"""
    w = site_json("world.json") or {}
    if not w.get("items"):
        return "", []
    s = (w.get("summary") or {}).get("d5") or ""
    lines = [f"（world.json {str(w.get('updated', ''))[:16]} の回・{w.get('edition_label', '')}）", f"  まとめ: {s}"]
    gname = {g["k"]: g["n"] for g in w.get("groups") or []}
    for i in w["items"]:
        if not i.get("ok"):
            continue
        v = (i.get("r") or {}).get("d5")
        if not isinstance(v, (int, float)):
            continue
        unit = "%pt" if i.get("k") == "rate" else "%"
        lines.append(f"  [{gname.get(i.get('g'), i.get('g'))}] {i.get('n')}: 5日 {v:+.2f}{unit}")
    return s, lines


def week_days(rows, mon, fri):
    """今週の日ごとの機械データ（日経の騰落・値上がり/値下がり数・強かった業種/弱かった業種）と、
    週間の業種の騰落（日ごとの業種平均を掛け合わせたもの）。heatmap_days/YYYY-MM-DD.json から作る"""
    ret = {r["d"]: r.get("ret") for r in rows if isinstance(r.get("ret"), (int, float))}
    # 金曜の分は ai_record にまだ載っていない（採点は翌営業日）。日経の終値から前日比を埋める
    try:
        from yf_latest import history_closes
        c = history_closes("^N225", start=(mon - timedelta(days=10)).isoformat(),
                           end=(fri + timedelta(days=1)).isoformat())
        for i in range(1, len(c)):
            ds = f"{c.index[i]:%Y-%m-%d}"
            if mon.isoformat() <= ds <= fri.isoformat() and ds not in ret:
                ret[ds] = round((float(c.iloc[i]) / float(c.iloc[i - 1]) - 1) * 100, 2)
    except Exception as e:
        print(f"  ^N225 日ごと: 取得失敗 {e}", file=sys.stderr)
    days, acc = [], {}
    d = mon
    while d <= fri:
        ds = d.isoformat()
        hd = site_json(f"heatmap_days/{ds}.json") or {}
        secs = [s for s in hd.get("sectors") or [] if isinstance(s.get("avg"), (int, float))]
        if hd or ds in ret:
            ss = sorted(secs, key=lambda s: s["avg"], reverse=True)
            days.append({"d": ds, "w": "月火水木金"[d.weekday()], "ret": ret.get(ds),
                         "up": hd.get("up"), "down": hd.get("down"),
                         "top": [{"n": s["s"], "c": round(s["avg"], 2)} for s in ss[:3]],
                         "bottom": [{"n": s["s"], "c": round(s["avg"], 2)} for s in ss[-3:][::-1]]})
            for s in secs:
                acc[s["s"]] = acc.get(s["s"], 1.0) * (1 + s["avg"] / 100)
        d += timedelta(days=1)
    wk = sorted(((n, round((v - 1) * 100, 2)) for n, v in acc.items()), key=lambda x: x[1], reverse=True)
    sec = {"strong": [{"n": n, "c": c} for n, c in wk[:5]],
           "weak": [{"n": n, "c": c} for n, c in wk[-5:][::-1]]} if wk else None
    return days, sec


def week_day_lines(days):
    def pc(v):
        return f"{v:+.2f}%" if isinstance(v, (int, float)) else "—"
    out = []
    for x in days:
        out.append(f"  {x['d'][5:].replace('-', '/')}（{x['w']}） 日経{pc(x['ret'])}"
                   + (f"／値上がり{x['up']}・値下がり{x['down']}銘柄" if x.get("up") is not None else "")
                   + ("／強い業種 " + "、".join(f"{s['n']}{pc(s['c'])}" for s in x["top"]) if x["top"] else "")
                   + ("／弱い業種 " + "、".join(f"{s['n']}{pc(s['c'])}" for s in x["bottom"]) if x["bottom"] else ""))
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
        import yfinance as yf  # noqa: F401
        from yf_latest import history_closes
        c = history_closes("^N225", start=(day - timedelta(days=10)).isoformat(),
                           end=(day + timedelta(days=1)).isoformat())
        if len(c):
            return round(float(c.iloc[-1]), 2), f"{c.index[-1]:%Y-%m-%d}"
    except Exception as e:
        print(f"  ^N225 {day}: 取得失敗 {e}", file=sys.stderr)
    m = (s3 or {}).get("metrics") or {}
    if m.get("n225_close") and m.get("n225_date") == day.isoformat():
        return round(float(m["n225_close"]), 2), m["n225_date"]
    return None, None


def grade_prev_view(views, s3, today):
    """前の週末（日曜版）で出した方向感を、金曜終値で機械採点する。AIは関与しない。
    基準＝出したときの金曜終値 → 結果＝対象週の金曜（休場なら直前の営業日）の終値"""
    pend = [v for v in views if v.get("hit") is None and v.get("week_to", "9") < today.isoformat()]
    if not pend:
        # 採点済みの直近（土曜に採点したものを、日曜版でもそのまま見せる）
        done = [v for v in views if v.get("hit") is not None and v.get("week_to", "9") < today.isoformat()]
        return dict(done[-1]) if done else None
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


def prev_view_text(pv, prev_entry):
    if pv is None:
        return "（先週は方向感を出していません）"
    head = f"先週末の見立て「{VIEW_JP.get(pv['view'], pv['view'])}」（{pv.get('date')} 公開・タイトル「{pv.get('title', '')}」）"
    if prev_entry and prev_entry.get("date") == pv.get("date") and prev_entry.get("view_reason"):
        head += f"\n  そのときの根拠: {prev_entry['view_reason']}"
    if pv.get("hit") is None:
        return head + f"\n  → {pv.get('note', '未採点')}"
    return (head + f"\n  → 基準 {pv['base_date']} 終値 {pv['base_close']:,.0f}円 → "
            f"{pv['close_date']} 終値 {pv['close']:,.0f}円・週間{pv['ret']:+.2f}% → "
            + ("○ 的中" if pv["hit"] else "× 外れ"))


PROMPT_SAT = """あなたは日本株市場を専門とするマクロアナリストです。
株レーダーの「週末版・土曜＝今週の振り返り」を日本語で書いてください。読者は個人投資家で、土曜の朝に「今週なにが起きたのか」を整理するために読みます。

# この記事の主役は「今週の話」です（採点表ではありません）
- 今週、何が起きて、日本株のどこに効いたか。日ごとの出来事・世界→日本の伝わり方・強かった業種／弱かった業種を、下の数字から書く
- 世界で起きたことは「出来事 → 何が動いたか → 日本株のどこに効いたか」の順で、つながりを書く
- **来週の見立て（上寄り・横ばい・下寄り）は書かない**。来週の話は明日（日曜）の「来週の地図」で書きます
- 先週末の見立ての採点は機械が済ませています（下）。**外れていたら**、何を読み違えたかを miss に2〜3行で書く。当たっていたら miss は空の配列 []

# 厳守
- 個別銘柄名・証券コードは書かない（業種まで）。売買の指示は書かない
- {word_rule}
- 「必ず」「確実に」などの断定はしない
- 入力にない事実・数字を作らない。ニュースの原因を推測で断定しない（入力に原因が無い日は値動きの事実だけを書く）

# 先週末の見立ての採点（機械が計算済み・書き換えない）
{prev_view}

# 今週の日ごとの値動き（機械の集計。日経は前日比、業種は東証33業種の平均）
{week_days}

# 今週の業種（日ごとの業種平均を5日分つないだ週間の騰落）
{week_sectors}

# 今週の世界の値動き（世界の値動きの地図・5日の騰落）
{world5}

# 今週の世界の値動き（金曜終値・週間騰落）
{market}

# 毎朝のAI朝刊のスタンスと、その日の日経（機械の採点・参考）
{table}

# 金曜朝のAI朝刊の見立て（世界の流れ・セクター）
{morning}

# 週次の需給
{flow}

# 出力（JSONのみ。コードフェンスや前置きは書かない）
{{
  "title": "今週を一言で（25字以内。例: 中銀2つを越えて、半導体が戻った週）",
  "points": ["今週の要点を3行（各45字以内）。1行目=今週の日本株、2行目=世界の流れ、3行目=業種の明暗"],
  "days": [{{"d": "MM/DD（曜）", "what": "その日に起きたことを1文（数字つき）", "japan": "日本株のどこに、どちら向きに出たかを1文"}}],
  "world": [{{"event": "今週、世界で起きたこと（数字つき）", "path": "それが何を動かしたか", "japan": "日本株のどこに、どちら向きに効いたか"}}],
  "sectors": [{{"name": "業種名", "side": "strong（強かった）か weak（弱かった）", "why": "理由を1文（数字つき）"}}],
  "miss": ["先週末の見立てが外れたときだけ、何を読み違えたかを2〜3行（各60字以内）。当たったときは空の配列"],
  "lesson": "来週に持ち越す教訓を1文"
}}
points は3つ。days は今週の営業日すべて（上の日ごとの値動きにある日）。world は2〜3個。
sectors は strong 2つ・weak 2つ（上の週間の業種の数字から選ぶ）。JSONのみを出力すること。"""

PROMPT_SUN = """あなたは日本株市場を専門とするマクロアナリストです。
株レーダーの「週末版・日曜＝来週の地図」を日本語で書いてください。読者は個人投資家で、日曜にこれを読んで来週の準備をします。

# この記事の主役は「来週の地図」です
- 今週の値動きと世界の動きから、**来週1週間の日経平均の方向感を1つ**出してください
  （up=上寄り / flat=横ばい（週間±1%以内）/ down=下寄り）。迷っても必ずどれか1つを選ぶ
- 方向感は**来週の土曜に機械が採点し、そのまま公開します**（金曜終値→翌週の金曜終値）。
  当たり外れが残るので、根拠は数字で書いてください
- 世界で起きていることは「出来事 → 何が動くか → 日本株のどこに効くか」の順で、つながりを書く
- 「この形になったら見立てを変える」条件を、上側・下側の両方について**数値で**書く
- 今週の振り返りは昨日（土曜）の記事にあります。ここでは繰り返さず、来週に効くことだけを書く

# 厳守
- 個別銘柄名・証券コードは書かない（セクターまで）。売買の指示は書かない
- {word_rule}
- 「必ず」「確実に」などの断定はしない。「〜寄り」「〜しやすい」「〜なら」で書く
- 入力にない事実・数字を作らない。ニュースの原因を推測で断定しない

# 昨日（土曜）の「今週の振り返り」
{sat}

# 先週末の見立ての採点（機械が計算済み）
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
決算発表（決算カレンダー。社数だけ）:
{kessan}

# 来週のイベントの過去の反応（相場の暦・事実）
{event_stats}

# 出力（JSONのみ。コードフェンスや前置きは書かない）
{{
  "title": "来週の見立てを一言で（25字以内。例: CPI週、7万円の上は重い）",
  "view": "up か flat か down",
  "view_reason": "その方向感の根拠を2〜3文。3軸スコア・需給・世界の数字を必ず引用する",
  "points": ["来週の要点を3行（各45字以内）。1行目=来週の焦点、2行目=世界の流れ、3行目=来週の見立て"],
  "world": [{{"event": "世界で起きていること（数字つき）", "path": "それが何を動かすか", "japan": "日本株のどこに、どちら向きに効きやすいか"}}],
  "flip": [{{"side": "up か down", "if": "こうなったら（数値の条件）", "then": "見立てをどう変えるか"}}],
  "days": [{{"d": "MM/DD（曜）", "watch": "その日に見るものを1文"}}],
  "sectors": [{{"name": "セクター名", "wind": "tail（追い風）か head（逆風）", "why": "理由を1文（数字つき）"}}],
  "lesson": "来週に持ち越す教訓を1文"
}}
points は3つ。world は2〜3個。flip は2〜3個で、up と down を必ず両方入れる。
days は来週の営業日すべて（休場日は入れない）。sectors は追い風2つ・逆風2つ。JSONのみを出力すること。"""


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
    """日曜版（来週の地図）の形を確かめる。崩れた回答をページに出さないため"""
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


def validate_sat(d, pv):
    """土曜版（今週の振り返り）の形を確かめる。見立て（view）は持たせない"""
    if "view" in d or "flip" in d:
        return "土曜版に見立て（view/flip）は書かない"
    if not (isinstance(d.get("points"), list) and len(d["points"]) == 3):
        return "points は3行"
    if not (isinstance(d.get("days"), list) and d["days"]
            and all(isinstance(x, dict) and x.get("d") and x.get("what") for x in d["days"])):
        return "days が空か、d/what が無い行がある"
    if not (isinstance(d.get("world"), list) and 1 <= len(d["world"]) <= 3):
        return "world は1〜3個"
    secs = [s for s in d.get("sectors") or [] if isinstance(s, dict)]
    if any(s.get("side") not in ("strong", "weak") for s in secs):
        return "sectors.side は strong/weak"
    if not {"strong", "weak"} <= {s.get("side") for s in secs}:
        return "sectors に strong と weak の両方が要る"
    miss = d.get("miss")
    if not isinstance(miss, list) or len(miss) > 3 or not all(isinstance(x, str) for x in miss):
        return "miss は文字列の配列（0〜3行）"
    if pv and pv.get("hit") is False and len(miss) < 2:
        return "先週末の見立てが外れたので、miss は2〜3行"
    return None


def check_banned(data):
    blob = json.dumps(data, ensure_ascii=False)
    for pat in BANNED_PATTERNS:
        if re.search(pat, blob):
            return f"禁止パターン検出: {pat}"
    t = blob
    for a in BUYSELL_ALLOW:
        t = t.replace(a, "")
    m = re.search(r"買い|売り", t)
    if m:
        return f"禁止語検出: 「{m.group(0)}」（…{t[max(0, m.start() - 12):m.end() + 6]}…）"
    return None


def build_prompt(today):
    """入力データを集めて完成プロンプトを作る。sat で採点対象が無ければ exit 1。
    返り値: (prompt, ctx)。ctx は書き出し（write_entry）でも使う機械の確定値"""
    rec = site_json("ai_record.json")
    ana = site_json("ai_analysis.json")
    mon, fri = week_range(today)
    rows, stats = grade_week(rec, ana, mon, fri)
    flow = flow_lines()
    s3 = site_json("score3.json") or {}
    out = load_out()
    pv = grade_prev_view(out.get("views") or [], s3, today)
    ctx = {"rows": rows, "stats": stats, "mon": mon, "fri": fri, "pv": pv, "s3": s3}

    if MODE == "sat":
        if not rows:
            print("今週のスタンスが1件も取れませんでした", file=sys.stderr)
            sys.exit(1)
        tbl = [f"対象期間: {mon:%Y-%m-%d}（月）〜 {fri:%Y-%m-%d}（金）",
               f"判定できた日: {stats['judged']}日 / 的中 {stats['hits']}日"
               + (f" / 勝率 {stats['win']}%" if stats["win"] is not None else " / 勝率は判定不能")]
        for r in rows:
            mark = "—（中立は採点対象外）" if not r["judged"] else ("○ 的中" if r["hit"] else "× 外れ")
            rv = f"{r['ret']:+.2f}%" if isinstance(r.get("ret"), (int, float)) else "—"
            tbl.append(f"  {r['d']}（{r['w']}） スタンス={r['stance_jp']} / "
                       f"日経{rv} → {mark} / 見出し「{r['headline']}」")
        days, secw = week_days(rows, mon, fri)
        w5s, w5 = world5()
        ctx.update({"week_days": days, "sector_week": secw, "world_d5": w5s})
        secl = []
        if secw:
            secl.append("  強かった順: " + "、".join(f"{s['n']}{s['c']:+.2f}%" for s in secw["strong"]))
            secl.append("  弱かった順: " + "、".join(f"{s['n']}{s['c']:+.2f}%" for s in secw["weak"]))
        prompt = PROMPT_SAT.format(
            word_rule=WORD_RULE,
            prev_view=prev_view_text(pv, out.get("weekend")),
            week_days="\n".join(week_day_lines(days)) or "(取得できず)",
            week_sectors="\n".join(secl) or "(取得できず)",
            world5="\n".join(w5) or "(取得できず)",
            market="\n".join(market_lines()) or "(取得できず)",
            table="\n".join(tbl),
            morning="\n".join(morning_lines(ana)) or "(取得できず)",
            flow="\n".join(flow) or "(取得できず)")
    else:
        ev, nm, nf = next_week_events(today)
        days, closed = next_week_days(today)
        sat = out.get("sat") or {}
        if sat.get("date") == weekend_sat(today).isoformat():
            satt = (f"タイトル「{sat.get('title', '')}」\n"
                    + "\n".join(f"  ・{p}" for p in sat.get("points") or [])
                    + (f"\n  教訓: {sat['lesson']}" if sat.get("lesson") else ""))
        else:
            satt = "（今週の土曜版はありません）"
        prompt = PROMPT_SUN.format(
            word_rule=WORD_RULE, sat=satt,
            prev_view=prev_view_text(pv, None),
            market="\n".join(market_lines()) or "(取得できず)",
            morning="\n".join(morning_lines(ana)) or "(取得できず)",
            score=score_lines(s3, fri), flow="\n".join(flow) or "(取得できず)",
            days=" / ".join(days) or "(なし)", closed="\n".join(closed),
            events="大型イベント: " + (" / ".join(ev) if ev else "なし"),
            kessan="\n".join(kessan_lines(nm, nf)) or "  (予定の取得なし)",
            event_stats="\n".join(event_stat_lines(ev)) or "(該当なし)")
    return prompt, ctx


def days_dir():
    return Path(OUT).parent / DAYS_DIR


def rebuild_days_index():
    """docs/ai_weekly_days/ の中身から index.json を作り直す（新しい週末が先）"""
    dd = days_dir()
    weeks = {}
    for p in sorted(dd.glob("????-??-??-s??.json")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})-(sat|sun)\.json$", p.name)
        if not m:
            continue
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        w = weeks.setdefault(m.group(1), {"date": m.group(1)})
        w[m.group(2)] = {"date": e.get("date"), "title": e.get("title", "")}
        if m.group(2) == "sun":
            w["sun"].update({"view": e.get("view"), "view_jp": e.get("view_jp"),
                             "next_week": e.get("next_week")})
        else:
            w["sat"]["week"] = e.get("week")
    ix = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
          "weeks": sorted(weeks.values(), key=lambda x: x["date"], reverse=True)}
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "index.json").write_text(json.dumps(ix, ensure_ascii=False, indent=1), encoding="utf-8")
    return ix


def write_entry(data, label, now, ctx):
    """回答 → entry 組み立て → 書き出し（API / --answer 共通）"""
    today = now.date()
    mon, fri = ctx["mon"], ctx["fri"]
    wsat = weekend_sat(today)
    entry = {
        "kind": "sat" if MODE == "sat" else "weekend",
        "edition": MODE,
        "date": today.isoformat(),
        "weekend": wsat.isoformat(),
        "updated": now.isoformat(timespec="seconds"),
        "model": label,
        "week": {"from": mon.isoformat(), "to": fri.isoformat()},
        "note": "土日は市場が動かないため、金曜引け時点の数値で作成しています。",
        **data,
    }

    out = load_out()
    views = out.get("views") or []
    # 前の週末の見立ての採点（土曜が本番。土曜が落ちた週は日曜でも採点する）。採点はここだけで行う
    pv = grade_prev_view(views, ctx["s3"], today)
    if pv is not None and pv.get("hit") is not None:
        views = [pv if (v.get("date") == pv["date"]) else v for v in views]
    entry["prev_view"] = pv

    if MODE == "sat":
        entry["rows"] = ctx["rows"]
        entry["stats"] = ctx["stats"]
        entry["days_data"] = ctx.get("week_days") or []
        entry["sector_week"] = ctx.get("sector_week")
        entry["world_d5"] = ctx.get("world_d5") or ""
        out["sat"] = entry
    else:
        ev, nm, nf = next_week_events(today)
        entry["events"] = ev
        entry["next_week"] = {"from": nm.isoformat(), "to": nf.isoformat()}
        entry["view_jp"] = VIEW_JP[data["view"]]
        days, closed = next_week_days(today)
        entry["closed"] = closed
        sat = out.get("sat") or {}
        entry["sat_date"] = sat.get("date") if sat.get("weekend") == wsat.isoformat() else None
        base, bdate = n225_close_on(fri, ctx["s3"])
        # 同じ週を対象にした見立ては1つだけ（同じ日・同じ週の再実行は置き換え）
        views = [v for v in views if v.get("date") != today.isoformat()
                 and not (v.get("hit") is None and v.get("week_from") == nm.isoformat())]
        views.append({"date": today.isoformat(), "view": data["view"],
                      "base_close": base, "base_date": bdate,
                      "week_from": nm.isoformat(), "week_to": nf.isoformat(),
                      "title": data.get("title", "")})
        out["weekend"] = entry
    out["views"] = views[-104:]
    entry["view_record"] = view_record(views)
    out["updated"] = entry["updated"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # 記録（weekly/YYYY-MM-DD.html の元）
    dd = days_dir()
    dd.mkdir(parents=True, exist_ok=True)
    (dd / f"{wsat.isoformat()}-{MODE}.json").write_text(json.dumps(entry, ensure_ascii=False, indent=1), encoding="utf-8")
    rebuild_days_index()
    st = ctx["stats"]
    print(f"OK {OUT.name}: {MODE} {today}（記録 {DAYS_DIR}/{wsat}-{MODE}.json）"
          + (f" 毎朝の判定 {st['hits']}/{st['judged']}" if MODE == "sat" else f" 見立て {entry['view_jp']}")
          + f" / 通算の見立て {entry['view_record']}")


def run_prompt_out(prompt_out):
    """APIを呼ばず、完成したプロンプトだけを書き出す（routine 用）"""
    now = datetime.now(JST)
    prompt, _ctx = build_prompt(now.date())
    Path(prompt_out).write_text(prompt, encoding="utf-8")
    print(f"モード: {MODE} / プロンプトを出力: {prompt_out} ({len(prompt)}文字)")


def validate(data, ctx):
    return check_banned(data) or (validate_sat(data, ctx.get("pv")) if MODE == "sat" else validate_weekend(data))


def run_answer(answer_path, label):
    """APIを呼ばず、routine が書いた回答JSONを検証して書き出す。不正なら exit 2"""
    global LOCAL_FIRST
    LOCAL_FIRST = True
    now = datetime.now(JST)
    print(f"モデル: {label} / モード: {MODE}")
    _prompt, ctx = build_prompt(now.date())
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
    err = validate(data, ctx)
    if err is not None:
        print(f"回答不正: 検証NG: {err}", file=sys.stderr)
        sys.exit(2)
    write_entry(data, label, now, ctx)


def run_api():
    """従来モード: Anthropic API を呼んで生成する（手動予備）"""
    now = datetime.now(JST)
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        print("ANTHROPIC_API_KEY 未設定", file=sys.stderr)
        sys.exit(1)

    prompt, ctx = build_prompt(now.date())

    print(f"モデル: {MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL)} / モード: {MODE}")
    data, err = None, None
    for i in range(3):
        try:
            cand = call_anthropic(prompt, key)
            err = validate(cand, ctx)
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

    write_entry(data, MODEL_NAMES.get(ANTHROPIC_MODEL, ANTHROPIC_MODEL) + " (Anthropic)", now, ctx)


def main():
    global MODE, OUT
    import argparse
    ap = argparse.ArgumentParser(description="週末版AI朝刊")
    ap.add_argument("mode", nargs="?", default="sat", help="sat(=review) / sun(=outlook)")
    ap.add_argument("out", nargs="?", default="docs/ai_weekly.json")
    ap.add_argument("--prompt-out", help="APIを呼ばず、完成したプロンプトをこのファイルに書いて終了")
    ap.add_argument("--answer", help="APIを呼ばず、このAI回答JSONを検証して書き出す")
    ap.add_argument("--model-label", default="", help=f"--answer 時の model 表記(既定: {ROUTINE_LABEL})")
    ap.add_argument("--reindex", action="store_true", help="docs/ai_weekly_days/index.json だけを作り直す")
    a = ap.parse_args()
    MODE = MODE_ALIASES.get(a.mode.lower(), a.mode.lower())
    OUT = Path(a.out)
    if a.prompt_out and a.answer:
        ap.error("--prompt-out と --answer は同時に指定できません")
    if MODE not in ("sat", "sun"):
        print(f"モード不正: {a.mode}（sat / sun / review / outlook）", file=sys.stderr)
        sys.exit(1)

    if a.reindex:
        ix = rebuild_days_index()
        print(f"index.json: {len(ix['weeks'])}週")
    elif a.prompt_out:
        run_prompt_out(a.prompt_out)
    elif a.answer:
        run_answer(a.answer, a.model_label.strip() or ROUTINE_LABEL)
    else:
        run_api()


if __name__ == "__main__":
    main()
