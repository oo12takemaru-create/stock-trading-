# -*- coding: utf-8 -*-
"""有価証券報告書の「主要な経営指標等の推移」（5期）→ docs/yuho/latest.json（銘柄カルテの「業績の5期」の元）

起動文: kaburadar\\起動文_実務_業績5期の収集_2026-11-10.md
先にある仕組み: edinet_eps.py（同じ有報から EPS だけを読み、PER の過去の幅を作る）

■ 出所
  EDINET API v2（edinet_daily.py と同じキー・同じ取り方・書類ごとに1.5秒あける）。
  有価証券報告書（府令 010・docTypeCode 120）の XBRL（type=1 の zip の XBRL/PublicDoc/）のうち、
  「主要な経営指標等の推移」の行（要素名の末尾が SummaryOfBusinessResults / KeyFinancialData）だけを読む。PDF は読まない。
  1通で5期（CurrentYear と Prior1〜4Year）そろう。連結があれば連結、無ければ単体（文脈の末尾 _NonConsolidatedMember）。

■ どの有報を読むか
  edinet_eps.py が毎朝作る docs/eps/_state.json の cand（銘柄ごとの直近の有報・400日ぶんの書類一覧から）をそのまま使う。
  書類一覧を二重に取りに行かない。workflow は edinet-eps.yml の中で edinet_eps.py の後に走らせる（新しい cron は足さない）。
  まだ読んでいない（または新しい有報が出た）銘柄を、提出日の新しい順に読む。初回の遡及は1回40分（約750社）×約5日（1件 約3秒）。

■ 読む行（値は書類のまま。金額は円・比率は書類の小数のまま＝0.081 が 8.1%）
  rev   売上高など（決算期の一番上の行）
          日本基準 NetSales（売上高）/ OperatingRevenue1・2（営業収益）/ OrdinaryIncome（経常収益・銀行や保険）
          IFRS     RevenueIFRS（売上収益）  米国基準 RevenuesUSGAAP（収益）
          どれも無ければ、会社が独自に足した行（例 トヨタ OperatingRevenuesIFRSKeyFinancialData「営業収益」）から
          名前に Revenue/Sales を含むものを、表示の順（_pre.xml）の先頭から1つ。呼び名は会社のラベル（_lab.xml）
  op    営業利益（主要な経営指標に無いことが多い。会社が独自に足した行にあるときだけ。無ければ出さない）
  ord   経常利益（日本基準） / pbt 税引前利益（IFRS・米国基準。経常利益の行が無いため）
  ni    親会社株主に帰属する当期純利益（単体は当期純利益）
  eps   1株当たり当期純利益（有報の記載のまま。株式分割は直していない。直した値は eps_hist.json）
  bps   1株当たり純資産  na 純資産（IFRS・米国基準は親会社の所有者に帰属する持分）  ta 総資産
  eqr   自己資本比率  roe 自己資本利益率  div 1株当たり配当額（提出会社の単体の行）
  期ごとに std（会計基準）を持つ。IFRS へ移った会社は、移る前の期を日本基準の行で埋める（有報の表のとおり）。

■ 出力
  docs/yuho/_state.json  銘柄ごとに読んだ docID（数字が取れなかった書類も入れて、毎日読み直さない）
  docs/yuho/latest.json  公開用。codes: 銘柄コード → {doc, sub（提出日）, name, pe（決算期末）, std, cons, rev_l, fy:[期…古い順]}
                         期: {s, e, std, rev, rev_l?, op?, ord?, pbt?, ni?, eps?, bps?, na?, ta?, eqr?, roe?, div?}（無い値は書かない）
  公共データ利用規約（PDL1.0）＝出典（EDINET）を書けば加工・商用可。

使い方:
  python -X utf8 edinet_yuho.py --max-minutes 40     # 毎朝（edinet_eps.py の後）
  python -X utf8 edinet_yuho.py --code 7203 --code 2809   # この銘柄だけ読む（確認用）
"""
import argparse
import io
import json
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edinet_daily import API, api_key, get, load_json, log   # noqa: E402

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
CAND = DOCS / "eps" / "_state.json"     # edinet_eps.py が作る（銘柄ごとの直近の有報）
DIR = DOCS / "yuho"
STATE = DIR / "_state.json"
OUT = DIR / "latest.json"
WAIT = 1.5

