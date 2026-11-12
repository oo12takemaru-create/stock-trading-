# -*- coding: utf-8 -*-
"""業績予想の修正・配当予想の修正の PDF（XBRL の付かない開示）から「修正前 → 修正後」の表を読む

起動文: kaburadar\\_報告_業績修正の向き.md（2026-11-12・PDF の版）
使い元: fc_revision.py（XBRL が無い修正の開示だけをここで読む。XBRL があればそちらが優先）

■ 何を読むか
  TDnet の修正の開示は、ほぼ次の表を持つ（2026-11-12 の PDF だけの101件で確認）。
               売上高   営業利益   経常利益   親会社株主に帰属する当期純利益   1株当たり当期純利益
      前回発表予想（A）  百万円 80,000   2,200   1,700   1,400   90.10
      今回修正予想（B）  84,000   3,200   2,600   2,550   163.80
      増減額（B－A）     4,000   1,000     900   1,150
      増減率（％）        5.0%   45.5%   52.9%   82.1%
  配当予想の修正は 列＝第2四半期末・期末・合計、行＝前回予想・今回修正予想・当期実績・前期実績。

■ 読み方（PDF の文字の位置で読む。表の罫線には頼らない）
  1. ページの文字を「語＋位置」で取り（PyMuPDF）、同じ高さの語を1行にまとめる
  2. 左端の見出しが「前回…予想」の行（A）と、その下の「今回…予想／修正後」の行（B）を探す。
     B の見出しに「実績」があるもの（予想と実績との差異の表）は修正ではないので読まない
  3. A の上にある項目名（売上高・営業利益…）の位置で列を決め、各行の数字を列に割り振る
  4. 数字の検算: 増減額の行があれば「B−A＝増減額」（表示の端数で ±1 まで）、無ければ増減率と合うか。
     **1項目でも合わない・検算できる行が無い・列に入らない数字がある・単位が読めない → 採用しない（向き不明のまま）**
  5. 向きの決め方は fc_revision.decide（XBRL と同じ）。通期の表があれば通期、無ければ中間期。連結があれば連結
  画像だけの PDF（文字が無い）・子会社の業績予想の開示・表の型に合わないものは読まない（当て推量をしない）。

PyMuPDF（pymupdf）が無い環境では読まない（fc_revision.py は XBRL だけで動く）。
"""
import re
import unicodedata

# ---------------------------------------------------------------- 文字の整え
DASH = "-"
TBD = "未定"
_SP = re.compile(r"\s+")
_UNIT = re.compile(r"^[(（]?(?:単位[:：])?(百万円|千円|億円|円|銭|円銭|%|％)[)）]?$")


def nz(s):
    """NFKC＋空白を取る（全角数字・括弧をそろえる）"""
    return _SP.sub("", unicodedata.normalize("NFKC", s or ""))


def is_unit(t):
    return bool(_UNIT.match(nz(t)))


_NUM1 = r"[△▲\-−]?\d+(?:\.\d+)?"
_NUM_RE = re.compile(rf"^\+?({_NUM1})$")
_RNG_RE = re.compile(rf"^\+?({_NUM1})(?:~|〜|～|から)\+?({_NUM1})$")
_DASHES = {"", "-", "―", "‐", "—", "ー", "－", "─", "−", "–", "…", "*"}


def _f(s):
    neg = s[0] in "△▲-−"
    v = float(s.lstrip("△▲-−"))
    return -v if neg else v


def parse_cell(text, pct=False):
    """セルの文字 → (lo, hi) / DASH / TBD / None（読めない）。百万円などの単位は外す"""
    t = nz(text).replace(",", "")
    t = re.sub(r"百万円|千円|億円|円|銭", "", t)
    if pct:
        t = t.replace("%", "")
    if t in _DASHES:
        return DASH
    if "未定" in t or t in ("—", "※"):
        return TBD
    m = _NUM_RE.match(t)
    if m:
        v = _f(m.group(1))
        return (v, v)
    m = _RNG_RE.match(t)
    if m:
        a, b = _f(m.group(1)), _f(m.group(2))
        return (min(a, b), max(a, b))
    return None


