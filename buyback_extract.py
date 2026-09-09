# -*- coding: utf-8 -*-
"""自社株買い開示PDFから数値を抜く（株レーダー）

■ 実物のPDFを読んでから書いている
  TDnetのPDFは本文が分かち書きで出る。「取 得 期 間」「ク ロ ス プ ラ ス」のように
  1文字ずつ空白が入るので、**空白を全部落としてから**正規表現をかける。
  全角数字（９月７日）も混ざるので NFKC で揃える。

■ 取れなければ空にする。推測で埋めない
  項目ごとに None を許し、extract_ok / extract_note で状態を残す。
  「たぶんこれだろう」で数字を入れると、後から検算できない嘘になる。
"""
import re
import unicodedata

try:
    import pymupdf
except ImportError:  # 環境によっては fitz
    import fitz as pymupdf


def pdf_text(data):
    """PDFのバイト列 → 空白を落とした本文"""
    doc = pymupdf.open(stream=data, filetype="pdf")
    raw = "\n".join(p.get_text() for p in doc)
    doc.close()
    return raw


def norm(t):
    """全角→半角に揃え、空白を全部落とす（分かち書き対策）"""
    t = unicodedata.normalize("NFKC", t or "")
    return re.sub(r"[\s　]+", "", t)


NUM = r"([0-9][0-9,]*)"

# 取得し得る株式の総数（上限）
RE_SHARES = [
    re.compile(r"取得し得る株式の総数[^0-9]{0,12}" + NUM + r"株"),
    re.compile(r"取得する株式の総数[^0-9]{0,12}" + NUM + r"株"),
    re.compile(r"取得(?:対象)?株式の(?:総)?数[^0-9]{0,12}" + NUM + r"株"),
    re.compile(r"取得(?:予定)?株式総数[^0-9]{0,12}" + NUM + r"株"),
]
# 発行済株式総数（自己株式を除く）に対する割合
RE_PCT = [
    re.compile(r"発行済株式総数[^%％]{0,40}?に対する割合[^0-9]{0,8}([0-9]+\.?[0-9]*)[%％]"),
    re.compile(r"発行済株式(?:の)?総数[^%％]{0,40}?割合[^0-9]{0,8}([0-9]+\.?[0-9]*)[%％]"),
    re.compile(r"(?:に対する)?割合[^0-9]{0,6}([0-9]+\.?[0-9]*)[%％]"),
]
# 取得価額の総額（上限）。
# 実物は「2,000百万円」「50億円」「300,000千円」と単位付きで書かれることが多い。
# 単位を取り違えると桁が1000倍ずれるので、必ず単位ごと拾って円に直す
AMT = r"([0-9][0-9,]*(?:\.[0-9]+)?)(億|百万|千万|万|千)?円"
UNIT = {None: 1, "": 1, "千": 1_000, "万": 10_000, "十万": 100_000,
        "百万": 1_000_000, "千万": 10_000_000, "億": 100_000_000}
RE_AMOUNT = [
    re.compile(r"取得価額の総額[^0-9]{0,14}" + AMT),
    re.compile(r"取得価格の総額[^0-9]{0,14}" + AMT),
    re.compile(r"取得(?:に要する)?(?:金額|総額)[^0-9]{0,14}" + AMT),
]
# 「取得期間」「取得する期間」「買付日」（ToSTNeT-3は単日）を拾う
RE_PERIOD = re.compile(r"取得(?:する)?期間[：:]?(.{0,60})")
RE_PERIOD2 = re.compile(r"(?:買付(?:け)?日|取得日|買付予定日)[：:]?(.{0,40})")
# 「取得方法」は決議内容の表の1項目で、実物では必ず番号付きの見出しになっている:
#   「(5)取得方法東京証券取引所における市場買付」
#   「(5)取得の方法:自己株式立会外買付取引(ToSTNeT-3)を含む市場買付」
#   「2.取得の方法本日(2026年8月28日)の終値にて…買付けの委託を行う」（ToSTNeT-3の型）
# 見出しの形を要求しないと、本文の「その具体的な取得方法について決議しましたので」に
# 引っかかってあいさつ文を拾う。2026-09-09 時点で method 177件中78件がこれだった。
# 見出しの番号は「(5)」「5.」「5」「⑤」と揺れる（PDFから起こすと括弧が落ちることがある）。
# 項目名も「取得方法」「取得の方法」「株式の取得方法」と揺れる
RE_METHOD = re.compile(
    r"(?:[(（]\d{1,2}[)）]|\d{1,2}[.．]?|[①-⑳])"
    r"(?:自己)?(?:株式)?の?取得(?:の)?方法[：:]?([\s\S]{0,120})")
# 項目の切れ目。次の番号・注記・参考・以上・句点で切る。
# 中身の括弧（(ToSTNeT-3) や (証券会社による取引一任方式)）は残したいので、
# 「括弧なら何でも切る」にはしない
RE_METHOD_END = re.compile(r"[(（]\d{1,2}[)）]|[(（<＜]?(?:ご)?参考|[(（]注|※|以上|なお|。")

# 2026年9月7日 / 2026年9月7日から / ～11月30日（年が省略される）
DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")