XI = "{http://www.xbrl.org/2003/instance}"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
CTX = re.compile(r"^(Current|Prior([1-4]))Year(Duration|Instant)(_NonConsolidatedMember)?$")
STDS = ("IFRS", "USGAAP", "JP")

# jpcrp_cor の要素名（SummaryOfBusinessResults を除いた部分）→ (項目, 基準)
_STD = {
    "NetSales": ("rev", "JP"), "OperatingRevenue1": ("rev", "JP"), "OperatingRevenue2": ("rev", "JP"),
    "OrdinaryIncome": ("rev", "JP"),
    "RevenueIFRS": ("rev", "IFRS"), "RevenuesUSGAAP": ("rev", "USGAAP"),
    "OrdinaryIncomeLoss": ("ord", "JP"),
    "ProfitLossBeforeTaxIFRS": ("pbt", "IFRS"), "ProfitLossBeforeTaxUSGAAP": ("pbt", "USGAAP"),
    "ProfitLossAttributableToOwnersOfParent": ("ni", "JP"),
    "NetIncomeLossAttributableToOwnersOfParent": ("ni", "JP"),
    "ProfitLossAttributableToOwnersOfParentIFRS": ("ni", "IFRS"),
    "NetIncomeLossAttributableToOwnersOfParentUSGAAP": ("ni", "USGAAP"),
    "BasicEarningsLossPerShare": ("eps", "JP"), "BasicEarningsLossPerShareIFRS": ("eps", "IFRS"),
    "BasicEarningsLossPerShareUSGAAP": ("eps", "USGAAP"),
    # IFRS の1株当たり親会社所有者帰属持分は、名前に反して EquityToAssetRatioIFRS（単位 JPYPerShares・トヨタで確認）。
    # 比率のほうは RatioOfOwnersEquityToGrossAssetsIFRS
    "NetAssetsPerShare": ("bps", "JP"), "EquityToAssetRatioIFRS": ("bps", "IFRS"),
    "EquityAttributableToOwnersOfParentPerShareUSGAAP": ("bps", "USGAAP"),
    "NetAssets": ("na", "JP"), "EquityAttributableToOwnersOfParentIFRS": ("na", "IFRS"),
    "EquityAttributableToOwnersOfParentUSGAAP": ("na", "USGAAP"),
    "TotalAssets": ("ta", "JP"), "TotalAssetsIFRS": ("ta", "IFRS"), "TotalAssetsUSGAAP": ("ta", "USGAAP"),
    "EquityToAssetRatio": ("eqr", "JP"), "RatioOfOwnersEquityToGrossAssetsIFRS": ("eqr", "IFRS"),
    "EquityToAssetRatioUSGAAP": ("eqr", "USGAAP"),
    "RateOfReturnOnEquity": ("roe", "JP"), "RateOfReturnOnEquityIFRS": ("roe", "IFRS"),
    "RateOfReturnOnEquityUSGAAP": ("roe", "USGAAP"),
    "DividendPaidPerShare": ("div", "JP"),
}
# 単体だけの会社の純利益（連結の「親会社株主に帰属する」が無い）
_SOLO_NI = {"NetIncomeLoss", "ProfitLoss"}
REV_LABEL = {"NetSales": "売上高", "OperatingRevenue1": "営業収益", "OperatingRevenue2": "営業収益",
             "OrdinaryIncome": "経常収益", "RevenueIFRS": "売上収益", "RevenuesUSGAAP": "収益"}
SUFFIX = re.compile(r"^(.+?)(SummaryOfBusinessResults|KeyFinancialData)$")
EXT_REV = re.compile(r"Revenue|Sales")
EXT_OP = re.compile(r"OperatingIncome|OperatingProfit")
EXT_NG = re.compile(r"NonOperating|Ordinary|Cost|Expense|PerShare|Ratio|Rate|Margin|Employee|Worker|Intersegment|Gross|Growth|Change")
FIELDS = ("rev", "op", "ord", "pbt", "ni", "eps", "bps", "na", "ta", "eqr", "roe", "div")
INT_FIELDS = {"rev", "op", "ord", "pbt", "ni", "na", "ta"}