def decimals(text):
    t = nz(text).replace(",", "")
    ds = [len(x) for x in re.findall(r"\.(\d+)", t)]
    return max(ds) if ds else 0


def parse_yen(text):
    """配当のセル → 円（float）/ DASH / TBD / None。「55 円00 銭」「14.00」「円 銭 14.00」"""
    t = nz(text).replace(",", "")
    m = re.fullmatch(r"(\d+)円(\d{2})銭?", t)       # 「55 円00 銭」（「銭」は単位として外れることがある）
    if m:
        return int(m.group(1)) + int(m.group(2)) / 100
    m = re.fullmatch(r"(\d+)円", t)
    if m:
        return float(m.group(1))
    t = re.sub(r"円|銭", "", t)
    if t in _DASHES:
        return DASH
    if "未定" in t:
        return TBD
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t)
    return None


# ---------------------------------------------------------------- 行と列
def lines_of(words, tol=3.0):
    """[(x0,y0,x1,y1,text)] → [{"y": 中心, "w": [語…（x の順）]}]（上から）"""
    ws = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    out = []
    for w in ws:
        yc = (w[1] + w[3]) / 2
        if out and abs(out[-1]["y"] - yc) <= tol:
            out[-1]["w"].append(w)
            out[-1]["y"] = sum((a[1] + a[3]) / 2 for a in out[-1]["w"]) / len(out[-1]["w"])
        else:
            out.append({"y": yc, "w": [w]})
    for ln in out:
        ln["w"].sort(key=lambda w: w[0])
    return out


def _numlike(t):
    t = nz(t).replace(",", "")
    return bool(re.match(r"^[△▲+\-−]?\d", t)) or t in ("-", "―", "－", "─", "‐", "—", "−")


def label_of(ln):
    """行の左の見出し（最初の数字・横線より前の語）と、見出しの右端 x"""
    lab, x1 = [], None
    for w in ln["w"]:
        if _numlike(w[4]) and lab and not re.search(r"[(（]$", nz("".join(a[4] for a in lab))):
            break
        if is_unit(w[4]) and lab:
            break
        lab.append(w)
        x1 = w[2]
    return nz("".join(w[4] for w in lab)), x1


def kind_of(label):
    if re.match(r"^(連結|個別)?(前回|当初|直近|修正前|旧)", label) and re.search(r"予想|\(A\)|発表|公表", label):
        return "A"
    if re.match(r"^(連結|個別)?(今回|修正後|新)", label) and ("予想" in label or "(B)" in label or "修正" in label) \
            and "実績" not in label:
        return "B"
    if label.startswith(("今回", "実績")) and "実績" in label:
        return "ACT"                       # 予想と実績との差異の表（修正ではない）
    if label.startswith("増減額"):
        return "D"
    if label.startswith("増減率"):
        return "R"
    if re.match(r"^\(?(ご)?参考|^前期|^前年|^当期実績|^前中間|^前第", label):
        return "X"
    return None


ITEM_RULES = [  # (キー, 判定) 上から順に当てる
    ("eps", lambda t: re.search(r"1株|一株|株当", t)),
    ("other", lambda t: re.search(r"事業利益|コア|調整後|EBITDA|償却前|配当", t)),
    ("sales", lambda t: re.search(r"経常収益|営業収益|営業収入|売上|純営業収益|営業総収入|^収益$|^連結収益$", t)),
    ("op", lambda t: re.search(r"営業利益|営業損益|営業損失", t)),
    ("ord", lambda t: re.search(r"経常利益|経常損益|経常損失|税引前|税金等調整前", t)),
    ("np", lambda t: re.search(r"純利益|純損益|純損失|当期利益|中間利益|四半期利益|帰属", t)),
]
DIV_COLS = [("q1", r"第1四半期末"), ("q2", r"第2四半期末|中間期末|中間"), ("q3", r"第3四半期末"),
            ("total", r"合計|年間"), ("ye", r"期末")]


