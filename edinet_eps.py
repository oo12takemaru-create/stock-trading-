# -*- coding: utf-8 -*-
"""有価証券報告書の EPS（過去5期）と期末の株価 → docs/eps_hist.json（銘柄カルテの「PER の過去の幅」の元）

起動文: kaburadar\\起動文_実務_第2波_競合から取り込む_2026-10-29.md の担当F（計画の12）
調査: kaburadar\\_調査_配当データの出所.md §1-(c)（EDINET 有報 XBRL の取り方・分割の補正）

■ 出所
  EDINET API v2（edinet_daily.py と同じキー・同じ取り方）。有価証券報告書（ordinanceCode 010・docTypeCode 120）の
  XBRL（type=1 の zip 内 XBRL/PublicDoc/*.xbrl）の「主要な経営指標等の推移」だけを読む。
    1株当たり当期純利益   jpcrp_cor:BasicEarningsLossPerShare{,IFRS,USGAAP}SummaryOfBusinessResults
    親会社株主に帰属する当期純利益（分割の補正の確かめに使う）
                          jpcrp_cor:ProfitLossAttributableToOwnersOfParent{,IFRS,USGAAP}SummaryOfBusinessResults
                          （単体だけの会社は NetIncomeLossSummaryOfBusinessResults / ProfitLossSummaryOfBusinessResults）
  文脈は CurrentYearDuration（当期）と Prior1〜4YearDuration（過去4期）＝5期。連結を優先し、無ければ単体
  （文脈の末尾が _NonConsolidatedMember）。期の長さが330日未満（決算期の変更）は使わない。
  1社につき直近の有報を1本読めば5期そろう。公共データ利用規約（PDL1.0）＝出典を書けば加工・商用可。

■ 株式分割の補正（_調査_配当データの出所.md §1-(c) の注意）
  有報の5期の数字は「当時の株数のまま」の期と「分割後の株数に直した」期が混ざる（会社によって、
  直近2期だけ直して古い3期は当時のまま、など）。どちらかは書類からは機械的に読めないので、
  純利益 ÷ EPS ＝ 期中の平均株数 を期ごとに出し、その期の末日〜有報の提出日の間にあった分割
  （docs/splits.json の by_code・2016年〜）の比率 R と比べる:
    直近期の株数 ÷ その期の株数 が 1 に近い → 直してある（そのまま使う）
                                    R に近い → 当時のまま（EPS を R で割る）
  純利益が無い期で分割を挟むものは、どちらか決められないので使わない（推測で埋めない）。
  有報の提出日より後の分割は、全期を R で割って「今の株数」に直す。
  株価（yfinance の Close）は取得日の時点で分割を調整した値なので、取得日より後の分割は株価を R で割る。
  → PER（期末の株価 ÷ その期の EPS）はどちらも今の株数にそろえてから計算する。

■ 出力
  docs/eps/_state.json   書類一覧を見た日（scanned）と、銘柄ごとの直近の有報（cand: code -> [docID, 提出日, 社名]）
  docs/eps/records.json  読んだ有報の中身（code -> 5期の EPS・純利益・期末日、期末の株価と取得日）
  docs/eps_hist.json     公開用。今の株数に直した EPS と各期末の PER（サイトの gen_stocks.py が読む）

■ 株価
  期末の株価は yfinance の日足（内部計算用。表示するのは計算した PER だけで、株価の系列は配らない）。
  期末日がお休みならその前の最後の終値。取れない期は PER を空にする。

使い方:
  python -X utf8 edinet_eps.py                       # 毎朝: 前回の続き〜昨日の書類一覧を見て、新しい有報を読む
  python -X utf8 edinet_eps.py --backfill-days 400 --max-minutes 40
                                                     # 初回: 400日ぶんの一覧を見て、全社の直近の有報を少しずつ読む
  python -X utf8 edinet_eps.py --build-only          # 取得せず eps_hist.json だけ作り直す（分割の反映など）
  python -X utf8 edinet_eps.py --code 7203 --code 7974   # この銘柄だけ読む（確認用）
"""
import argparse
import io
import json
import math
import re
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edinet_daily import API, api_key, get, load_json, write_json, log   # noqa: E402

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
DIR = DOCS / "eps"
STATE = DIR / "_state.json"
RECS = DIR / "records.json"
OUT = DOCS / "eps_hist.json"
SPLITS = DOCS / "splits.json"
WAIT = 1.5                 # 書類ごとの間隔（秒）。edinet_daily.py と同じ
LIST_WAIT = 0.5            # 書類一覧の間隔
MIN_DAYS = 330             # これより短い期（決算期の変更）は使わない
NEAR_SPLIT = 7             # 分割の権利落ち日の前後この日数は「今の PER」を出さない（株価と EPS の株数がずれうる）