def code4(sec):
    s = (sec or "").strip()
    return s[:4] if len(s) == 5 else (s or None)


# ─────────────────────────────────────────────
#  XBRL の読み取り
# ─────────────────────────────────────────────
def labels_of(lab_xml):
    """会社の _lab.xml → {要素名: 日本語の標準ラベル}（会社が独自に足した要素だけが載っている）"""
    out = {}
    if not lab_xml:
        return out
    t = lab_xml.decode("utf-8", "replace") if isinstance(lab_xml, bytes) else lab_xml
    # loc（要素）→ arc → label（文言）。label の xlink:label は会社によって形が違うので arc でつなぐ
    loc = dict((m.group(2), m.group(1)) for m in re.finditer(
        r'<link:loc[^>]*xlink:href="[^"#]*#([^"]+)"[^>]*xlink:label="([^"]+)"', t))
    loc.update((m.group(1), m.group(2)) for m in re.finditer(
        r'<link:loc[^>]*xlink:label="([^"]+)"[^>]*xlink:href="[^"#]*#([^"]+)"', t))
    arc = {}
    for m in re.finditer(r"<link:labelArc[^>]*>", t):
        a = m.group(0)
        f = re.search(r'xlink:from="([^"]+)"', a)
        to = re.search(r'xlink:to="([^"]+)"', a)
        if f and to:
            arc.setdefault(to.group(1), f.group(1))
    for m in re.finditer(r"<link:label\b([^>]*)>([^<]*)</link:label>", t):
        attrs, txt = m.group(1), m.group(2).strip()
        role = re.search(r'xlink:role="([^"]+)"', attrs)
        lid = re.search(r'xlink:label="([^"]+)"', attrs)
        if not (role and lid and txt) or not role.group(1).endswith("/role/label"):
            continue
        el = loc.get(arc.get(lid.group(1), ""), "")
        name = el.split("_", 1)[-1] if el.startswith("jpcrp_cor_") else re.sub(r"^.*?_E\d{5}-\d{3}_", "", el)
        if name:
            out.setdefault(name, txt)
    return out


def order_of(pre_xml):
    """会社の _pre.xml → 要素名の表示順（主要な経営指標等の推移の行の並び）"""
    if not pre_xml:
        return []
    t = pre_xml.decode("utf-8", "replace") if isinstance(pre_xml, bytes) else pre_xml
    out = []
    for m in re.finditer(r'xlink:href="[^"#]*#([^"]+)"', t):
        el = m.group(1)
        name = el.split("_", 1)[-1] if el.startswith("jpcrp_cor_") else re.sub(r"^.*?_E\d{5}-\d{3}_", "", el)
        if name not in out:
            out.append(name)
    return out