def item_of(text):
    t = nz(text)
    for k, f in ITEM_RULES:
        if f(t):
            return k
    return None


def div_col_of(text):
    # 上の段の「年間配当金」「1株当たり配当金」が列の名前にくっつくことがある（「年間配当金期末」）
    t = re.sub(r"(1株当たり|年間)?配当金(\(円\))?", "", nz(text))
    for k, pat in DIV_COLS:
        if re.search(pat, t):
            return k
    return None


HEAD_WORDS = sorted(["売上高", "売上収益", "営業収益", "営業収入", "営業総収入", "純営業収益", "経常収益", "営業利益",
                     "経常利益", "税引前利益", "当期純利益", "中間純利益", "四半期純利益", "純利益", "当期利益",
                     "第1四半期末", "第2四半期末", "第3四半期末", "期末", "合計", "年間"], key=len, reverse=True)


def _segment(chars):
    """[(文字, x0, x1, y0, y1)] → 項目名の辞書で区切った語 [(x0,y0,x1,y1,text)]。区切れなければ None"""
    s = "".join(nz(c[0]) for c in chars)
    if len(s) != len(chars):
        return None
    out, i = [], 0
    while i < len(s):
        hit = next((h for h in HEAD_WORDS if s.startswith(h, i)), None)
        if not hit:
            return None
        cs = chars[i:i + len(hit)]
        out.append((cs[0][1], min(c[3] for c in cs), cs[-1][2], max(c[4] for c in cs), hit))
        i += len(hit)
    return out


def _join_spaced(ws):
    """1文字ずつ離して書いた見出し（「売 上 高」「営 業 利 益」）をつなぐ。
    字の間と列の間が同じくらいのこともある（2026-11-12 2201 森永製菓「売 上 高 営 業 利 益 経 常 利 益」）ので、
    1〜2文字の語が並ぶところは 項目名の辞書で区切る。区切れなければ 字の間が字の高さの0.9倍未満のものだけつなぐ"""
    out, i = [], 0
    while i < len(ws):
        j = i
        while (j + 1 < len(ws) and len(nz(ws[j][4])) <= 2 and len(nz(ws[j + 1][4])) <= 2
               and ws[j + 1][0] - ws[j][2] < 2.2 * (ws[j][3] - ws[j][1])):
            j += 1
        if j > i:
            chars = []
            for w in ws[i:j + 1]:
                t = nz(w[4])
                step = (w[2] - w[0]) / max(len(t), 1)
                for q, ch in enumerate(t):
                    chars.append((ch, w[0] + q * step, w[0] + (q + 1) * step, w[1], w[3]))
            seg = _segment(chars)
            if seg:
                out.extend(seg)
                i = j + 1
                continue
        w = ws[i]
        h = w[3] - w[1]
        if out and w[0] - out[-1][2] < 0.9 * h:
            p = out[-1]
            out[-1] = (p[0], min(p[1], w[1]), w[2], max(p[3], w[3]), p[4] + w[4])
        else:
            out.append(tuple(w[:5]))
        i += 1
    return out


def build_columns(hlines, x_min):
    """見出しの行（下から）→ 列 [{"x0","x1","text"}]。2列以上にまたがる語（「1株当たり配当金」など）は使わない"""
    cols = []
    for ln in reversed(hlines):
        ws = [w for w in ln["w"] if w[0] >= x_min - 1 and not is_unit(w[4])]
        merged = _join_spaced(ws)
        for w in merged:
            hit = [c for c in cols if w[0] < c["x1"] - 1 and w[2] > c["x0"] + 1]
            if len(hit) == 1:
                c = hit[0]
                c["words"].append(w)
                c["x0"], c["x1"] = min(c["x0"], w[0]), max(c["x1"], w[2])
            elif not hit:
                cols.append({"x0": w[0], "x1": w[2], "words": [w]})
            # 2列以上にまたがる語は捨てる
    for c in cols:
        c["text"] = nz("".join(w[4] for w in sorted(c["words"], key=lambda w: (w[1], w[0]))))
    cols.sort(key=lambda c: c["x0"])
    # 重なってしまった列（上の行の語が2列をつないだ）は読まない
    for a, b in zip(cols, cols[1:]):
        if a["x1"] > b["x0"] + 1:
            return None
    return cols