CTX = re.compile(r"^(Current|Prior([1-4]))YearDuration(_NonConsolidatedMember)?$")
EPS_RE = re.compile(r"^BasicEarningsLossPerShare(IFRS|USGAAP)?SummaryOfBusinessResults$")
NI_RE = re.compile(r"^(?:ProfitLossAttributableToOwnersOfParent|NetIncomeLossAttributableToOwnersOfParent)"
                   r"(IFRS|USGAAP)?SummaryOfBusinessResults$")
NI_SOLO = ("NetIncomeLossSummaryOfBusinessResults", "ProfitLossSummaryOfBusinessResults")
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def code4(sec):
    s = (sec or "").strip()
    return s[:4] if len(s) == 5 else (s or None)


# ─────────────────────────────────────────────
#  XBRL の読み取り
# ─────────────────────────────────────────────
def parse_xbrl(xb):
    """→ {"std": "JP|IFRS|USGAAP", "cons": bool, "per": [{"s","e","eps","ni"}...古い順]} か None"""
    root = ET.fromstring(xb)
    ctx = {}
    for c in root.iter("{http://www.xbrl.org/2003/instance}context"):
        cid = c.get("id") or ""
        if not CTX.match(cid):
            continue
        st = c.find(".//{http://www.xbrl.org/2003/instance}startDate")
        en = c.find(".//{http://www.xbrl.org/2003/instance}endDate")
        if st is not None and en is not None:
            ctx[cid] = (st.text.strip(), en.text.strip())
    vals = {}      # (種類, 基準, 連結/単体, 期の番号) -> 値
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str) or "}" not in tag:
            continue
        ns, name = tag[1:].split("}", 1)
        if "jpcrp" not in ns:        # jpcrp_cor の名前空間 URI は .../jpcrp/YYYY-MM-DD/jpcrp_cor
            continue
        m = CTX.match(el.get("contextRef") or "")
        if not m or el.get(XSI_NIL) == "true" or not (el.text or "").strip():
            continue
        k = 0 if m.group(1) == "Current" else int(m.group(2))
        solo = bool(m.group(3))
        me, mn = EPS_RE.match(name), NI_RE.match(name)
        try:
            v = float(el.text.strip())
        except ValueError:
            continue
        if me:
            vals.setdefault(("eps", me.group(1) or "JP", solo, k), v)
        elif mn:
            vals.setdefault(("ni", mn.group(1) or "JP", solo, k), v)
        elif name in NI_SOLO:
            vals.setdefault(("ni", "JP", solo, k), v)
    if not any(k[0] == "eps" for k in vals):
        return None
    # 当期の EPS がある基準・連結を選ぶ（連結を優先）。IFRS へ移った会社は古い期を日本基準で埋める
    pick = None
    for solo in (False, True):
        for std in ("IFRS", "USGAAP", "JP"):
            if ("eps", std, solo, 0) in vals:
                pick = (std, solo)
                break
        if pick:
            break
    if not pick:
        return None
    std, solo = pick
    order = [std] + [s for s in ("IFRS", "USGAAP", "JP") if s != std]
    per = []
    for k in range(4, -1, -1):
        cid = ("Current" if k == 0 else f"Prior{k}") + "YearDuration" + ("_NonConsolidatedMember" if solo else "")
        if cid not in ctx:
            continue
        s, e = ctx[cid]
        eps = next((vals[("eps", x, solo, k)] for x in order if ("eps", x, solo, k) in vals), None)
        ni = next((vals[("ni", x, solo, k)] for x in order if ("ni", x, solo, k) in vals), None)
        if eps is None:
            continue
        per.append({"s": s, "e": e, "eps": eps, "ni": ni})
    if not per:
        return None
    return {"std": std, "cons": not solo, "per": per}


