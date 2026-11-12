# -*- coding: utf-8 -*-
"""業績予想の修正・配当予想の修正の「修正前 → 修正後」を XBRL から読み、向きを記録する
   → docs/fc_revision/YYYY-MM.json（開示 id ごとの記録・消さない）
   → docs/tdnet_hist/YYYY-MM-DD.json と docs/tdnet_list.json の該当する行に「rv」を足す（足すだけ）

起動文: kaburadar\\_報告_業績修正の向き.md（2026-11-12）
使い元: サイトの「今日の上方修正・増配 一覧」（/shusei/YYYY-MM-DD.html）。11/12 は向きの分からない修正が121社あった。

■ どの開示に XBRL があるか（2026-11-12 の実物で確認）
  TDnet の一覧の XBRL 欄の zip 名は、決算短信が「0812＋id の後ろ」、業績予想・配当予想の修正が「0912＋id の後ろ」。
  0912 の zip の中身は tse-rvfc-*-ixbrl.htm（業績予想の修正）か tse-rvdf-*-ixbrl.htm（配当予想の修正）。
  ただし **修正の開示に XBRL が付くのは一部だけ**（11/12: 表題が業績修正の124件のうち23件。10/30〜11/11 も2〜5割）。
  XBRL の無い開示（PDF だけ）は読まない＝向きは「不明」のまま。PDF の文字起こしはしない。

■ 要素（tse-ed-t: の名前は決算短信のサマリーと同じ。読み方は kessan_flash.facts_html を使う）
  売上高 NetSales / OperatingRevenues / OrdinaryRevenuesBK（銀行）/ *IFRS / *US
  営業利益 OperatingIncome(IFRS/US)・経常利益 OrdinaryIncome（IFRS は ProfitBeforeTaxIFRS）
  純利益 ProfitAttributableToOwnersOfParent(IFRS)（連結）/ NetIncome・ProfitIFRS（個別）
  配当 DividendPerShare（年間は AnnualMember、無ければ期末 YearEndMember）
  文脈: {期}Duration_{Consolidated|NonConsolidated}Member_{PreviousMember=修正前|CurrentMember=修正後}_{ForecastMember|LowerMember|UpperMember}
    期は CurrentYear（通期）が基本。中間期だけの修正は CurrentAccumulatedQ2。レンジ予想は Lower/Upper。

■ 向きの決め方（推定しない・数字は書類のまま）
  主の利益＝営業利益（無ければ経常利益→純利益）。修正後 > 修正前 なら上方、< なら下方。
  主の利益が修正前と同じで、ほかの利益が変わったときは次の利益で決める（特別損失で純利益だけ下げる修正など）。
  ほかの項目の向きが主の利益と割れたら「まちまち」として mix に「売上高は下方」のように残す（dir は主の利益の向き）。
  レンジ予想は「修正後の下限 > 修正前の上限」で上方、「修正後の上限 < 修正前の下限」で下方。重なれば決めない。
  修正前の数字が無い（新しく公表した予想・連結への移行など）は dir を空にし、why に理由を書く。
  配当: 年間配当の会社予想の修正前と修正後を比べて 増配／減配／据置。

使い方:
  python -X utf8 fc_revision.py                  # docs/tdnet_list.json にある日（直近10営業日）
  python -X utf8 fc_revision.py 2026-11-12       # 日付を絞る
  python -X utf8 fc_revision.py --backfill 30    # 直近30営業日。手元の一覧に無い日は TDnet の一覧を取り直す
                                                 #（TDnet は約1か月で消える。2026-11-12 時点で 10/5 まで残っていた）
"""
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from kessan_flash import JST, facts_html, mil

HERE = Path(__file__).parent
DOCS = HERE / "docs"
LIST = DOCS / "tdnet_list.json"
HIST = DOCS / "tdnet_hist"
OUT = DOCS / "fc_revision"
BASE = "https://www.release.tdnet.info/inbs/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
SLEEP = 1.0
RV_ZIP = re.compile(r"/0912\d+\.zip$")      # 修正の XBRL（0812 は決算短信）

