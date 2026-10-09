# -*- coding: utf-8 -*-
"""EDINET 大量保有報告書の取り込み（記録だけ・ページは作らない）

起動文: kaburadar\\起動文_実務_積上③_EDINET大量保有の取り込み_2026-10-07.md
設計書: 機能5「著名投資家の保有追跡」p11〜13

■ 対象
  府令 060（株券等の大量保有の状況の開示に関する内閣府令）の
  docTypeCode 350（大量保有報告書・変更報告書）と 360（訂正報告書）。
  様式（formCode）: 010000 大量保有 / 010002 変更 / 020002 変更（短期大量譲渡）
                    030000 大量保有（特例対象株券等）/ 030002 変更（特例）/ 090001 訂正
  XBRL（type=1 の zip 内 PublicDoc/*.xbrl）だけを読む。CSV・PDF は見ない。

■ 出力
  docs/edinet/days/YYYY-MM-DD.jsonl.gz  1行＝1書類。提出日ごと（読み書きは edinet_store.py だけ・2026-10-09〜）
                              旧 docs/edinet/YYYY-MM.jsonl（〜10-08）は移行済み
  docs/edinet_latest.json     直近30日の銘柄別・提出者別の索引＋直近90日の「新規5%超・増加」件数
  docs/edinet/_state.json     取り込み済みの日付（遡及の進み具合）

■ 個人情報（設計書 p12・p13）
  保存するのは 氏名（報告書記載名）・職業欄・E番号 まで。
  住所・電話・連絡先・生年月日・勤務先の住所は**読まない**（下の KEEP に無いものは捨てる）。

■ XBRL の罠（2026-10-07 に実物6様式で確認）
  1. 共同保有者がいると、各者の割合（FilerLargeVolumeHolderN の文脈）とは別に、
     文脈が FilingDateInstant だけの値が「合計」。単独提出では合計が無いので本人の値を使う
  2. インラインXBRL由来で同じ要素が同じ文脈に2〜3回出る → 最初の1つだけ使う
  3. 割合は小数（0.1788 = 17.88%）。% に直して保存する
  4. 表紙の NameCoverPage は「株式会社〇〇　代表取締役　××」のように肩書が混ざる。
     提出者名は保有者欄の Name と EDINETCodeDEI を正とする
  5. 60日間の取得・処分の表は HTML がエスケープされた文字列。空欄は <td .../>（自己終了）。
     値が無い要素は xsi:nil="true" の空要素
  6. 特例報告（030000/030002）には60日間の表が**そもそも無い**
  7. 新規の大量保有報告書は「前回の割合」が空
  8. 書類一覧の secCode は大量保有では null。発行者の証券コードは XBRL の SecurityCodeOfIssuer
  9. 日付別の書類一覧には「その日に書類情報が修正された過去の書類」も混ざる
     （docInfoEditStatus=1、submitDateTime が過去日）。提出月のファイルに入れ、docID で重複を弾く
  10. 「変更報告書提出事由」に訂正内容の全文（担当者名・電話番号入り・4,300字）が入る書類がある
     （S100WUUI。縦覧期間切れの訂正を変更報告書の形で出したもの）。
     → 事由は200字で切り、保存する文字列はすべて電話番号の形を消す（scrub）
"""
import argparse
import html
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import edinet_store

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
OUT_DIR = DOCS / "edinet"
LATEST = DOCS / "edinet_latest.json"
STATE = OUT_DIR / "_state.json"
API = "https://api.edinet-fsa.go.jp/api/v2"
UA = "kaburadar.jp edinet_daily (+https://kaburadar.jp/)"
WAIT = 1.5                 # 書類ごとの間隔（秒）。公式の上限は非公開。通例は3〜5秒とされるが1件の応答に約0.5秒かかる
DOC_TYPES = {"350", "360"}
ORDINANCE = "060"
LATEST_DAYS = 30
BIG_DAYS = 90
BACKFILL_DAYS = 365
# 2026-11-01 からは5年まで遡る（起動文「まず直近1年。5年は11月以降」）。毎朝40分ずつ・約4週間で埋まる見込み
BACKFILL_5Y_FROM = date(2026, 11, 1)
BACKFILL_DAYS_5Y = 365 * 5

FORM = {
    "010000": ("大量保有", False), "010002": ("変更", False), "020002": ("変更", False),
    "030000": ("大量保有", True),  "030002": ("変更", True),  "090001": ("訂正", False),
}
NS_LVH = "jplvh_cor"
XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"


def log(*a):
    print(*a, flush=True)


