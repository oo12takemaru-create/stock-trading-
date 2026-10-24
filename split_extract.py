# -*- coding: utf-8 -*-
"""株式分割の開示を読む部品（株レーダー）

  classify(title)          表題 → 種類（split / correction / cancel / related / gratis / reverse / None）
  extract(pdf_bytes|text)  開示PDF → 分割比率（1:N）・基準日・効力発生日
  parse_jpx_kenri(text)    JPX「新株落・権利落等一覧」（月次PDF）→ 分割の行
  kenri_last(record)       基準日 → 権利付き最終日（基準日の2営業日前。基準日が休みならその前の営業日から数える）

■ 実物を読んでから書いている（2026-10-24）
  TDnet: 光フードサービス(138A)・内外テック(3374)・エクシオHD(362A)とその訂正。
  JPX : 17_kenri2509.pdf（2025年9月）・18_kenri1603.pdf（2016年3月）。
  PDFから起こした本文は分かち書き・全角数字が混ざるので、NFKC で揃えて空白を全部落としてから読む
  （自社株買い buyback_extract.py と同じ作法）。

■ 取れなければ None。推測で埋めない
  基準日は「2026年11月30日(月)を基準日として」の文を第一に、日程表の「基準日 2026年11月30日」を第二に読む。
  2つが食い違ったら値は入れず、extract_note に両方を残す。

■ 原本PDFは保存しない・配らない（統括方針 第3章）。URL だけ持つ
"""
import re
import unicodedata
from datetime import date, timedelta

try:
    import pymupdf
except ImportError:  # 環境によっては fitz
    try:
        import fitz as pymupdf
    except ImportError:          # 表題の分類だけ使う環境（テスト）では無くてよい
        pymupdf = None

import jp_bizday

# ─────────────────────────── 表題の分類
# 「会社分割」「吸収分割」「新設分割」は株式分割ではない（2026-10 の一覧で8件あった）。
# 株式分割の語は「株式分割」「株式の分割」の2通り
RE_SPLIT = re.compile(r"株式(?:の)?分割")
RE_CORR = re.compile(r"訂正")
RE_CANCEL = re.compile(r"中止|取りやめ|取止め|取り止め|撤回|延期|取消")
# 「株式分割に伴う定款の一部変更」「株式分割に伴う配当予想の修正」だけの表題は、決議そのものではなく付随の開示。
# 表題のどこかに「株式分割」が付随の言い回し**以外**で出てくれば決議とみなす
RE_FOLLOW = re.compile(r"株式(?:の)?分割(?:に伴う|に伴い|に係る|後の|後における|を踏まえた|の実施に伴う)")
RE_GRATIS = re.compile(r"株式(?:の)?無償割当")            # 新株予約権の無償割当（ライツ）は含めない
RE_REVERSE = re.compile(r"株式(?:の)?併合")


def normalize(t):
    return re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", t or ""))


def classify(title):
    """表題 → 種類。株式分割に関係が無ければ None

    split      株式分割の決議（「株式分割に関するお知らせ」「株式分割及び定款の一部変更…」）
    correction 株式分割の開示の訂正
    cancel     株式分割の中止・延期
    related    「株式分割に伴う…」だけの付随の開示（配当予想・優待・新株予約権の調整など）
    gratis     株式無償割当（分割と同じく株数が増えるが、別の種類として分ける）
    reverse    株式併合（株数が減る。別の種類）"""
    t = normalize(title)
    if RE_SPLIT.search(t):
        if RE_CORR.search(t):
            return "correction"
        if RE_CANCEL.search(t):
            return "cancel"
        rest = RE_FOLLOW.sub("", t)
        return "split" if RE_SPLIT.search(rest) else "related"
    if RE_GRATIS.search(t) and "新株予約権" not in t:
        return "gratis"
    if RE_REVERSE.search(t):
        return "reverse"
    return None


# ─────────────────────────── PDF本文から値を抜く
D = r"(\d{4})年(\d{1,2})月(\d{1,2})日"
WD = r"(?:\((?:[月火水木金土日](?:曜日?)?)\))?"          # 「(月)」「(月曜日)」
# 分割比率。「普通株式1株につき、2株の割合をもって分割」「1株を2株に分割」
RE_RATIO = [
    # 「1株に付き2株」（メイコー 2026-09-28）と書く会社もある
    re.compile(r"1株に?(?:つき|付き)[,、]?(\d+(?:\.\d+)?)株の割合"),
    re.compile(r"1株を(\d+(?:\.\d+)?)株に分割"),
    re.compile(r"1株に?(?:つき|付き)[,、]?(\d+(?:\.\d+)?)株"),
]
# 基準日。文の形（最も確か）と、日程表の形
RE_REC_SENT = re.compile(D + WD + r"(?:\(予定\))?を(?:分割の)?基準日(?:と|に)")
RE_REC_TBL = re.compile(r"(?<!公告)(?<!公告日)基準日(?!公告)(?:\(予定\))?[:：]?" + D)
RE_EFF = re.compile(r"効力発生日(?:\(予定\))?[:：]?" + D)
# 分割前・後の発行済株式総数（確認用。比率と食い違えば印を付ける）
RE_SH_BEFORE = re.compile(r"(?:株式)?分割前の(?:当社)?発行済株式(?:総)?数[^0-9]{0,8}([0-9][0-9,]*)株")
RE_SH_AFTER = re.compile(r"(?:株式)?分割後の(?:当社)?発行済株式(?:総)?数[^0-9]{0,8}([0-9][0-9,]*)株")