ITEMS = [  # (キー, 表示名, 要素名の候補)
    ("sales", "売上高", ["NetSales", "OperatingRevenues", "OrdinaryRevenuesBK", "OrdinaryRevenuesIN",
                         "NetSalesIFRS", "SalesIFRS", "RevenueIFRS", "OperatingRevenuesIFRS",
                         "NetSalesUS", "OperatingRevenuesUS", "RevenuesUS"]),
    ("op", "営業利益", ["OperatingIncome", "OperatingIncomeIFRS", "OperatingIncomeUS"]),
    ("ord", "経常利益", ["OrdinaryIncome", "ProfitBeforeTaxIFRS", "IncomeBeforeIncomeTaxesUS"]),
    ("np", "純利益", ["ProfitAttributableToOwnersOfParent", "ProfitAttributableToOwnersOfParentIFRS",
                      "NetIncomeUS", "NetIncome", "ProfitIFRS"]),
]
LABEL = {k: lb for k, lb, _ in ITEMS}
MAIN = ("op", "ord", "np")                     # 主の利益の順
PERIODS = [("CurrentYear", "通期"), ("NextYear", "来期"), ("CurrentAccumulatedQ2", "中間期"),
           ("CurrentAccumulatedQ3", "第3四半期累計"), ("CurrentAccumulatedQ1", "第1四半期累計")]
MEMS = [("ConsolidatedMember", True), ("NonConsolidatedMember", False)]


# ---------------------------------------------------------------- 読み取り
def ixbrl_files(zip_bytes):
    """zip → [(様式, 本文)]。様式は rvfc（業績予想の修正）/ rvdf（配当予想の修正）"""
    z = zipfile.ZipFile(io.BytesIO(zip_bytes))
    out = []
    for n in z.namelist():
        if n.endswith("-ixbrl.htm"):
            kind = n.split("/")[-1].split("-")[1]
            out.append((kind, z.read(n).decode("utf-8", "replace")))
    return out


def _val(fs, names, base, money=True):
    """(下限, 上限) か None。1つの数字は (v, v)。money=False は円（配当）"""
    conv = mil if money else (lambda f: None if not f or f["shown"] is None
                              else round(f["shown"] * (10 ** (f["scale"] or 0)), 2))
    for nm in names:
        v = conv(fs.get((nm, base + "_ForecastMember")))
        if v is not None:
            return (v, v)
        lo, hi = conv(fs.get((nm, base + "_LowerMember"))), conv(fs.get((nm, base + "_UpperMember")))
        if lo is not None or hi is not None:
            return (lo, hi)
    return None


def _show(v):
    """記録の形: 1つの数字はそのまま、レンジは [下限, 上限]"""
    if v is None:
        return None
    return v[0] if v[0] == v[1] else [v[0], v[1]]


def direction(prev, cur):
    """(下限, 上限) 同士 → 上/下/同/None（重なる・欠けている）"""
    if prev is None or cur is None:
        return None
    if prev == cur:
        return "同"
    if cur[0] is not None and prev[1] is not None and cur[0] > prev[1]:
        return "上"
    if cur[1] is not None and prev[0] is not None and cur[1] < prev[0]:
        return "下"
    if prev[0] == prev[1] and cur[0] == cur[1] and None not in (prev[0], cur[0]):
        return "上" if cur[0] > prev[0] else "下" if cur[0] < prev[0] else "同"
    return None


def decide(prev, cur):
    """{項目: (lo,hi)} 2つ → (dir, by, mix)。dir は 上方/下方/据置/None"""
    ds = {k: direction(prev.get(k), cur.get(k)) for k, _, _ in ITEMS}
    by = None
    for k in MAIN:                        # 主の利益: 営業 → 経常 → 純利益。同じなら次へ
        if ds[k] in ("上", "下"):
            by = k
            break
    if by is None:
        same = [k for k in MAIN if ds[k] == "同"]
        if ds["sales"] in ("上", "下") and (same or not any(prev.get(k) or cur.get(k) for k in MAIN)):
            # 利益は据え置きで売上高だけ修正（2026-11-12 23230 の「上方修正」）・利益の予想を出していない会社
            d = ds["sales"]
            return ("上方" if d == "上" else "下方"), "sales", ("利益は据置" if same else None)
        if same:
            return "据置", same[0], None
        return None, None, None
    d = ds[by]
    other = [f"{LABEL[k]}は{'上方' if ds[k] == '上' else '下方'}" for k, _, _ in ITEMS
             if k != by and ds[k] in ("上", "下") and ds[k] != d]
    return ("上方" if d == "上" else "下方"), by, ("・".join(other) or None)