def api_key():
    k = os.environ.get("EDINET_API_KEY", "").strip()
    if not k:
        p = ROOT / ".edinet_key"
        if p.exists():
            k = p.read_text(encoding="utf-8").strip()
    if not k:
        log("::error::EDINET_API_KEY がありません（Secrets か .edinet_key）")
        sys.exit(1)
    return k


def get(url, key, tries=3):
    sep = "&" if "?" in url else "?"
    full = f"{url}{sep}Subscription-Key={urllib.parse.quote(key)}"
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            # 例外文にURL（＝キー）が入ることがあるので伏せる
            last = str(e).replace(key, "***")
            time.sleep(5 * (i + 1))
    raise RuntimeError(last)


def nfkc(s):
    return unicodedata.normalize("NFKC", s or "").strip()


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


# ─────────────────────────────────────────────
#  XBRL の読み取り
# ─────────────────────────────────────────────
def facts(xbrl_bytes):
    """{(要素名, 保有者番号 or None): 値} 。同じキーは最初の1つだけ（罠2）"""
    root = ET.fromstring(xbrl_bytes)
    out = {}
    for el in root.iter():
        tag = el.tag
        if not isinstance(tag, str) or "}" not in tag:
            continue
        ns, name = tag[1:].split("}", 1)
        if not ns.endswith("/jplvh_cor") and "jplvh" not in ns:
            continue
        ctx = el.get("contextRef") or ""
        m = re.search(r"FilerLargeVolumeHolder(\d+)Member", ctx)
        key = (name, int(m.group(1)) if m else None)
        if key in out:
            continue
        if el.get(XSI_NIL) == "true":
            out[key] = None
        else:
            out[key] = (el.text or "").strip()
    return out


PHONE = re.compile(r"(?:電話番号|TEL|Tel|tel)?[\s:：]*0\d{1,4}[-－‐ー(（]\d{1,4}[-－‐ー)）]\d{3,4}")
REASON_MAX = 200


def scrub(s, limit=None):
    """自由記述から電話番号の形を消す（罠10）。limit を超えたら切る"""
    if not s:
        return s
    s = PHONE.sub("［電話番号削除］", s)
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    return s


def pct(v):
    if v in (None, ""):
        return None
    try:
        return round(float(v) * 100, 2)
    except ValueError:
        return None


def num(v):
    if v in (None, ""):
        return None
    try:
        return int(float(nfkc(v).replace(",", "")))
    except ValueError:
        return None


TD = re.compile(r"<td\b[^>]*/>|<td\b[^>]*>(.*?)</td>", re.S)


def jdate(s):
    """取得・処分の日付。「令和8年1月9日」のような和暦も多い（7,588行・積上⑧ #598）ので ISO にそろえる"""
    return edinet_store.iso_date(nfkc(s)) or None


def trades(block):
    """60日間の取得・処分（罠5）。見出し行は捨てる。列: 年月日/種類/数量/割合/市場内外/取得処分/単価"""
    if not block:
        return []
    h = html.unescape(block) if "&lt;" in block else block
    out = []
    for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", h, re.S):
        cells = [nfkc(re.sub(r"<[^>]+>", "", m.group(1) or "")) for m in TD.finditer(tr)]
        if len(cells) < 6 or "年月日" in cells[0]:
            continue
        # 「該当事項なし」だけの行（表の埋め草）は売買ではないので捨てる
        if "該当" in cells[0] and not any(ch.isdigit() for ch in cells[2]):
            continue
        cells += [""] * (7 - len(cells))
        price = cells[6].replace(",", "").replace("円", "")
        try:
            price = float(price) if price else None
        except ValueError:
            price = None   # 「-」や注記が入ることがある。原文は捨てずに残す
        r = {"d": jdate(cells[0]), "kind": cells[1], "qty": num(cells[2]),
             "ratio": cells[3] or None, "mkt": cells[4], "side": cells[5], "price": price}
        if price is None and cells[6]:
            r["price_raw"] = cells[6]
        out.append(r)
    return out


def issuer_code(v):
    c = nfkc(v).upper()
    if len(c) == 5 and c.endswith("0"):
        c = c[:4]
    return c or None