def pdf_text(data):
    if pymupdf is None:
        raise RuntimeError("pymupdf が無い")
    doc = pymupdf.open(stream=data, filetype="pdf")
    raw = "\n".join(p.get_text() for p in doc)
    doc.close()
    return raw


def _iso(m, off=0):
    y, mo, d = int(m.group(1 + off)), int(m.group(2 + off)), int(m.group(3 + off))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def _num(x):
    v = float(x)
    return int(v) if v.is_integer() else v


def extract(data_or_text):
    """開示PDF（バイト列）か本文 → {ratio, record, effective, shares_before, shares_after, extract_ok, extract_note}

    訂正の開示は「(訂正後)」より後ろだけを読む（訂正前の値を拾わないため）。
    そこに書かれていない項目は None のまま返し、呼び元で元の開示の値を残す"""
    if isinstance(data_or_text, (bytes, bytearray)):
        try:
            t = normalize(pdf_text(data_or_text))
        except Exception as e:
            return {"extract_ok": False, "extract_note": f"PDFを開けない: {e}"}
    else:
        t = normalize(data_or_text)
    if len(t) < 50:
        return {"extract_ok": False, "extract_note": "本文が取れない（画像PDFの可能性）"}
    corrected = False
    if "訂正後" in t:
        t = t[t.index("訂正後"):]
        corrected = True

    ratio = None
    for p in RE_RATIO:
        m = p.search(t)
        if m:
            ratio = _num(m.group(1))
            break
    if ratio is not None and not (1 < ratio <= 1000):
        ratio = None                       # 1株→1株・桁違いは読み違い

    notes = []
    rs = RE_REC_SENT.search(t)
    rt = RE_REC_TBL.search(t)
    a = _iso(rs) if rs else None
    b = _iso(rt) if rt else None
    record = a or b
    if a and b and a != b:
        record = None
        notes.append(f"基準日が文({a})と日程表({b})で食い違う")
    me = RE_EFF.search(t)
    eff = _iso(me) if me else None

    sb, sa = RE_SH_BEFORE.search(t), RE_SH_AFTER.search(t)
    sb = int(sb.group(1).replace(",", "")) if sb else None
    sa = int(sa.group(1).replace(",", "")) if sa else None
    if ratio and sb and sa:
        # 発行済株式数の前後と比率が合うか（端数で1株ずれる程度は許す）
        if abs(sb * ratio - sa) > max(2, sa * 0.001):
            notes.append(f"比率1:{ratio}と発行済株式数({sb:,}→{sa:,})が合わない")
    if record and eff and eff <= record:
        # 効力発生日は基準日の翌日以降（エクシオHDの訂正後は基準日10/31(土)→効力11/1(日)）
        notes.append(f"効力発生日({eff})が基準日({record})より前")

    out = {"ratio": ratio, "record": record, "effective": eff,
           "shares_before": sb, "shares_after": sa}
    got = [k for k in ("ratio", "record", "effective") if out[k] is not None]
    out["extract_ok"] = len(got) == 3 and not notes
    miss = [k for k in ("ratio", "record", "effective") if out[k] is None]
    if miss:
        notes.append("取れない: " + ",".join(miss))
    out["extract_note"] = "／".join(notes)
    if corrected:
        out["corrected_part"] = True
    return out


# ─────────────────────────── 権利付き最終日
def prev_bizday_or_same(d):
    while not jp_bizday.is_bizday(d):
        d -= timedelta(days=1)
    return d


def add_bizdays(d, n):
    step = 1 if n > 0 else -1
    for _ in range(abs(n)):
        d += timedelta(days=step)
        while not jp_bizday.is_bizday(d):
            d += timedelta(days=step)
    return d