def parse_fc(fs):
    """rvfc の facts → 記録（取れなければ None）"""
    for per, plabel in PERIODS:
        for mem, cons in MEMS:
            b = f"{per}Duration_{mem}"
            prev = {k: _val(fs, names, b + "_PreviousMember") for k, _, names in ITEMS}
            cur = {k: _val(fs, names, b + "_CurrentMember") for k, _, names in ITEMS}
            if not any(cur.values()) and not any(prev.values()):
                continue
            if not any(prev[k] and cur[k] for k in prev):
                # 修正前が無い＝新しく出した予想（連結への移行など）。個別の修正が別にあればそちらを見る
                continue
            dr, by, mix = decide(prev, cur)
            rec = {"per": plabel, "cons": cons,
                   "prev": {k: _show(v) for k, v in prev.items() if v},
                   "cur": {k: _show(v) for k, v in cur.items() if v},
                   "dir": dr, "by": LABEL.get(by) if by else None, "mix": mix}
            if mix and re.search(r"は(上方|下方)", mix):
                rec["mixed"] = True        # 「まちまち」（dir は主の利益の向き）
            if dr is None:
                rec["why"] = "修正前と修正後の幅が重なる・項目がそろわない"
            return rec
    # 修正前の数字がどこにも無い
    if any(nm == "NetSales" or nm.startswith(("Operating", "Ordinary", "Profit", "NetIncome")) for nm, _ in fs):
        return {"dir": None, "why": "修正前の数字が無い（新しく公表した予想）"}
    return None


def parse_div(fs):
    for mem, _ in reversed(MEMS):          # 配当はふつう個別（NonConsolidated）
        for part in ("AnnualMember", "YearEndMember"):
            b = f"CurrentYearDuration_{part}_{mem}"
            p = _val(fs, ["DividendPerShare"], b + "_PreviousMember", money=False)
            c = _val(fs, ["DividendPerShare"], b + "_CurrentMember", money=False)
            if p and c:
                d = direction(p, c)
                return {"part": "年間" if part == "AnnualMember" else "期末", "prev": _show(p), "cur": _show(c),
                        "dir": {"上": "増配", "下": "減配", "同": "据置"}.get(d)}
    return None


def parse(zip_bytes):
    """修正の XBRL（0912 の zip）→ 記録。{k, per, cons, prev, cur, dir, by, mix, div}"""
    rec = {}
    kinds = []
    for kind, html in ixbrl_files(zip_bytes):
        kinds.append(kind)
        fs = facts_html(html)
        fc = parse_fc(fs)
        if fc and (not rec.get("prev") or fc.get("prev")):
            rec.update(fc)
        dv = parse_div(fs)
        if dv:
            rec["div"] = dv
    if not kinds:
        return None
    rec["k"] = "+".join(sorted(set(kinds)))
    if "prev" not in rec and "div" in rec:
        rec.pop("why", None)               # 配当だけの修正
        rec.pop("dir", None)
    return rec


# ---------------------------------------------------------------- 取得と記録
def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read()


def load_month(m):
    f = OUT / f"{m}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8")).get("items") or {}
        except Exception as e:
            print(f"  {f.name} が読めない（作り直す）: {e}", file=sys.stderr)
    return {}