def fetch_doc(doc_id, key):
    raw = get(f"{API}/documents/{doc_id}?type=1", key)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if n.startswith("XBRL/PublicDoc/") and n.endswith(".xbrl")]
    # 本体は jpcrp030000-asr（有報）。監査報告書（jpaud）は除く
    names.sort(key=lambda n: ("jpcrp" not in n, n))
    if not names or "jpcrp" not in names[0]:
        return None
    return parse_xbrl(zf.read(names[0]))


# ─────────────────────────────────────────────
#  書類一覧 → 銘柄ごとの直近の有報
# ─────────────────────────────────────────────
def scan_day(d, key, cand):
    lst = json.loads(get(f"{API}/documents.json?date={d}&type=2", key))
    if str(lst.get("metadata", {}).get("status")) != "200":
        raise RuntimeError(f"書類一覧 {d}: {lst.get('metadata')}")
    n = 0
    for r in lst.get("results") or []:
        if (r.get("ordinanceCode") != "010" or r.get("docTypeCode") != "120"
                or r.get("withdrawalStatus") != "0" or r.get("xbrlFlag") != "1"):
            continue
        c = code4(r.get("secCode"))
        if not c:
            continue
        sd = (r.get("submitDateTime") or d)[:10]
        old = cand.get(c)
        if old and old[1] >= sd:
            continue
        cand[c] = [r["docID"], sd, r.get("filerName") or "", r.get("periodEnd") or ""]
        n += 1
    return n


# ─────────────────────────────────────────────
#  株価（期末の終値）
# ─────────────────────────────────────────────
def fetch_prices(need):
    """need: {code: [期末日,...]} → {code: {期末日: 終値}}。yfinance の日足（分割調整済みの Close）"""
    if not need:
        return {}
    import yfinance as yf
    import pandas as pd
    out = {}
    codes = sorted(need)
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        lo = min(min(need[c]) for c in chunk)
        start = (date.fromisoformat(lo) - timedelta(days=12)).isoformat()
        tick = [f"{c}.T" for c in chunk]
        try:
            df = yf.download(tick, start=start, interval="1d", progress=False, auto_adjust=False,
                             group_by="ticker", threads=True)
        except Exception as e:
            log(f"  株価の取得に失敗（{chunk[0]}〜）: {e}")
            continue
        for c in chunk:
            try:
                s = df[f"{c}.T"]["Close"].dropna() if len(tick) > 1 else df["Close"].dropna()
            except Exception:
                continue
            if isinstance(s, pd.DataFrame):
                s = s.iloc[:, 0]
            if s.empty:
                continue
            idx = [x.date().isoformat() for x in s.index]
            got = {}
            for e in need[c]:
                # 期末日以前で最後の終値。期末日から10日より前しか無ければ（上場前・売買停止）使わない
                j = None
                for t in range(len(idx) - 1, -1, -1):
                    if idx[t] <= e:
                        j = t
                        break
                if j is None or (date.fromisoformat(e) - date.fromisoformat(idx[j])).days > 10:
                    continue
                got[e] = round(float(s.iloc[j]), 2)
            if got:
                out[c] = got
        time.sleep(1)
    return out


# ─────────────────────────────────────────────
#  分割の補正と PER
# ─────────────────────────────────────────────
def load_splits():
    d = load_json(SPLITS, {}) or {}
    out = {}
    for c, xs in (d.get("by_code") or {}).items():
        for x in xs:
            ex = x.get("ex") or x.get("ex_jpx")
            r = x.get("ratio")
            if ex and r and r > 0 and x.get("status") != "中止":
                out.setdefault(c, []).append((ex, float(r)))
    return out