def kenri_last(record):
    """基準日 → (権利付き最終日, 権利落ち日)。どちらも YYYY-MM-DD

    受渡しは T+2（2019-07-16 以降）。基準日に株主名簿に載るには、基準日（休みならその前の営業日）の
    2営業日前までに買っている必要がある。例: 基準日 2026-10-31(土) → 10/30(金)の2営業日前 = 10/28(水)。
    2019-07-16 より前は T+3 なので3営業日前（event_score.kenri と同じ扱い）"""
    r = date.fromisoformat(record)
    base = prev_bizday_or_same(r)
    back = 2 if base >= date(2019, 7, 16) else 3
    k = add_bizdays(base, -back)
    return k.isoformat(), add_bizdays(k, 1).isoformat()


# ─────────────────────────── JPX「新株落・権利落等一覧」
RE_CODE = re.compile(r"^[0-9]{3}[0-9A-Z]$")
RE_JDATE = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$")
RE_JRATIO = re.compile(r"(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)")
# 区分の見出し（その時点の市場）。実物 129か月分（2016-01〜2026-09）で出てきたもの。
# 英語の見出し行（1st Section / Prime / REITs …）は区分としては使わず、行の切れ目としてだけ使う
SECTIONS = ("市場第一部", "市場第二部", "マザーズ", "JASDAQスタンダード", "JASDAQグロース",
            "プライム", "スタンダード", "グロース", "TOKYO PRO Market", "不動産投信(REIT)",
            "ETF", "ETN(JDR)", "ETF(JDR)", "<外国株>")
RE_EN = re.compile(r"^(?:(?:1st|2nd) Section.*|Prime|Standard|Growth|Mothers|JASDAQ (?:Standard|Growth)|"
                   r"REITs?|ETFs?|ETNs?(?:\(JDR\))?|TOKYO PRO Market)$")
# 区分（種類）の行。分割は「分割」（〜2024年頃）か「株式分割」。比率が同じ行に付く年がある（「1:1.1 分割」）。
# 「投資口分割」（REIT）・「受益権分割」（ETF）は株式分割ではないので外す
RE_KIND_SPLIT = re.compile(r"^(?:(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s+)?(?:株式)?分割$")
RE_JRATIO_LINE = re.compile(r"^(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)$")


def _is_section(ln):
    return ln in SECTIONS or ln.startswith("1st Section") or bool(RE_EN.match(ln))


def parse_jpx_kenri(text):
    """月次PDFの本文 → [{code, name, ex, record, ratio, kind, section}]

    行は「コード／社名／英名（折り返すことがある）／権利落ち日／確定日／比率（無いこともある）／種類」の並び。
    比率と種類が1行に並ぶ年がある（2016年「1:1.1 分割」）。kind は split（株式分割）か other"""
    lines = [unicodedata.normalize("NFKC", x).strip() for x in (text or "").splitlines()]
    lines = [x for x in lines if x]
    out, sec, i = [], None, 0
    while i < len(lines):
        ln = lines[i]
        if ln in SECTIONS:
            sec = ln
            i += 1
            continue
        if not RE_CODE.match(ln):
            i += 1
            continue
        code = ln
        name = lines[i + 1] if i + 1 < len(lines) else ""
        j, dates = i + 2, []
        while j < len(lines) and len(dates) < 2 and j < i + 8:
            m = RE_JDATE.match(lines[j])
            if m:
                dates.append(f"{m.group(1)}-{m.group(2)}-{m.group(3)}")
            j += 1
        if len(dates) < 2:
            i += 1
            continue
        tail = []
        while (j < len(lines) and not RE_CODE.match(lines[j]) and not _is_section(lines[j])
               and len(tail) < 3):
            tail.append(lines[j])
            j += 1
        kind, ratio = "other", None
        for k, t in enumerate(tail):
            m = RE_KIND_SPLIT.match(t)
            if not m:
                continue
            kind = "split"
            if m.group(1):
                a, b = m.group(1), m.group(2)
            else:
                pm = RE_JRATIO_LINE.match(tail[k - 1]) if k else None
                a, b = (pm.group(1), pm.group(2)) if pm else (None, None)
            if a and float(a) == 1:
                ratio = _num(b)
            break
        out.append({"code": code, "name": name, "ex": dates[0], "record": dates[1],
                    "ratio": ratio, "kind": kind, "section": sec, "raw": " ".join(tail)[:60]})
        i = j
    return out


# その時点の市場 → いまの市場区分の系統（統計の区分に使う。2022-04 の再編をまたいで比べるため）
def market_group(section):
    s = section or ""
    if "プライム" in s or "第一部" in s:
        return "プライム（旧一部）"
    if "スタンダード" in s or "第二部" in s:
        return "スタンダード（旧二部・JASDAQスタンダード）"
    if "グロース" in s or "マザーズ" in s:
        return "グロース（旧マザーズ・JASDAQグロース）"
    if "JASDAQ" in s or "ジャスダック" in s:
        return "スタンダード（旧二部・JASDAQスタンダード）" if "グロース" not in s else "グロース（旧マザーズ・JASDAQグロース）"
    return None
