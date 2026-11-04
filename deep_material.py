# -*- coding: utf-8 -*-
"""AI朝刊の「深掘り」(deep)・日曜版の「長い目で見る」(long_view) の材料と検査（2026-11-04）

材料（プロンプトに足す。数字だけ・1,500字以内）:
  - 歴史の物差し: world_stats.json（同じ動きの翌日の日経）・wave.json（似た局面）・
    crash.json（暴落の傾斜計）・gauge.json（前兆の点灯数）・investor_hist.json（投資部門別の週次）
  - 世界のお金の流れ: world.json（5日・20日・年初来・円建て）
  - 10年の視点（日曜だけ）: investor_hist.json の2016年からの累計・日経平均/TOPIX の10年の位置

検査（ingest で使う。落ちたら deep / long_view だけを捨て、本体は出す）:
  - 文中の数字がすべて入力にある（四捨五入は可）
  - 実在の評論家・投資家の名前を含まない（名前そのものはコードに書かず、ハッシュで持つ）
    と「〜氏」「〜流」などの人を指す書き方をしない
  - 禁止語: 推奨・おすすめ・必ず・確実・買い/売り（需給の名詞だけ許可）
  - 字数
"""
import hashlib
import json
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
PAGES = "https://oo12takemaru-create.github.io/stock-trading-"
DOCS = Path("docs")
MAX_CHARS = 1500

# ------------------------------------------------------------------ 読み込み