def parse_doc(meta, xbrl_bytes):
    f = facts(xbrl_bytes)
    g = lambda name, h=None: f.get((name, h))
    form = meta.get("formCode") or ""
    typ, special = FORM.get(form, ("その他", False))
    holders = sorted({h for (_, h) in f if h is not None})
    hs = []
    for h in holders:
        kind = nfkc(g("IndividualOrCorporation", h))
        indiv = kind.startswith("個人")
        rec = {
            "e": nfkc(g("EDINETCodeDEI", h)) or None,
            "name": nfkc(g("Name", h)) or None,
            "kind": kind or None,
            "ratio": pct(g("HoldingRatioOfShareCertificatesEtc", h)),
            "ratio_prev": pct(g("HoldingRatioOfShareCertificatesEtcPerLastReport", h)),
            "shares": num(g("TotalNumberOfStocksEtcHeld", h)),
            "base_date": g("BaseDate", h) or None,
            "purpose": scrub(g("PurposeOfHolding", h)) or None,   # 原文のまま（電話番号の形だけ消す）
            "trades": trades(g("DetailsOfAcquisitionsAndDisposalsOfStocksEtcIssuedByIssuerOfSaidStocksEtcDuringLast60DaysTextBlock", h)),
        }
        if indiv:
            # 個人は 氏名・職業・E番号まで（住所・電話・勤務先は読まない）
            rec["occupation"] = nfkc(g("Occupation", h)) or None
        hs.append(rec)
    # 合計（罠1）。単独なら本人の値
    tot = pct(g("HoldingRatioOfShareCertificatesEtc"))
    tot_prev = pct(g("HoldingRatioOfShareCertificatesEtcPerLastReport"))
    if tot is None and len(hs) == 1:
        tot, tot_prev = hs[0]["ratio"], hs[0]["ratio_prev"]
    sub = (meta.get("submitDateTime") or "")
    return {
        "id": meta["docID"],
        "d": sub[:10], "tm": sub[11:16],
        "type": typ, "form": form, "special": special,
        "short_transfer": form == "020002",
        "title": nfkc(g("DocumentTitleCoverPage")) or meta.get("docDescription"),
        "parent": meta.get("parentDocID"),
        "filer_e": meta.get("edinetCode"),
        "filer": meta.get("filerName"),
        "issuer": {"e": meta.get("issuerEdinetCode"),
                   "code": issuer_code(g("SecurityCodeOfIssuer")),
                   "name": nfkc(g("NameOfIssuer")) or None},
        "obligation_date": g("DateWhenFilingRequirementAroseCoverPage") or None,
        "reason": scrub(nfkc(g("ReasonForFilingChangeReportCoverPage")), REASON_MAX) or None,
        "ratio": tot, "ratio_prev": tot_prev,
        "n_holders": len(hs),
        "holders": hs,
    }


def fetch_xbrl(doc_id, key):
    raw = get(f"{API}/documents/{doc_id}?type=1", key)
    zf = zipfile.ZipFile(io.BytesIO(raw))
    names = [n for n in zf.namelist() if n.startswith("XBRL/PublicDoc/") and n.endswith(".xbrl")]
    names.sort(key=lambda n: ("jplvh" not in n, n))
    if not names:
        return None
    return zf.read(names[0])


# ─────────────────────────────────────────────
#  1日ぶんの取り込み
# ─────────────────────────────────────────────
def run_day(d, key, stats):
    lst = json.loads(get(f"{API}/documents.json?date={d.isoformat()}&type=2", key))
    if str(lst.get("metadata", {}).get("status")) != "200":
        raise RuntimeError(f"書類一覧 {d}: {lst.get('metadata')}")
    rows = [r for r in lst.get("results") or []
            if r.get("ordinanceCode") == ORDINANCE and r.get("docTypeCode") in DOC_TYPES]
    got = []
    seen = {}
    c = Counter()
    for r in rows:
        if r.get("withdrawalStatus") != "0":
            c["取下げ"] += 1
            continue
        if r.get("xbrlFlag") != "1":
            c["XBRLなし"] += 1
            continue
        sd = (r.get("submitDateTime") or d.isoformat())[:10]   # 罠9: 過去日の提出が混ざる
        if sd not in seen:
            seen[sd] = edinet_store.day_ids(sd)
        if r["docID"] in seen[sd]:
            c["取込済み"] += 1
            continue
        try:
            x = fetch_xbrl(r["docID"], key)
            if not x:
                c["XBRL読めず"] += 1
                continue
            rec = parse_doc(r, x)
        except Exception as e:
            c["失敗"] += 1
            log(f"  {r['docID']} 失敗: {e}")
            time.sleep(WAIT)
            continue
        got.append(rec)
        seen[sd].add(r["docID"])
        c[rec["type"] + ("（特例）" if rec["special"] else "")] += 1
        time.sleep(WAIT)
    edinet_store.write(got)
    n = len(got)
    stats.update(c)
    log(f"  {d}: 対象 {len(rows)}件 → 取込 {n}件  {dict(c)}")
    return n


# ─────────────────────────────────────────────
#  索引（edinet_latest.json）
# ─────────────────────────────────────────────
def load_records(since):
    return edinet_store.iter_records(since=since)