def save_months(store):
    OUT.mkdir(parents=True, exist_ok=True)
    for m, items in sorted(store.items()):
        (OUT / f"{m}.json").write_text(json.dumps(
            {"updated": datetime.now(JST).isoformat(timespec="seconds"),
             "note": "TDnet の業績予想・配当予想の修正の開示のうち XBRL があるものの修正前・修正後（百万円・配当は円）。"
                     "XBRL の無い開示は載らない。dir=主の利益（営業→経常→純利益）の向き、mix=向きの割れた項目",
             "items": items}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    months = sorted(p.stem for p in OUT.glob("????-??.json"))
    (OUT / "index.json").write_text(json.dumps(
        {"updated": datetime.now(JST).isoformat(timespec="seconds"), "months": months},
        ensure_ascii=False), encoding="utf-8")


def rv_of(rec):
    """行に足す形（日付・社名など行にあるものは省く）"""
    return {k: v for k, v in rec.items() if k not in ("d", "c", "n", "t", "ti", "id")}


def collect(day, store, fetched):
    """その日の一覧（tdnet_list の形）の 0912 の zip を読む。読んだことのある id は読み直さない"""
    n_new = 0
    for it in day.get("items") or []:
        url = it.get("xbrl_url") or ""
        if not RV_ZIP.search(url):
            continue
        m = day["d"][:7]
        box = store.setdefault(m, load_month(m))
        if it["id"] in box and not box[it["id"]].get("err"):
            continue
        try:
            rec = parse(get(url)) or {"err": "ixbrl が無い"}
        except Exception as e:
            rec = {"err": str(e)[:80]}
        time.sleep(SLEEP)
        fetched[0] += 1
        n_new += 1
        box[it["id"]] = {"d": day["d"], "c": it.get("code"), "n": it.get("name"), "t": it.get("time"),
                         "ti": it.get("title"), "id": it["id"], **rec}
    return n_new


def add_to_rows(store):
    """tdnet_hist の各日・tdnet_list.json の行に rv を足す（列を1つ足すだけ。無ければ今までどおり）"""
    by_id = {i: r for items in store.values() for i, r in items.items() if not r.get("err")}
    for f in sorted(HIST.glob("????-??-??.json")) if HIST.exists() else []:
        if f.stem[:7] not in store:
            continue
        j = json.loads(f.read_text(encoding="utf-8"))
        cols = j.get("cols") or []
        if "id" not in cols:
            continue
        i_id = cols.index("id")
        if "rv" not in cols:
            cols.append("rv")
            j["cols"] = cols
        i_rv = cols.index("rv")
        hit = 0
        for r in j.get("rows") or []:
            while len(r) < len(cols):
                r.append(None)
            rec = by_id.get(r[i_id])
            r[i_rv] = rv_of(rec) if rec else None
            hit += bool(rec)
        f.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if LIST.exists():
        j = json.loads(LIST.read_text(encoding="utf-8"))
        for day in j.get("days") or []:
            for it in day.get("items") or []:
                rec = by_id.get(it.get("id"))
                if rec:
                    it["rv"] = rv_of(rec)
                else:
                    it.pop("rv", None)
        LIST.write_text(json.dumps(j, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def bizdays_back(n, today):
    from jp_bizday import is_bizday
    out, d = [], today
    while len(out) < n:
        if is_bizday(d):
            out.append(d.isoformat())
        d -= timedelta(days=1)
    return sorted(out)


def main(argv):
    backfill = None
    if "--backfill" in argv:
        i = argv.index("--backfill")
        backfill = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    dates = [a for a in argv if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    days = {}
    if LIST.exists():
        for day in json.loads(LIST.read_text(encoding="utf-8")).get("days") or []:
            days[day["d"]] = day
    want = dates or sorted(days)
    if backfill:
        want = sorted(set(want) | set(bizdays_back(backfill, datetime.now(JST).date())))
    store, fetched = {}, [0]
    for d in want:
        day = days.get(d)
        if day is None and backfill:
            import tdnet_list
            day = tdnet_list.fetch_day(d)          # 手元に無い古い日は TDnet の一覧を取り直す（消えていれば None）
            time.sleep(SLEEP)
        if not day:
            print(f"  {d}: 一覧なし（TDnet から消えた日・休日）")
            continue
        n = collect(day, store, fetched)
        box = [r for r in store.get(d[:7], {}).values() if r["d"] == d]
        print(f"  {d}: 修正の XBRL {len(box)}件（今回読んだ {n}件）・向き "
              + " ".join(f"{k}{sum(1 for r in box if r.get('dir') == k)}" for k in ("上方", "下方", "据置"))
              + f"・配当 {sum(1 for r in box if r.get('div'))}件")
    if not store:
        print("対象の日が無い")
        return 0
    save_months(store)
    add_to_rows(store)
    print(f"→ docs/fc_revision/: {', '.join(sorted(store))}（今回 TDnet から {fetched[0]}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