def prod_ratio(spl, lo, hi):
    """lo < 権利落ち日 <= hi の分割の比率の積"""
    p = 1.0
    for ex, r in spl:
        if lo < ex <= hi:
            p *= r
    return p


def build_one(code, rec, spl, today):
    """1銘柄 → 公開用の形。使える期が3つ未満なら None"""
    per = [p for p in rec["per"]
           if (date.fromisoformat(p["e"]) - date.fromisoformat(p["s"])).days + 1 >= MIN_DAYS]
    if len(per) < 3:
        return None, "期が足りない"
    sub = rec["sub"]
    last = per[-1]
    sh_last = (last["ni"] / last["eps"]) if (last.get("ni") and last["eps"]) else None
    after = prod_ratio(spl, sub, today)                   # 有報の提出より後の分割 → 今の株数へ
    rows, how = [], []
    for p in per:
        r = prod_ratio(spl, p["e"], sub)                  # その期の末日〜提出日の分割
        eps = p["eps"]
        if p is last or r == 1.0:
            mode = "そのまま"
        else:
            sh = (p["ni"] / eps) if (p.get("ni") and eps) else None
            if not (sh and sh_last and sh > 0 and sh_last > 0):
                rows.append(None)
                how.append("分割の補正を決められない")
                continue
            q = math.log(sh_last / sh)
            if abs(q) <= abs(q - math.log(r)):
                mode = "直し済み"
            else:
                eps = eps / r
                mode = f"当時の株数→÷{r:g}"
        rows.append({"e": p["e"], "eps": eps / after})
        how.append(mode + (f"・提出後の分割÷{after:g}" if after != 1.0 else ""))
    px = rec.get("px") or {}
    out = []
    for p, row in zip(per, rows):
        if row is None:
            continue
        v = px.get(p["e"])
        per_v = None
        if v and row["eps"] > 0:
            price = v[0] / prod_ratio(spl, v[1], today)  # 株価の取得日より後の分割
            per_v = round(price / row["eps"], 1)
            row["px"] = round(price, 1)
        row["eps"] = round(row["eps"], 2)
        row["per"] = per_v
        out.append(row)
    if len([r for r in out if r["per"] is not None]) < 3:
        return None, "PER が3期そろわない"
    hold = any(abs((date.fromisoformat(ex) - date.fromisoformat(today)).days) <= NEAR_SPLIT
               for ex, _ in spl)
    o = {"doc": rec["doc"], "sub": sub, "std": rec["std"], "cons": rec["cons"], "fy": out,
         "how": how}
    if hold:
        o["hold"] = True
    return o, None


def build(recs, today):
    spl_all = load_splits()
    codes, why = {}, {}
    for c, rec in recs.items():
        o, w = build_one(c, rec, spl_all.get(c, []), today)
        if o:
            codes[c] = o
        else:
            why[w] = why.get(w, 0) + 1
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "EDINET（金融庁）有価証券報告書の「主要な経営指標等の推移」（1株当たり当期純利益）",
        "note": ("EPS は直近の有価証券報告書に載っている5期分。株式分割は docs/splits.json で今の株数に直した"
                 "（純利益÷EPSの株数で、会社が直してある期かどうかを確かめた）。各期の PER は"
                 "期末の終値（yfinance・分割調整済み）÷その期の EPS。EPS が0以下の期は PER を出さない。"
                 "決算期の変更で1年に満たない期は使わない。出典：EDINET（https://disclosure2.edinet-fsa.go.jp/）を加工して作成"),
        "count": len(codes),
        "skipped": why,
        "codes": codes,
    }
    write_json(OUT, out)
    return len(codes), why


# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill-days", type=int, default=0, help="書類一覧をこの日数さかのぼって見る（未見の日だけ）")
    ap.add_argument("--max-minutes", type=float, default=0, help="有報の読み込みをこの分数で打ち切る")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--code", action="append", help="この銘柄だけ読む（確認用）")
    args = ap.parse_args()
    t0 = time.time()
    today = datetime.now(JST).date()
    state = load_json(STATE, {}) or {}
    scanned = set(state.get("scanned") or [])
    cand = state.get("cand") or {}
    recs = load_json(RECS, {}) or {}

    if not args.build_only:
        key = api_key()
        # 1) 書類一覧。毎朝は前回の続き〜昨日（昨日ぶんはやり直す）。遡及は未見の日を新しい方から
        last = max(scanned) if scanned else (today - timedelta(days=2)).isoformat()
        days = []
        d = min(date.fromisoformat(last), today - timedelta(days=1))
        while d < today:
            days.append(d.isoformat())
            d += timedelta(days=1)
        if args.backfill_days:
            for i in range(1, args.backfill_days + 1):
                x = (today - timedelta(days=i)).isoformat()
                if x not in scanned and x not in days:
                    days.append(x)
        n_new = 0
        for x in days:
            try:
                n_new += scan_day(x, key, cand)
            except Exception as e:
                log(f"  一覧 {x} 失敗: {e}")
                continue
            scanned.add(x)
            time.sleep(LIST_WAIT)
            if len(days) > 20 and len(scanned) % 50 == 0:
                write_json(STATE, {"scanned": sorted(scanned), "cand": cand})
        write_json(STATE, {"scanned": sorted(scanned), "cand": cand})
        log(f"書類一覧: {len(days)}日 → 有報の候補 {len(cand)}社（新しく {n_new}件）")

        # 2) 有報を読む。まだ読んでいない（または新しい有報が出た）銘柄を、提出日の新しい順に
        todo = [c for c, v in cand.items() if (recs.get(c) or {}).get("doc") != v[0]]
        if args.code:
            todo = [c for c in args.code if c in cand]
        todo.sort(key=lambda c: cand[c][1], reverse=True)
        got, fail = [], 0
        for c in todo:
            if args.max_minutes and (time.time() - t0) / 60 > args.max_minutes:
                log(f"  {args.max_minutes}分に達したので打ち切り（残り {len(todo) - len(got) - fail}社）")
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
            if not p:
                recs[c] = {"doc": doc, "sub": sd, "name": nm, "std": None, "cons": None, "per": [], "px": {}}
                got.append(c)
                continue
            recs[c] = {"doc": doc, "sub": sd, "name": nm, **p, "px": {}}
            got.append(c)
            if len(got) % 100 == 0:
                write_json(RECS, recs)
        log(f"有報: {len(got)}社を読んだ（失敗 {fail}）")

        # 3) 期末の株価（まだ持っていない期だけ）
        need = {}
        spl_all = load_splits()
        for c, r in recs.items():
            # 権利落ち日の前後は yfinance の分割調整が追いついていないことがある。落ち着くまで取りに行かない
            if any(abs((date.fromisoformat(ex) - today).days) <= NEAR_SPLIT for ex, _ in spl_all.get(c, [])):
                continue
            miss = [p["e"] for p in r.get("per") or [] if p["e"] not in (r.get("px") or {})
                    and p["e"] not in (r.get("px_none") or [])]
            if miss:
                need[c] = miss
        if args.code:
            need = {c: v for c, v in need.items() if c in args.code}
        px = fetch_prices(need)
        pxd = today.isoformat()
        for c, es in need.items():
            r = recs[c]
            r.setdefault("px", {})
            for e in es:
                if e in px.get(c, {}):
                    r["px"][e] = [px[c][e], pxd]
                elif (today - date.fromisoformat(e)).days > 30:
                    # 上場前などで取れない期は、毎回取りに行かない
                    r.setdefault("px_none", []).append(e)
        log(f"期末の株価: {len(need)}社ぶんを見て {len(px)}社が取れた")
        write_json(RECS, recs)

    n, why = build(recs, today.isoformat())
    log(f"eps_hist.json: {n}社（出さない {why}）／所要 {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