def assign(words, cols, x_min):
    """語を列に割り振る → {列番号: 文字}。
    列の境目は となりの項目名の間の中点（数字は右寄せ・項目名は中央寄せなので、項目名の幅だけでは数字がはみ出す）。
    語の中心で列を決める。1つの語が2列にまたがって大きくはみ出す（境目から語の幅の3割以上）ときは None"""
    if not cols:
        return None
    left = (x_min + cols[0]["x0"]) / 2 if x_min < cols[0]["x0"] else cols[0]["x0"] - 2
    bounds = [left] + [(a["x1"] + b["x0"]) / 2 for a, b in zip(cols, cols[1:])] + [cols[-1]["x1"] + 60]
    cells = {}
    for w in words:
        if is_unit(w[4]):
            continue
        xc = (w[0] + w[2]) / 2
        if xc < bounds[0]:
            continue                      # 見出しの欄（左）
        best = None
        for i in range(len(cols)):
            if bounds[i] <= xc < bounds[i + 1]:
                best = i
                break
        if best is None:
            if _numlike(w[4]):
                return None
            continue
        wd = max(w[2] - w[0], 1)
        if _numlike(w[4]) and (bounds[best] - w[0] > 0.3 * wd or w[2] - bounds[best + 1] > 0.3 * wd):
            return None
        cells.setdefault(best, []).append(w)
    out = {}
    for i, ws in cells.items():
        ws = sorted(ws, key=lambda w: (round((w[1] + w[3]) / 2), w[0]))
        # 1つの欄に数字が2つ（となりの列の数字が入り込んだ・注記の数字）は読まない（2026-11-12 5142 で
        # 経常利益と純利益の2列が1つにつながり「20002200」になった。B−A も同じくつながるので検算では見抜けない）
        n_num, prev_t = 0, ""
        for w in ws:
            t = nz(w[4])
            if _numlike(t) and not re.match(r"^[~〜～]", t) and not re.search(r"[~〜～円]$", prev_t):
                n_num += 1
            prev_t = t
        out[i] = "".join(w[4] for w in ws) if n_num <= 1 else "?二つの数字"
    return out


# ---------------------------------------------------------------- 表を探す
HEAD_STOP = re.compile(r"^(\d+[.．、)]|[(（]\d+[)）]|[①-⑳]|【|■|●|記$|以上$)")


