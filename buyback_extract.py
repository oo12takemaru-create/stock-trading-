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
RE_METHOD = re.compile(r"取得方法[：:]?(.{0,60})")

# 2026年9月7日 / 2026年9月7日から / ～11月30日（年が省略される）
DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")


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


def extract(data_or_text):
    """PDFのバイト列（または本文文字列）から数値を抜く"""
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
    mm = RE_METHOD.search(t)
    method = None
    if mm:
        # 「取得方法：市場買付（※）〜」の※以降は注記なので落とす
        method = re.split(r"[(（]※|[(（]注|以上", mm.group(1))[0].strip("：:・ ")[:40] or None

    out = {
        "shares_max": shares,
        "pct": float(pct) if pct else None,
        "amount_max": amount,
        "period_from": pfrom,
        "period_to": pto,
        "method": method,
    }
    got = [k for k in ("shares_max", "pct", "amount_max", "period_from") if out[k] is not None]
    out["extract_ok"] = len(got) >= 2
    if not out["extract_ok"]:
        out["extract_note"] = f"主要項目が取れない（取れたのは{got or 'なし'}）"
    else:
        miss = [k for k in ("shares_max", "pct", "amount_max", "period_from", "period_to")
                if out[k] is None]
        out["extract_note"] = ("欠け: " + ",".join(miss)) if miss else ""
    return out