def build_latest(today):
    since30 = (today - timedelta(days=LATEST_DAYS)).isoformat()
    since90 = (today - timedelta(days=BIG_DAYS)).isoformat()
    recs90 = load_records(since90)
    recs30 = [r for r in recs90 if r["d"] >= since30]

    def brief(r):
        return {"id": r["id"], "d": r["d"], "type": r["type"], "special": r["special"],
                "filer_e": r["filer_e"], "filer": r["filer"],
                "code": r["issuer"]["code"], "issuer": r["issuer"]["name"],
                "ratio": r["ratio"], "ratio_prev": r["ratio_prev"]}

    by_issuer = defaultdict(list)
    by_holder = {}
    for r in sorted(recs30, key=lambda r: (r["d"], r.get("tm") or ""), reverse=True):
        b = brief(r)
        if r["issuer"]["code"]:
            by_issuer[r["issuer"]["code"]].append(b)
        for h in r["holders"]:
            e = h.get("e")
            if not e:
                continue
            ent = by_holder.setdefault(e, {"name": h.get("name"), "count": 0, "docs": []})
            ent["count"] += 1
            ent["docs"].append(r["id"])

    # 積上②（大口）へ渡す件数。訂正は数えない（元の書類で数えている）
    big = defaultdict(lambda: {"new": 0, "up": 0, "down": 0})
    for r in recs90:
        code = r["issuer"]["code"]
        if not code or r["type"] == "訂正":
            continue
        if r["type"] == "大量保有":
            big[code]["new"] += 1
        elif r["ratio"] is not None and r["ratio_prev"] is not None:
            if r["ratio"] > r["ratio_prev"]:
                big[code]["up"] += 1
            elif r["ratio"] < r["ratio_prev"]:
                big[code]["down"] += 1

    write_json(LATEST, {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "from": since30, "to": today.isoformat(), "count": len(recs30),
        "note": "EDINET 大量保有報告書・変更報告書・訂正報告書の記録。割合は%。個人は氏名・職業・E番号のみ",
        "by_issuer": by_issuer,
        "by_holder": by_holder,
        "issuer_90d": {"from": since90, "items": big},
    })
    return len(recs30), len(big)


# ─────────────────────────────────────────────
#  本体
# ─────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="この日だけ取り込む（YYYY-MM-DD）")
    ap.add_argument("--backfill", type=int, default=0,
                    help=f"直近{BACKFILL_DAYS}日のうち未取込の日を古い方から最大N日")
    ap.add_argument("--max-minutes", type=float, default=0, help="遡及をこの分数で打ち切る")
    args = ap.parse_args()
    key = api_key()
    t0 = time.time()
    today = datetime.now(JST).date()
    state = load_json(STATE, {}) or {}
    done = set(state.get("done") or [])

    if args.date:
        days = [date.fromisoformat(args.date)]
    else:
        # 毎朝: 前回取り込んだ日の翌日〜昨日（休日も一覧は空で返るので暦日で回す）
        last = max(done) if done else (today - timedelta(days=1)).isoformat()
        start = min(date.fromisoformat(last) + timedelta(days=1), today - timedelta(days=1))
        days = [start + timedelta(days=i) for i in range((today - start).days)]
        # その日の提出は夜まで増えるので、昨日ぶんは毎朝やり直す（docID で重複は弾く）
        if not days:
            days = [today - timedelta(days=1)]

    stats = Counter()
    total = 0
    for d in days:
        total += run_day(d, key, stats)
        done.add(d.isoformat())

    if args.backfill:
        span = BACKFILL_DAYS_5Y if today >= BACKFILL_5Y_FROM else BACKFILL_DAYS
        lo = today - timedelta(days=span)
        todo = [lo + timedelta(days=i) for i in range(span)
                if (lo + timedelta(days=i)).isoformat() not in done]
        n_days = 0
        for d in todo[:args.backfill]:
            if args.max_minutes and (time.time() - t0) / 60 > args.max_minutes:
                log(f"  遡及: {args.max_minutes}分に達したので打ち切り")
                break
            total += run_day(d, key, stats)
            done.add(d.isoformat())
            n_days += 1
            write_json(STATE, {"done": sorted(done)})   # 途中で落ちても進みを残す
        left = len([x for x in todo if x.isoformat() not in done])
        log(f"遡及: {n_days}日ぶん処理。直近{span}日の残り {left}日")

    write_json(STATE, {"done": sorted(done)})
    n30, n90 = build_latest(today)
    log(f"取込 {total}件 {dict(stats)}")
    log(f"edinet_latest.json: 直近{LATEST_DAYS}日 {n30}件 / 90日で動きのあった銘柄 {n90}")
    log(f"所要時間: {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