def find_tables(lines, page_w=595.0):
    """ページの行 → [{"rows": {"A": 行, ...}, "hlines": [...], "ctx": "表の上の文", "x_min": 見出し欄の右端}]"""
    labs = []
    for i, ln in enumerate(lines):
        lab, x1 = label_of(ln)
        labs.append((kind_of(lab), lab, x1))
    out = []
    i = 0
    while i < len(lines):
        k = labs[i][0]
        if k not in ("A",):
            i += 1
            continue
        # A の下: B（と D・R・X）を集める。80pt 以上あくか、次の A で終わる
        rows = [("A", i)]
        j, last_y = i + 1, lines[i]["y"]
        while j < len(lines) and lines[j]["y"] - last_y < 80:
            kj = labs[j][0]
            if kj == "A":
                break
            if kj in ("B", "ACT", "D", "R", "X"):
                if kj in [r[0] for r in rows]:
                    break
                rows.append((kj, j))
                last_y = lines[j]["y"]
            elif labs[j][1] and len(labs[j][1]) > 30 and "。" in labs[j][1]:
                break                      # 本文に戻った
            j += 1
        kinds = [r[0] for r in rows]
        if "B" not in kinds:
            i = j if j > i else i + 1
            continue
        # 見出しの欄の右端（A・B の見出しの右端のうち大きい方）
        x_min = max(labs[n][2] or 0 for _, n in rows if _ in ("A", "B"))
        # A の上の見出しの行（項目名）: 上へ 70pt まで。本文・番号つきの見出しで止める
        hl, ctx = [], []
        top = lines[i]["y"]
        n = i - 1
        while n >= 0 and top - lines[n]["y"] < 70:
            t = nz("".join(w[4] for w in lines[n]["w"]))
            if HEAD_STOP.match(t) or ("。" in t and len(t) > 25) or labs[n][0] in ("A", "B", "D", "R", "X", "ACT"):
                break
            if len(t) > 40 and lines[n]["w"][0][0] < x_min - 5 and not any(w[0] > x_min for w in lines[n]["w"]):
                break
            hl.insert(0, lines[n])
            top = lines[n]["y"]
            n -= 1
        # 表の上の文（期・連結/個別の手がかり）: 見出しの上 4行
        for m in range(max(0, n - 3), n + 1):
            ctx.append(nz("".join(w[4] for w in lines[m]["w"])))
        # 行ごとの高さの帯（となりの行の見出しとの中点まで）
        ys = [lines[r[1]]["y"] for r in rows]
        bands = {}
        for q, (kq, idx) in enumerate(rows):
            up = (ys[q] - (ys[q + 1] - ys[q]) / 2) if q == 0 and len(ys) > 1 else (ys[q] + ys[q - 1]) / 2
            dn = (ys[q] + ys[q + 1]) / 2 if q + 1 < len(ys) else ys[q] + (ys[q] - ys[q - 1]) / 2
            if q == 0 and hl:
                up = max(up, hl[-1]["y"] + 2)
            bands[kq] = (up, dn)
        # 見出しが枠の上寄せ・数字が下寄せの表（2026-11-12 9791）: 数字の行の数が見出しの行の数と同じなら、上から順に対応させる
        lo_y, hi_y = bands[rows[0][0]][0], bands[rows[-1][0]][1]
        vys = [ln["y"] for ln in lines if lo_y <= ln["y"] < hi_y
               and any(_numlike(w[4]) and w[0] > x_min + 5 for w in ln["w"])]
        if len(vys) == len(rows) and len(rows) > 1:
            for q, (kq, _) in enumerate(rows):
                up = (vys[q] + vys[q - 1]) / 2 if q else vys[q] - (vys[1] - vys[0]) / 2
                dn = (vys[q] + vys[q + 1]) / 2 if q + 1 < len(vys) else vys[q] + (vys[q] - vys[q - 1]) / 2
                bands[kq] = (max(up, lo_y), min(dn, hi_y))
        out.append({"rows": dict(rows), "bands": bands, "hlines": hl, "ctx": ctx, "x_min": x_min,
                    "labels": {kq: labs[idx][1] for kq, idx in rows}})
        i = j
    return out


def band_words(words, band):
    return [w for w in words if band[0] <= (w[1] + w[3]) / 2 < band[1]]


# ---------------------------------------------------------------- 期・連結・単位
def period_of(tb, cols):
    txt = "".join(tb["ctx"]) + "".join(nz("".join(w[4] for w in ln["w"])) for ln in tb["hlines"])
    head = "".join(c["text"] for c in cols)
    ms = list(re.finditer(r"(\d{4})年(\d{1,2})月\d{1,2}日[~〜～\-－―]+(\d{4})年(\d{1,2})月\d{1,2}日", txt))
    m = ms[-1] if ms else None             # 表にいちばん近い期間
    if m:
        span = (int(m.group(3)) - int(m.group(1))) * 12 + int(m.group(4)) - int(m.group(2)) + 1
        if span >= 11:
            return "通期"
        if 5 <= span <= 7:
            return "中間期"
        return None
    if re.search(r"中間|第2四半期|上期|上半期|累計", head + txt) and "通期" not in txt:
        return "中間期"
    if re.search(r"第[13]四半期", head + txt):
        return None
    return "通期"