# ToSTNeT-3 の買付日。実物を読んで分かった書き方（2026-09-09 確認・生存PDF 34件中33件が該当）:
#   「2026年9月7日午前8時45分の東京証券取引所の自己株式立会外買付取引において買付けの委託を行う」
#   「2026年8月19日午前8時50分の福岡証券取引所の…」  ← 取引所ごとに時刻が違う
# 時刻を8:45に決め打ちすると福証・名証を落とすので、時刻は縛らず「午前○時○分」で拾う。
# 本文で最初に出るこの形が買付日（後段の「取得結果の公表」も同じ日を指す）。
#
# ※ tostnet3 の period_from を買付日に流用してはいけない。
#   period_from は親の取得枠の期間（例「取得期間 2026年1月30日〜2027年1月29日」）で、
#   買付日とは別物。2026-09-09 に既存28件を調べて誤りが判明し、この形の抽出に切り替えた。
RE_BUY_DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日午前\d{1,2}時\d{1,2}分")


def buy_date(t, year_hint=None):
    """ToSTNeT-3の買付日（YYYY-MM-DD）。書かれていなければ None"""
    m = RE_BUY_DATE.search(t or "")
    if not m:
        return None
    y = m.group(1) or year_hint
    if not y:
        return None
    return f"{int(y):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _first(pats, t):
    for p in pats:
        m = p.search(t)
        if m:
            return m.group(1)
    return None


def _int(s):
    return int(s.replace(",", "")) if s else None


def _amount(t):
    """金額を単位ごと拾って円に直す。「50億円」→ 5000000000"""
    for p in RE_AMOUNT:
        m = p.search(t)
        if m:
            n = float(m.group(1).replace(",", ""))
            return int(round(n * UNIT.get(m.group(2), 1)))
    return None


def extract_method(t):
    """取得方法。見出しの直後の1項目だけを取り、次の項目に入る手前で切る"""
    m = RE_METHOD.search(t or "")
    if not m:
        return None
    seg = m.group(1)
    end = RE_METHOD_END.search(seg)
    if end:
        seg = seg[:end.start()]
    seg = seg.strip("：:・、 ")[:100]
    # 100字で切ると括弧の途中で終わることがある（「…買付けの委託を行います(その」）。
    # 対応する閉じ括弧が無い開き括弧は、そこから後ろを落とす
    depth = 0
    cut = len(seg)
    for i, ch in enumerate(seg):
        if ch in "(（":
            if depth == 0:
                cut = i
            depth += 1
        elif ch in ")）":
            if depth:
                depth -= 1
            if depth == 0:
                cut = len(seg)
    if depth:
        seg = seg[:cut]
    return seg.strip("：:・、 ") or None


def parse_period(seg):
    """「2026年9月7日~11月30日」→ (from, to)。年が省略された終了日は開始年を継ぐ"""
    ds = DATE.findall(seg or "")
    if not ds:
        return None, None
    def build(t, fallback_year):
        y = t[0] or fallback_year
        if not y:
            return None
        return f"{int(y):04d}-{int(t[1]):02d}-{int(t[2]):02d}"
    y0 = ds[0][0]
    a = build(ds[0], None)
    b = build(ds[1], y0) if len(ds) > 1 else None
    # 「2026年12月1日~1月31日」のように年をまたぐ場合、終了が開始より前なら翌年
    if a and b and b < a:
        b = f"{int(b[:4])+1}{b[4:]}"
    return a, b


def extract(data_or_text, year_hint=None):
    """PDFのバイト列（または本文文字列）から数値を抜く

    year_hint: 本文で年が省略されたときに補う年（開示日の年を渡す）"""
    if isinstance(data_or_text, (bytes, bytearray)):
        try:
            t = norm(pdf_text(data_or_text))
        except Exception as e:
            return {"extract_ok": False, "extract_note": f"PDFを開けない: {e}"}
    else:
        t = norm(data_or_text)

    if len(t) < 50:
        return {"extract_ok": False, "extract_note": "本文が取れない（画像PDFの可能性）"}

    shares = _int(_first(RE_SHARES, t))
    amount = _amount(t)
    pct = _first(RE_PCT, t)
    pm = RE_PERIOD.search(t) or RE_PERIOD2.search(t)
    pfrom, pto = parse_period(pm.group(1) if pm else "")
    # ToSTNeT-3 は単日の買付。開始だけ取れたら同じ日を終了にする
    if pfrom and not pto and re.search(r"立会外買付|ToSTNeT", t, re.I):
        pto = pfrom
    method = extract_method(t)

    bd = buy_date(t, year_hint)
    out = {
        "shares_max": shares,
        "pct": float(pct) if pct else None,
        "amount_max": amount,
        "period_from": pfrom,
        "period_to": pto,
        "method": method,
    }
    if bd:
        out["buy_date"] = bd
        out["buy_date_src"] = "pdf"
    got = [k for k in ("shares_max", "pct", "amount_max", "period_from") if out[k] is not None]
    out["extract_ok"] = len(got) >= 2
    if not out["extract_ok"]:
        out["extract_note"] = f"主要項目が取れない（取れたのは{got or 'なし'}）"
    else:
        miss = [k for k in ("shares_max", "pct", "amount_max", "period_from", "period_to")
                if out[k] is None]
        out["extract_note"] = ("欠け: " + ",".join(miss)) if miss else ""
    return out