def parse_xbrl(xb, lab=None, pre=None):
    """→ {"std", "cons", "rev_l", "fy": [期…古い順]} か None（主要な経営指標の行が読めない）"""
    root = ET.fromstring(xb)
    ctx = {}
    for c in root.iter(XI + "context"):
        cid = c.get("id") or ""
        m = CTX.match(cid)
        if not m:
            continue
        st, en, ins = c.find(".//" + XI + "startDate"), c.find(".//" + XI + "endDate"), c.find(".//" + XI + "instant")
        if m.group(3) == "Duration" and st is not None and en is not None:
            ctx[cid] = (st.text.strip(), en.text.strip())
        elif m.group(3) == "Instant" and ins is not None:
            ctx[cid] = (None, ins.text.strip())

    vals = {}        # (項目, 基準, 単体か, 期の番号) -> 値
    ext = {}         # 会社が独自に足した行: (要素名, 単体か, 期の番号) -> 値
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str) or "}" not in tag:
            continue
        ns, name = tag[1:].split("}", 1)
        m = CTX.match(el.get("contextRef") or "")
        if not m or el.get(XSI_NIL) == "true" or not (el.text or "").strip():
            continue
        sm = SUFFIX.match(name)
        if not sm:
            continue
        try:
            v = float(el.text.strip())
        except ValueError:
            continue
        k = 0 if m.group(1) == "Current" else int(m.group(2))
        solo = bool(m.group(4))
        base = sm.group(1)
        if ns.endswith("/jpcrp_cor"):
            if sm.group(2) != "SummaryOfBusinessResults":
                continue
            if base in _STD:
                f, std = _STD[base]
                key = (f, std, solo, k)
                if f == "rev":
                    vals.setdefault(("rev_l", std, solo, k), REV_LABEL[base])
                vals.setdefault(key, v)
            elif base in _SOLO_NI:
                vals.setdefault(("ni_solo", "JP", solo, k), v)
        elif "jpcrp" in ns:      # 会社の名前空間（jpcrp030000-asr_E02144-000 など）
            ext.setdefault((name, solo, k), v)

    # 単体の純利益（NetIncomeLoss）は、連結の「親会社株主に帰属する」が無いときだけ使う
    for (f, std, solo, k), v in list(vals.items()):
        if f == "ni_solo" and ("ni", "JP", solo, k) not in vals:
            vals[("ni", "JP", solo, k)] = v

    # 会社が独自に足した行（売上・営業利益）。基準は名前の IFRS/USGAAP から
    order = order_of(pre)
    labels = labels_of(lab)

    def ext_std(n):
        return "IFRS" if "IFRS" in n else ("USGAAP" if "USGAAP" in n else "JP")

    names = sorted({n for (n, _, _) in ext}, key=lambda n: (order.index(n) if n in order else 9999, n))
    rev_c = [n for n in names if EXT_REV.search(n) and not EXT_NG.search(n) and not EXT_OP.search(n)]
    op_c = [n for n in names if EXT_OP.search(n) and not EXT_NG.search(n)]
    for (n, solo, k), v in ext.items():
        std = ext_std(n)
        if rev_c and n == rev_c[0] and ("rev", std, solo, k) not in vals:
            vals[("rev", std, solo, k)] = v
            vals[("rev_l", std, solo, k)] = labels.get(n) or "売上高等"
        if op_c and n == op_c[0]:
            vals.setdefault(("op", std, solo, k), v)

    def has(solo):
        return any((f, s, solo, 0) in vals for f in ("rev", "ni", "eps") for s in STDS)

    if has(False):
        solo = False
    elif has(True):
        solo = True
    else:
        return None
    # 当期に値がある基準を主にする（IFRS → 米国基準 → 日本基準の順に探す）
    std = next((s for s in STDS if any((f, s, solo, 0) in vals for f in ("rev", "ni", "eps"))), "JP")
    order_std = [std] + [s for s in STDS if s != std]
    sfx = "_NonConsolidatedMember" if solo else ""

    fy = []
    for k in range(4, -1, -1):
        p = "Current" if k == 0 else f"Prior{k}"
        dur = ctx.get(f"{p}YearDuration{sfx}")
        if not dur:
            continue
        row = {"s": dur[0], "e": dur[1]}
        # 期の基準: 売上・純利益・EPS のどれかがある基準のうち、主の基準を優先
        pstd = next((s for s in order_std if any((f, s, solo, k) in vals for f in ("rev", "ni", "eps", "pbt"))), None)
        if pstd is None:
            continue
        row["std"] = pstd
        for f in FIELDS:
            if f == "div":
                # 配当は提出会社（単体）の行。連結の会社でも単体の文脈にある
                v = vals.get(("div", "JP", solo, k), vals.get(("div", "JP", True, k)))
            else:
                v = vals.get((f, pstd, solo, k))
            if v is None:
                continue
            row[f] = int(round(v)) if f in INT_FIELDS else v
            if f == "rev":
                row["rev_l"] = vals.get(("rev_l", pstd, solo, k))
        fy.append(row)
    if not fy or not any(("rev" in r or "ni" in r) for r in fy):
        return None
    rev_l = next((r["rev_l"] for r in reversed(fy) if r.get("rev_l")), None)
    for r in fy:
        if r.get("rev_l") == rev_l:
            r.pop("rev_l", None)
    return {"std": fy[-1]["std"], "cons": not solo, "rev_l": rev_l, "fy": fy}