def cons_of(tb, cols):
    txt = "".join(tb["ctx"]) + "".join(c["text"] for c in cols)
    if "個別" in txt and "連結" not in txt:
        return False
    return "連結" in txt or "親会社" in txt


def unit_of(tb, words):
    """百万円 → 1、千円 → 0.001、億円 → 100。読めなければ None"""
    top = (tb["hlines"][0]["y"] if tb["hlines"] else tb["bands"]["A"][0]) - 8
    txt = "".join(tb["ctx"]) + "".join(nz(w[4]) for ln in tb["hlines"] for w in ln["w"]) \
        + "".join(nz(w[4]) for w in band_words(words, (top, tb["bands"]["A"][1])))
    found = set(re.findall(r"百万円|千円|億円", txt))
    if len(found) != 1:
        return None
    return {"百万円": 1.0, "千円": 0.001, "億円": 100.0}[found.pop()]


# ---------------------------------------------------------------- 検算
def check(a, b, d, r, dec):
    """1項目の検算。True=合う・False=合わない・None=検算できる行が無い"""
    tol = 1.01 * 10 ** (-dec)
    if d is not None and d != TBD:
        if d == DASH:
            return a == b
        if a[0] != a[1] or b[0] != b[1]:   # レンジ: 端どうしの差
            if d[0] == d[1]:
                return abs(d[0] - (b[0] - a[0])) <= tol or abs(d[0] - (b[1] - a[1])) <= tol
            return abs(d[0] - (b[0] - a[0])) <= tol and abs(d[1] - (b[1] - a[1])) <= tol
        return abs((b[0] - a[0]) - d[0]) <= tol
    if r is not None and r not in (DASH, TBD) and r[0] == r[1] and a[0] == a[1] and b[0] == b[1]:
        if a[0] <= 0:
            return None
        # 表示の端数（±0.5）を入れても合うか
        h = 0.5 * 10 ** (-dec)
        lo = ((b[0] - h) - (a[0] + h)) / (a[0] + h) * 100
        hi = ((b[0] + h) - (a[0] - h)) / (a[0] - h) * 100
        return lo - 0.051 <= r[0] <= hi + 0.051
    if r == DASH and a[0] == a[1] and b[0] == b[1] and (a[0] <= 0 or b[0] <= 0 or a == b):
        return None                        # 赤字をまたぐ・同じ（率は「－」）
    return None