def _load(name):
    """docs/ のローカルを先に読み、無ければ GitHub Pages から取る"""
    p = DOCS / name
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  {name}: ローカル読込失敗 {e}", file=sys.stderr)
    try:
        req = urllib.request.Request(f"{PAGES}/{name}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception as e:
        print(f"  {name}: 取得失敗 {e}", file=sys.stderr)
        return None


def _f(x, nd=2, sign=True):
    if x is None:
        return "—"
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


# ------------------------------------------------------------------ 世界のお金の流れ

# 並べる順（世界の値動きの地図 world.json の t）。多すぎると1,500字を超えるので主要なものだけ
FLOW_TICKERS = ["^GSPC", "^NDX", "^SOX", "^GDAXI", "000001.SS", "^KS11", "^N225", "1306.T",
                "GC=F", "CL=F", "HG=F", "JPY=X", "DX-Y.NYB", "^TNX", "^VIX"]


# 日曜は10年の視点を足すので、世界の並びは少なめにする
FLOW_TICKERS_SUN = ["^GSPC", "^SOX", "^GDAXI", "^KS11", "^N225", "1306.T",
                    "GC=F", "CL=F", "JPY=X", "^TNX", "^VIX"]


def world_flow_lines(periods=("d5", "d20", "ytd"), tickers=None):
    w = _load("world.json")
    if not w:
        return []
    items = {i["t"]: i for i in w.get("items", []) if i.get("ok")}
    pn = {"d1": "1日", "d5": "5日", "d20": "20日", "ytd": "年初来"}
    out = [f"世界の値動きの地図（{(w.get('updated') or '')[:16].replace('T', ' ')}の回）"
           f" {'/'.join(pn[p] for p in periods)}の騰落%（米金利は%pt）。[円]=円で持っていたら"]
    for t in tickers or FLOW_TICKERS:
        it = items.get(t)
        if not it:
            continue
        r = it.get("r") or {}
        nd = 3 if it.get("k") == "rate" else 2
        s = f"  {it['n']}: " + " / ".join(_f(r.get(p), nd) for p in periods)
        y = it.get("y") or {}
        if y and it.get("cur") not in (None, "JPY") and y.get("ytd") is not None:
            s += f" [円 年初来{_f(y.get('ytd'))}]"
        out.append(s)
    cnt = w.get("counts") or {}
    c = [f"{pn[p]}{cnt[p]['up']}/{cnt[p]['n']}" for p in periods if cnt.get(p)]
    if c:
        out.append("  世界の株価指数で上昇した数: " + "・".join(c))
    return out


# ------------------------------------------------------------------ 歴史の物差し

def _last_change(t, unit):
    """直近の終値の1日の変化（% または %pt）と、その日付。yf_latest で最新の終値を埋める"""
    try:
        from yf_latest import closes
        c = closes(t, period="10d", verbose=False)
        if c is None or len(c) < 2:
            return None, None
        a, b = float(c.iloc[-1]), float(c.iloc[-2])
        ch = (a - b) if unit == "%pt" else (a / b - 1) * 100
        return ch, c.index[-1].strftime("%Y-%m-%d")
    except Exception as e:
        print(f"  {t}: 直近の変化を取れず {e}", file=sys.stderr)
        return None, None


def world_stats_lines():
    ws = _load("world_stats.json")
    if not ws:
        return []
    rows = ws.get("rows") or []
    b = ws.get("base") or {}
    moves = {}
    for r in rows:
        if r["t"] not in moves:
            moves[r["t"]] = _last_change(r["t"], r["unit"])
    hits, near = [], None
    for r in rows:
        ch, d = moves.get(r["t"], (None, None))
        if ch is None:
            continue
        ratio = ch * r["dir"] / r["th"]
        if ratio >= 1:
            hits.append((r, ch, d))
        elif near is None or ratio > near[3]:
            near = (r, ch, d, ratio)
    out = [f"同じ動きの翌営業日の日経（{ws.get('since')}〜{ws.get('until')}・{ws.get('years')}年）。"
           f"全部の日{b.get('n')}日: 平均{_f(b.get('mean'), 3)}%・上昇{b.get('up')}%"]
    for r, ch, d in hits[:3]:
        unit = "%pt" if r["unit"] == "%pt" else "%"
        out.append(f"  今回当たった: {r['text']}（{d} {_f(ch, 3 if unit == '%pt' else 2)}{unit}）→ "
                   f"過去{r['n']}回 翌日の日経 平均{_f(r['mean'], 3)}%・中央値{_f(r['median'], 3)}%・上昇{r['up']}%"
                   + (f"・直近{r['last']}" if r.get("last") else ""))
    if not hits and near:
        r, ch, d, _ = near
        unit = "%pt" if r["unit"] == "%pt" else "%"
        out.append(f"  今回当たった条件なし。いちばん近い: {r['text']}（{d} {_f(ch, 3 if unit == '%pt' else 2)}{unit}）"
                   f" 過去{r['n']}回 翌日 平均{_f(r['mean'], 3)}%・上昇{r['up']}%")
    return out


def wave_lines():
    w = _load("wave.json")
    if not w:
        return []
    pos = w.get("position") or {}
    sim = w.get("similar") or {}
    cond = sim.get("cond") or {}
    nk = pos.get("n225") or {}
    out = [f"今の位置（{w.get('asof')}）: 地合い{pos.get('regime')} {pos.get('streak')}日連続・"
           f"日経{nk.get('close'):,.2f}は{nk.get('high_d')}の高値{nk.get('high'):,.2f}から{_f(nk.get('dist'))}%"
           if nk.get("close") else f"今の位置（{w.get('asof')}）: 地合い{pos.get('regime')}"]
    wh, bs = sim.get("whole") or {}, sim.get("base") or {}
    rules = w.get("rules") or {}
    yrs = rules.get("years")
    c = "・".join(str(v) for v in cond.values())
    s = f"  似た局面（{c}）は過去{yrs}年で{wh.get('n')}回"
    if wh.get("enough"):
        s += f": 20営業日後 上抜け{wh.get('up')}%・横ばい{wh.get('flat')}%・崩れ{wh.get('down')}%"
    out.append(s)
    for cs in (sim.get("cases") or [])[:2]:
        out.append(f"    {cs.get('d')}: 20営業日後の日経{_f(cs.get('ret20'))}%（結果 {cs.get('res')}）")
    if bs.get("n"):
        out.append(f"  条件なしの全期間{bs.get('n')}回: 上抜け{bs.get('up')}%・横ばい{bs.get('flat')}%・"
                   f"崩れ{bs.get('down')}%・20営業日後の中央値{_f(bs.get('ret20_med'))}%")
    return out


def crash_lines():
    c = _load("crash.json")
    if not c:
        return []
    on = [f["label"] for f in c.get("flags") or [] if f.get("on")]
    st = (c.get("stats") or {}).get("all") or {}
    per = c.get("period") or {}
    return [f"暴落の傾斜計（{c.get('trade_date')}）: {c.get('score')}/{c.get('flag_total')}点灯・{c.get('stage')}"
            + (f"（点灯: {'、'.join(on)}）" if on else ""),
            f"  この段階から{c.get('horizon')}営業日以内に日経が−10%以上下げた割合: "
            f"{per.get('all', '')} {c.get('prob')}%・{per.get('recent', '')} {c.get('prob_recent')}%"
            f"（全部の日 {st.get('_base')}%）"]


def gauge_lines():
    g = _load("gauge.json")
    if not g:
        return []
    ph = g.get("phase") or {}
    s = f"前兆の温度計: {g.get('lit')}/{g.get('total')}が点灯（{g.get('stage')}）"
    if ph.get("ok"):
        s += (f"。暴落の段階={ph.get('stage')}・高値{ph.get('peak_date')}からの下落率{_f(ph.get('drawdown'), 1)}%・"
              f"VIX{ph.get('vix')}・騰落レシオ{ph.get('adr')}")
    return [s]


def _iv_weeks():
    ih = _load("investor_hist.json")
    if not ih:
        return None, []
    return ih, [w for w in ih.get("weeks") or [] if isinstance(w.get("net"), dict)]


def investor_rank_lines():
    """海外投資家の直近4週の合計が、2016年からの4週合計の中で何番目か"""
    ih, ws = _iv_weeks()
    if len(ws) < 60:
        return []
    f = [w["net"].get("foreigners") or 0 for w in ws]
    s4 = [sum(f[i - 3:i + 1]) for i in range(3, len(f))]
    cur = s4[-1]
    hi = sum(1 for x in s4 if x > cur) + 1
    return [f"海外投資家（現物・週次 {ws[0]['s']}〜{ws[-1]['e']}・{len(ws)}週）: 直近週{f[-1]:+,}億円・"
            f"直近4週合計{cur:+,}億円は、4週合計{len(s4)}回の中で多い方から{hi}番目"]


def decade_lines():
    """10年の視点（日曜だけ）: 2016年からの累計と、日経平均・TOPIX の10年の位置"""
    out = []
    ih, ws = _iv_weeks()
    if ws:
        cats = {c["key"]: c["label"] for c in ih.get("categories") or []}
        last52 = ws[-52:]
        prev52 = ws[-104:-52]
        out.append(f"投資部門別の累計（{ws[0]['s']}〜{ws[-1]['e']}・{len(ws)}週・億円。"
                   f"{ih.get('market_change')}から対象が東証1部→プライム）")
        for k in ("foreigners", "business", "individuals", "trust_banks"):
            tot = sum(w["net"].get(k) or 0 for w in ws)
            a = sum(w["net"].get(k) or 0 for w in last52)
            b = sum(w["net"].get(k) or 0 for w in prev52)
            out.append(f"  {cats.get(k, k)}: 累計{tot:+,} / 直近52週{a:+,} / その前の52週{b:+,}")
    try:
        from yf_latest import history_closes
        # TOPIX は 1306.T の10年の日足に外れ値（安値の誤配信）があるので使わない。TOPIX は年初来（世界の地図）だけ
        for t, nm in (("^N225", "日経平均"),):
            c = history_closes(t, verbose=False, period="10y")
            if len(c) < 1500:
                continue
            last, first = float(c.iloc[-1]), float(c.iloc[0])
            hi, lo = float(c.max()), float(c.min())
            pos = (last - lo) / (hi - lo) * 100 if hi > lo else None
            out.append(f"  {nm} 10年: {c.index[0]:%Y-%m-%d}の{first:,.2f}→{c.index[-1]:%Y-%m-%d}の{last:,.2f}"
                       f"（{_f((last / first - 1) * 100, 1)}%）・10年の高値{hi:,.2f}・安値{lo:,.2f}・"
                       f"安値0〜高値100で{pos:.0f}の位置")
    except Exception as e:
        print(f"  10年の位置を取れず: {e}", file=sys.stderr)
    return out


def _cap(lines, limit=MAX_CHARS):
    """1,500字を超えたら後ろの行から削る（見出しの行は残す）"""
    out = list(lines)
    while out and len("\n".join(out)) > limit:
        out.pop()
    return out


def morning_material():
    """平日の朝刊に足す材料（型1〜3）。1,500字以内"""
    flow = world_flow_lines(("d5", "d20", "ytd"))
    hist = world_stats_lines() + wave_lines() + crash_lines() + gauge_lines() + investor_rank_lines()
    lines = ["[世界のお金の流れ]"] + flow + ["[歴史の物差し]"] + hist
    return _cap(lines)


def sunday_material():
    """日曜の「長い目で見る」に足す材料（型1〜4）。1,500字以内"""
    flow = world_flow_lines(("d5", "d20", "ytd"), FLOW_TICKERS_SUN)
    hist = world_stats_lines() + wave_lines() + crash_lines()
    dec = decade_lines()
    # 10年の視点を必ず残すため、先に置いてから歴史・世界の順で削る
    lines = ["[10年の視点]"] + dec + ["[歴史の物差し]"] + hist + ["[世界のお金の流れ]"] + flow
    return _cap(lines)


# ------------------------------------------------------------------ 検査

NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
# 「3段」「2つ」のような数えるための数字は入力に無くてよい（12まで）
COUNTER_RE = re.compile(r"(?<![\d.,])(\d{1,2})(?=つ|段|点|項目|番目|個)")


def _norm(s):
    return unicodedata.normalize("NFKC", s or "")


def _nums(text):
    """数字の集合（カンマ・符号を除き、Decimal 文字列で正規化）"""
    out = []
    for m in NUM_RE.finditer(_norm(text)):
        s = m.group(0).replace(",", "")
        if s.endswith("."):
            s = s[:-1]
        out.append(s)
    return out


def _canon(s):
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def numbers_ok(text, source):
    """text の数字がすべて source にあるか。四捨五入（小数の桁を減らす）は可。
    戻り値: 入力に無い数字のリスト（空なら合格）"""
    src = {_canon(x) for x in _nums(source)}
    src_f = []
    for x in src:
        try:
            src_f.append((float(x), len(x.split(".")[1]) if "." in x else 0))
        except ValueError:
            pass
    t = _norm(text)
    counters = {m.start() for m in COUNTER_RE.finditer(t)}
    bad = []
    for m in NUM_RE.finditer(t):
        if m.start() in counters and int(m.group(0)) <= 12:
            continue
        raw = m.group(0).replace(",", "").rstrip(".")
        c = _canon(raw)
        if c in src:
            continue
        nd = len(raw.split(".")[1]) if "." in raw else 0
        try:
            v = float(raw)
        except ValueError:
            bad.append(raw)
            continue
        if any(d > nd and abs(round(f, nd) - v) < 1e-9 for f, d in src_f):
            continue
        bad.append(m.group(0))
    return bad


# 実在の評論家・投資家の名前（NFKC・空白と「・」を除いた形の SHA-256 の先頭16桁）。
# 名前そのものはリポジトリに書かない（ページ・プロンプト・コードのどこにも人名を出さない方針）。
# 足すときは: python -c "import deep_material as d; print(d.name_hash('名前'))" の結果を足す（名前は書かない）。
# 一覧は日本の経済評論家・個人投資家の著名人と、海外の著名投資家（姓だけの形も）
NAME_HASHES = {
    "05e533dfb5664586", "0778cab9363daacd", "0940aa517b07102d", "0956ef150b4c5e4d",
    "11dafd6cb45d481c", "1918cff14a4079a3", "1ff2887f4fe16fbc", "27121d07e57182ba",
    "33fec4848e3041b4", "369dd34cb87e2436", "38b9bbe6fc3d05e0", "39343e241e472bfa",
    "3a5cca34eab46a7f", "3ad6c825b0ca916e", "48d64e5b7d80453a", "5d70241c28a6fa19",
    "6364b81d07eba0d3", "63972a70ed05e407", "67d0fa5aea2920b7", "6a57e58eae8890bd",
    "6cce55cc4425f02d", "7f11dc8378bc80f1", "897823ca548ee215", "957d4e4648156825",
    "95d665a3e4ce4f8e", "987fa9afa502c1d0", "aa5b1e36470f21c8", "abe8809efb40376d",
    "aca37a845dde2647", "acf6401ddd3a0050", "afc94f7beb5e78df", "b49f56a7c39f0542",
    "ba74e78aa9aa653c", "bb8471cd6bef9c1e", "bf0333c3cd421688", "c3bc77fb4069107b",
    "c7a3ece17fecfcfc", "cab5cf6051a1ee0d", "d1746d5152b677f0", "d56b614418b3604e",
    "d7feb900e6b02c9a", "e4fd8f1e1729e12c", "e832fd46e632de18", "f007b43ab2e742ff",
    "f1fd3157bfc02067", "fc0622fff8e2e691",
}


def _name_key(s):
    return re.sub(r"[\s・･=＝\.]", "", _norm(s)).lower()


def name_hash(s):
    return hashlib.sha256(_name_key(s).encode("utf-8")).hexdigest()[:16]


# 人を指す書き方（名前が一覧に無くても落とす）
PERSON_PATTERNS = [r"[一-龥ァ-ヶー]{1,10}(?:氏|さん|先生)(?:は|が|の|も|に|によ|いわく|曰く)?",
                   r"[一-龥ァ-ヶー]{1,10}流(?:の|に|で)", r"(?:評論家|投資家|ストラテジスト|アナリスト)の[一-龥ァ-ヶー]{2,}"]


def names_ok(text):
    """人名を含まないか。戻り値: 見つかった問題（無ければ None）"""
    t = _name_key(text)
    if NAME_HASHES:
        for L in range(2, 13):
            for i in range(0, max(0, len(t) - L + 1)):
                if hashlib.sha256(t[i:i + L].encode("utf-8")).hexdigest()[:16] in NAME_HASHES:
                    return "実在の人名（一覧にある名前）"
    n = _norm(text)
    for w in COMMON_WORDS:
        n = n.replace(w, "")
    for p in PERSON_PATTERNS:
        m = re.search(p, n)
        if m:
            return f"人を指す書き方「{m.group(0)}」"
    return None


# 「〜流」「〜さん」の形をしていても人を指さない言葉（先に取り除いてから調べる）
COMMON_WORDS = ["主流", "上流", "下流", "潮流", "時流", "還流", "環流", "本流", "逆流", "源流", "一流", "交流",
                "底流", "合流", "物流", "電流", "直流", "中流", "急流", "対流", "気流", "海流", "放流", "傍流",
                "皆さん", "みなさん"]


WORD_BANNED = [r"推奨", r"おすすめ", r"オススメ", r"必ず", r"確実", r"買うべき", r"売るべき",
               r"お金が流れ(?:た|込んだ|出した)", r"資金が流れ(?:た|込んだ|出した)"]
# 週末版（weekly.py の BUYSELL_ALLOW）と同じ需給の名詞だけ「買い」「売り」を許す
BUYSELL_ALLOW = ["空売り", "買い残", "売り残", "買い戻し", "信用買い", "信用売り",
                 "売買", "買い越し", "売り越し", "売上", "自社株買い"]


def words_ok(text):
    t = _norm(text)
    for p in WORD_BANNED:
        m = re.search(p, t)
        if m:
            return f"禁止語「{m.group(0)}」"
    u = t
    for a in BUYSELL_ALLOW:
        u = u.replace(a, "")
    m = re.search(r"買い|売り|買う|売る", u)
    if m:
        return f"禁止語「{m.group(0)}」（…{u[max(0, m.start() - 10):m.end() + 6]}…）"
    return None


def _check_text(text, source):
    bad = numbers_ok(text, source)
    if bad:
        return f"入力に無い数字: {', '.join(bad[:5])}"
    return names_ok(text) or words_ok(text)


DEEP_TYPES = {"history": "歴史の物差し", "world": "世界のお金の流れ", "why": "「なぜ」を3段"}
DEEP_LEN = (150, 360)       # 起動文は 200〜300字。少しだけ幅を持たせる
LONG_KEYS = ("history", "world", "why", "decade")
LONG_LEN = (360, 720)       # 起動文は 400〜600字
TITLE_MAX = 30


def validate_deep(d, source):
    """平日の朝刊の deep。戻り値: エラー（合格なら None）"""
    if not isinstance(d, dict):
        return "deep が dict でない"
    if d.get("type") not in DEEP_TYPES:
        return f"deep.type は {'/'.join(DEEP_TYPES)}"
    title, body = d.get("title"), d.get("body")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        return f"deep.title は1〜{TITLE_MAX}字"
    if not isinstance(body, str) or not (DEEP_LEN[0] <= len(body) <= DEEP_LEN[1]):
        return f"deep.body は{DEEP_LEN[0]}〜{DEEP_LEN[1]}字（{len(body) if isinstance(body, str) else '—'}字）"
    if d["type"] == "why" and not re.search(r"なら|場合", body):
        return "deep（なぜを3段）に条件（〜なら／〜場合）が無い"
    return _check_text(title + "\n" + body, source)


def validate_long_view(d, source):
    """日曜の long_view。戻り値: エラー（合格なら None）"""
    if not isinstance(d, dict):
        return "long_view が dict でない"
    title = d.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        return f"long_view.title は1〜{TITLE_MAX}字"
    parts = d.get("parts")
    if not isinstance(parts, list) or [p.get("k") if isinstance(p, dict) else None for p in parts] != list(LONG_KEYS):
        return f"long_view.parts は {'→'.join(LONG_KEYS)} の4つをこの順で"
    for p in parts:
        if not isinstance(p.get("body"), str) or not p["body"].strip():
            return f"long_view.parts.{p.get('k')} が空"
    total = sum(len(p["body"]) for p in parts)
    if not (LONG_LEN[0] <= total <= LONG_LEN[1]):
        return f"long_view は合計{LONG_LEN[0]}〜{LONG_LEN[1]}字（{total}字）"
    why = parts[2]["body"]
    if not re.search(r"なら|場合", why):
        return "long_view.why に条件（〜なら／〜場合）が無い"
    return _check_text(title + "\n" + "\n".join(p["body"] for p in parts), source)


def take_checked(data, key, validator, source):
    """data[key] を検査し、落ちたら data から外す（本体は出す）。戻り値: 落ちた理由 or None"""
    if key not in data:
        return None
    err = validator(data.get(key), source)
    if err:
        data.pop(key, None)
        print(f"警告: {key} を検査で外した（本体は出す）: {err}", file=sys.stderr)
    return err


# ------------------------------------------------------------------ プロンプトに足す指示（型の説明。人名は書かない）

RULES_COMMON = """- 材料は下の「深掘りの材料」と、ここまでの入力データだけ。**入力に無い過去の出来事・数字は書かない**（知っている歴史でも、入力に無ければ書かない）
- 数字は入力の表記のまま引く（小数を丸めるのは可）。「3つ」「2段」のような数え方以外の数字は、すべて入力にあるものだけ
- 「お金が流れた」と言い切らない。「〜が上がり〜が下がった並びは、〜のときに見られやすい」の形で書く
- 実在の人の名前・「〜氏」「〜流」は書かない。誰かの見方としてではなく、データの読み方として書く
- 「推奨」「おすすめ」「必ず」「確実」は書かない。「買い」「売り」は語の一部でも使わない（使ってよいのは 売買代金・空売り・買い越し・売り越し・買い残・売り残・自社株買い だけ）"""

DEEP_GUIDE = """# 深掘り（deep）の書き方
今朝のデータを、日々の値動きより一段深く読む段落を1つだけ書きます。次の3つの型から、今朝いちばん効くものを1つ選ぶ（type に書く）。
- history（歴史の物差し）: 今に似た過去の局面を1つ挙げ、その後どうなったかを数字で書く。材料は「歴史の物差し」の行（同じ動きの翌日・似た局面・傾斜計・投資部門別の順位）
- world（世界のお金の流れ）: 金・原油・ドル・米金利・各国株の並び（5日・20日・年初来・円建て）から、日本株への伝わり方を書く
- why（「なぜ」を3段）: 事実 → その理由として入力から言えること → その次に起きやすいこと（「〜なら〜しやすい」）。3段目は条件と、条件が外れたときの見方を必ず添える
""" + RULES_COMMON + """
- body は200〜300字。title は20字以内。採点・スタンスには関係しない（stance を動かす理由にしない）"""

LONG_GUIDE = """# 長い目で見る（long_view）の書き方
来週の地図とは別に、日々の値動きから一歩引いた節を書きます。採点はしません。次の4つを、この順で1段落ずつ（合計400〜600字）。
- history（歴史の物差し）: 今に似た過去の局面を1つ挙げ、その後どうなったかを数字で
- world（世界のお金の流れ）: 金・原油・ドル・米金利・各国株の並び（5日・20日・年初来・円建て）から、日本株への伝わり方
- why（「なぜ」を3段）: 事実 → その理由として入力から言えること → その次に起きやすいこと（「〜なら〜しやすい」）。3段目は条件と、条件が外れたときの見方を必ず添える
- decade（10年の視点）: 日本株の構造の変化を、入力にある長期の数字（投資部門別の累計・10年の位置など）だけで
""" + RULES_COMMON + """
- title は20字以内。見立て（view）とは矛盾させない"""


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "morning"
    lines = sunday_material() if mode == "sun" else morning_material()
    txt = "\n".join(lines)
    print(txt)
    print(f"---- {len(txt)}字", file=sys.stderr)