def fetch_doc(doc_id, key):
    raw = get(f"{API}/documents/{doc_id}?type=1", key)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    pub = [n for n in zf.namelist() if n.startswith("XBRL/PublicDoc/")]
    xb = sorted([n for n in pub if n.endswith(".xbrl")], key=lambda n: ("jpcrp" not in n, n))
    if not xb or "jpcrp" not in xb[0]:
        return None
    lab = next((n for n in pub if n.endswith("_lab.xml")), None)
    pre = next((n for n in pub if n.endswith("_pre.xml")), None)
    return parse_xbrl(zf.read(xb[0]), zf.read(lab) if lab else None, zf.read(pre) if pre else None)


# ─────────────────────────────────────────────
#  保存（1銘柄1行。毎日の差分を読みやすくする）
# ─────────────────────────────────────────────
def dump(obj, p):
    p.parent.mkdir(parents=True, exist_ok=True)
    codes = obj.pop("codes")
    head = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))[:-1]
    lines = [f'{json.dumps(c)}:{json.dumps(codes[c], ensure_ascii=False, separators=(",", ":"))}'
             for c in sorted(codes)]
    body = head + ',"codes":{\n' + ",\n".join(lines) + "\n}}\n"
    obj["codes"] = codes
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)


def save(codes, read):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"read": dict(sorted(read.items()))}, ensure_ascii=False,
                              separators=(",", ":")), encoding="utf-8")
    tmp.replace(STATE)
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "EDINET（金融庁）有価証券報告書の「主要な経営指標等の推移」",
        "note": ("直近の有価証券報告書に載っている5期分を、書類の値のまま載せた（金額は円・比率は小数＝0.081 が 8.1%）。"
                 "連結があれば連結、無ければ単体。EPS は有報の記載のまま（株式分割は直していない。直した値は eps_hist.json）。"
                 "営業利益は主要な経営指標に無い会社が多く、会社が独自に載せているときだけ op に入れた。"
                 "IFRS・米国基準は経常利益の行が無いので、税引前利益を pbt に入れた。"
                 "出典：EDINET（https://disclosure2.edinet-fsa.go.jp/）を加工して作成"),
        "count": len(codes),
        "codes": codes,
    }
    dump(out, OUT)


# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-minutes", type=float, default=0, help="有報の読み込みをこの分数で打ち切る")
    ap.add_argument("--code", action="append", help="この銘柄だけ読む（確認用・読み済みでも読み直す）")
    args = ap.parse_args()
    t0 = time.time()
    cand = (load_json(CAND, {}) or {}).get("cand") or {}
    if not cand:
        log("::warning::docs/eps/_state.json の cand が空です（edinet_eps.py が先に走っていない）")
        return
    st = load_json(STATE, {}) or {}
    read = st.get("read") or {}
    old = load_json(OUT, {}) or {}
    codes = old.get("codes") or {}

    todo = [c for c, v in cand.items() if read.get(c) != v[0]]
    if args.code:
        todo = [c for c in args.code if c in cand]
    todo.sort(key=lambda c: cand[c][1], reverse=True)
    log(f"有報の候補 {len(cand)}社 / 読み済み {len(read)}社 / これから {len(todo)}社")
    key = api_key()
    got = none = fail = 0
    for i, c in enumerate(todo):
        if args.max_minutes and (time.time() - t0) / 60 > args.max_minutes:
            log(f"  {args.max_minutes}分に達したので打ち切り（残り {len(todo) - i}社）")
            break
        doc, sd, nm, pe = cand[c]
        try:
            p = fetch_doc(doc, key)
        except Exception as e:
            log(f"  {c} {doc} 失敗: {e}")
            fail += 1
            time.sleep(WAIT)
            continue
        time.sleep(WAIT)
        read[c] = doc
        if not p:
            none += 1        # 数字が取れない書類は、前の有報の値があればそのまま残す
            continue
        codes[c] = {"doc": doc, "sub": sd, "name": nm, "pe": pe, **p}
        got += 1
        if got % 100 == 0:
            save(codes, read)
    save(codes, read)
    log(f"有報: {got}社を読んだ（数字なし {none}・失敗 {fail}）→ latest.json {len(codes)}社／所要 {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