# ---------------------------------------------------------------- 1つの表を読む
def read_fc_table(tb, words):
    """業績の表 → (記録, 理由)。記録は fc_revision.parse_fc と同じ形（prev/cur は百万円）"""
    from fc_revision import decide, LABEL
    cols = build_columns(tb["hlines"], tb["x_min"])
    if not cols:
        return None, "項目名の列が読めない"
    keys = [item_of(c["text"]) for c in cols]
    if not any(k in ("sales", "op", "ord", "np") for k in keys):
        return None, "項目名の列が読めない"
    # 同じ項目が2列: 純利益は「親会社」「帰属」のある方。ほかは読まない
    pos = {}
    for i, k in enumerate(keys):
        if k not in ("sales", "op", "ord", "np"):
            continue
        if k in pos:
            if k == "np" and re.search(r"親会社|帰属", cols[i]["text"]) and not re.search(r"親会社|帰属", cols[pos[k]]["text"]):
                pos[k] = i
            elif k == "np" and re.search(r"親会社|帰属", cols[pos[k]]["text"]):
                pass
            else:
                return None, "同じ項目が2列ある"
        else:
            pos[k] = i
    unit = unit_of(tb, words)
    if unit is None:
        return None, "単位が読めない"
    cells = {}
    for kq in ("A", "B", "D", "R"):
        if kq not in tb["bands"]:
            continue
        c = assign(band_words(words, tb["bands"][kq]), cols, tb["x_min"])
        if c is None:
            return None, "列に入らない数字がある"
        cells[kq] = c
    prev, cur, nchk, dec_all = {}, {}, 0, 0
    for k, i in pos.items():
        ta, tb_ = cells["A"].get(i, ""), cells["B"].get(i, "")
        a, b = parse_cell(ta), parse_cell(tb_)
        if a is None or b is None:
            return None, f"{LABEL[k]}の数字が読めない"
        if a in (DASH, TBD) or b in (DASH, TBD):
            if b not in (DASH, TBD) and a in (DASH, TBD):
                cur[k] = b                 # 修正前が無い（新しく出した予想）
            continue
        dec = max(decimals(ta), decimals(tb_))
        d = parse_cell(cells.get("D", {}).get(i, "")) if "D" in cells else None
        if "D" in cells and i not in cells["D"]:
            d = DASH                        # 増減額の欄が空＝0（据置）と読む。合わなければ不採用
        r = parse_cell(cells.get("R", {}).get(i, ""), pct=True) if "R" in cells else None
        if "D" in cells and d is None:
            return None, f"{LABEL[k]}の増減額が読めない"
        ok = check(a, b, d, r, dec)
        if ok is False:
            return None, f"{LABEL[k]}の B−A が増減額・増減率と合わない"
        if ok is None and a != b:
            return None, f"{LABEL[k]}を検算できない（増減額・増減率の行が無い）"
        nchk += ok is True
        prev[k] = tuple(round(v * unit, 1) for v in a)
        cur[k] = tuple(round(v * unit, 1) for v in b)
    if not prev:
        if cur:
            return {"dir": None, "why": "修正前の数字が無い（新しく公表した予想）"}, None
        return None, "数字が無い"
    if nchk == 0 and all(prev[k] == cur.get(k) for k in prev):
        pass                                # 全部同じ（据置）。検算は不要
    dr, by, mix = decide(prev, cur)
    from fc_revision import _show
    rec = {"per": period_of(tb, cols), "cons": cons_of(tb, cols),
           "prev": {k: _show(v) for k, v in prev.items()},
           "cur": {k: _show(v) for k, v in cur.items()},
           "dir": dr, "by": LABEL.get(by) if by else None, "mix": mix}
    if rec["per"] is None:
        return None, "期が読めない（四半期など）"
    if mix and re.search(r"は(上方|下方)", mix):
        rec["mixed"] = True
    if dr is None:
        rec["why"] = "修正前と修正後の幅が重なる・項目がそろわない"
    return rec, None


def read_div_table(tb, words):
    """配当の表 → ({"part","prev","cur","dir"}, 理由)"""
    cols = build_columns(tb["hlines"], tb["x_min"])
    if not cols:
        return None, "配当の列が読めない"
    keys = [div_col_of(c["text"]) for c in cols]
    if "total" not in keys and "ye" not in keys:
        return None, "配当の列が読めない"
    rows = {}
    for kq in ("A", "B", "X"):
        if kq not in tb["bands"]:
            continue
        c = assign(band_words(words, tb["bands"][kq]), cols, tb["x_min"])
        if c is None:
            return None, "列に入らない数字がある"
        rows[kq] = {keys[i]: parse_yen(t) for i, t in c.items() if keys[i]}
    # 当期実績（中間配当を払い済み）の行は「X」に入ることがある。見出しが「当期実績」のときだけ足しに使う
    act = rows.get("X") if tb["labels"].get("X", "").startswith("当期実績") else {}
    A, B = rows.get("A", {}), rows.get("B", {})
    if any(v is None for v in list(A.values()) + list(B.values())):
        return None, "配当の数字が読めない"
    part = "total" if "total" in keys else "ye"
    a, b = A.get(part), B.get(part)
    if not isinstance(a, float) or not isinstance(b, float):
        return None, "修正前か修正後の配当が無い（未定など）"
    if part == "total":                    # 検算: 合計＝各期の和（修正後は払い済みの実績も足す）
        parts = [k for k in keys if k in ("q1", "q2", "q3", "ye")]
        sa = [A.get(k) if isinstance(A.get(k), float) else (act or {}).get(k) for k in parts]
        sa = [v for v in sa if isinstance(v, float)]
        sb = [B.get(k) if isinstance(B.get(k), float) else (act or {}).get(k) for k in parts]
        sb = [v for v in sb if isinstance(v, float)]
        if sa and abs(sum(sa) - a) > 0.011:
            return None, "配当の合計が各期の和と合わない（修正前）"
        if sb and abs(sum(sb) - b) > 0.011:
            return None, "配当の合計が各期の和と合わない（修正後）"
        if not sa or not sb:
            return None, "配当の合計を検算できない"
    d = "増配" if b > a else "減配" if b < a else "据置"
    return {"part": "年間" if part == "total" else "期末", "prev": a, "cur": b, "dir": d}, None


# ---------------------------------------------------------------- 書類全体
SKIP_TITLE = re.compile(r"子会社(における|の)[^。]{0,12}(業績予想|業績見通し)|(親会社|その他の関係会社)の[^。]{0,8}業績予想")


def parse_words(pages, title=""):
    """[[(x0,y0,x1,y1,text)…] ページごと] → 記録（src="pdf"）。読めなければ {"dir": None, "why": 理由, "src": "pdf"}"""
    if SKIP_TITLE.search(title or ""):
        return {"src": "pdf", "dir": None, "why": "子会社・親会社の業績予想の開示（この会社の予想ではない）"}
    if sum(len(w[4]) for p in pages for w in p) < 80:
        return {"src": "pdf", "dir": None, "why": "PDF に文字が無い（画像だけ）"}
    fcs, divs, whys = [], [], []
    for words in pages[:4]:
        for tb in find_tables(lines_of(words)):
            if "B" not in tb["rows"]:
                continue
            cols = build_columns(tb["hlines"], tb["x_min"]) or []
            isfc = any(item_of(c["text"]) in ("sales", "op", "ord", "np") for c in cols)
            isdiv = not isfc and any(div_col_of(c["text"]) in ("total", "ye") for c in cols)
            if isfc:
                rec, why = read_fc_table(tb, words)
                (fcs.append(rec) if rec else whys.append(why))
            elif isdiv:
                rec, why = read_div_table(tb, words)
                (divs.append(rec) if rec else whys.append(why))
            else:
                whys.append("項目名の列が読めない")
    out = {"src": "pdf"}
    # 通期を主に（連結を先に）。修正前のある表を先に
    fcs.sort(key=lambda r: (r.get("prev") is None, r.get("per") != "通期", not r.get("cons")))
    if fcs:
        out.update(fcs[0])
    if divs:
        out["div"] = divs[0]
    if not fcs and not divs:
        out.update({"dir": None, "why": "表の型に合わない（" + (whys[0] if whys else "修正前・修正後の表が無い") + "）"})
    elif not fcs:
        out.pop("dir", None)               # 配当だけの修正
    if whys and (fcs or divs):
        out["skip"] = whys[:3]             # 読まなかった表（記録だけ）
    return out


def pdf_words(data):
    """PDF のバイト列 → ページごとの語。PyMuPDF が無ければ ImportError"""
    try:
        import pymupdf
    except ImportError:  # 古い版は fitz
        import fitz as pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        return [[tuple(w[:5]) for w in p.get_text("words")] for p in list(doc)[:4]]
    finally:
        doc.close()


def parse_pdf(data, title=""):
    return parse_words(pdf_words(data), title)
