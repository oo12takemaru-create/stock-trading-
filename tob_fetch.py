# -*- coding: utf-8 -*-
"""TOB・MBO（公開買付け）の記録 → docs/tob.json（株レーダー ②TOB・MBO の一覧と答え合わせ）

起動文: kaburadar\\起動文_実務_日付つき一覧①②③_決算予定とTOBと今日の開示_2026-11-11.md

■ 材料
  TDnet の表題（docs/tdnet_hist/ の1日1ファイル＋docs/tdnet_list.json）。公開買付けの開始・意見表明・結果・変更。
  開始・意見表明・結果の PDF だけを読む（買付価格・買付期間・成立したか）。原本は保存も再配布もしない（URL だけ持つ）。
  株価は yfinance（対象の会社だけ）: 発表前1か月の値動き・プレミアム（公表日の前営業日の終値比）・発表後の値動き。
■ 取れなければ None。推測で埋めない
  買付価格は「1株につき（金）◯円」の最初の値（普通株式）。期間は「買付け等の期間」の直後の日付2つ。
  成立は結果の PDF に「成立」「不成立」と書かれたとき（両方あれば不成立を優先しない＝値を入れず note に残す）。
■ 1件＝対象の会社×最初の公表日（120日以内の続報は同じ1件にまとめる）
■ 初回（docs/tob.json が無い）は TDnet に残っている直近30日を取り直す（1秒間隔）。以後は手元の一覧だけ

使い方:
  python -X utf8 tob_fetch.py              # 毎営業日（split-daily.yml の中）
  python -X utf8 tob_fetch.py --since 2026-10-14
  python -X utf8 tob_fetch.py --no-price   # 株価を取らない（試し）
"""
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
HIST = DOCS / "tdnet_hist"
TDLIST = DOCS / "tdnet_list.json"
OUT = DOCS / "tob.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
SLEEP = 1.0
GROUP_DAYS = 120
BASE = "https://www.release.tdnet.info/inbs/"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def norm(t):
    return re.sub(r"[\s　]+", "", unicodedata.normalize("NFKC", t or ""))


def code4(c):
    c = (c or "").strip().upper()
    return c[:4] if len(c) == 5 else c


# ─────────────────────────── 表題の分類
RE_TOB = re.compile(r"公開買付")
RE_EXCL = re.compile(r"準ずる行為|買集め|買い集め|への応募|に応募|応募契約|応募に関する|公開買付けに伴う(?!.*意見)|公開買付に伴う(?!.*意見)|"
                     r"投資利益|特別利益|特別損失|子会社株式の売却|持分法")
RE_SELF = re.compile(r"自己株式の公開買付|自己株券買付状況|自己株式の取得及び自己株式の公開買付")
RE_MBO = re.compile(r"MBO|マネジメント・?バイアウト")
RE_CODE = re.compile(r"(?:証券コード|コード番号)[:：]?\s*([0-9][0-9A-Z]{3})")
RE_ACQ = re.compile(r"^(?:\((?:変更|訂正)[^)]*\))?[「]?(.{2,40}?)(?:による|が実施する|が行う)")


def classify(title):
    """表題 → (種類, 対象が自社か)。TOB に関係なければ None
    種類: start（開始・実施）／opinion（意見表明）／result（結果）／change（変更・訂正・延長）／self（自社株の公開買付け）"""
    t = norm(title)
    if not RE_TOB.search(t) or RE_EXCL.search(t) and "意見表明" not in t:
        return None
    if RE_SELF.search(t):
        kind = "self"
    elif re.search(r"結果", t) and not re.search(r"意見表明", t):
        kind = "result"
    elif re.match(r"^\((?:変更|訂正|開示事項の変更)", t) or re.search(r"買付条件等の変更|期間の延長|期間延長|訂正|変更", t):
        kind = "change"
    elif "意見表明" in t:
        kind = "opinion"
    elif re.search(r"開始|実施", t):
        kind = "start"
    else:
        kind = "other"
    own = bool(re.search(r"当社(?:株式|株券|普通株式|の株式|の株券)", t)) or kind == "self"
    return kind, own


# ─────────────────────────── PDF
def http(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def pdf_text(data):
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    return norm("\n".join(p.get_text() for p in doc))


def ymd(y, m, d):
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except Exception:
        return None


def extract(text):
    """PDF の本文（norm 済み）→ {price, start, end, success, mbo, code}"""
    out = {}
    m = re.search(r"(?:買付け?等?の価格|公開買付価格)[^円]{0,80}?1株(?:につき|当たり)[、,]?金?([0-9,]+(?:\.[0-9]+)?)円", text)
    if not m:
        m = re.search(r"普通株式1株(?:につき|当たり)[、,]?金?([0-9,]+(?:\.[0-9]+)?)円", text)
    if m:
        try:
            out["price"] = float(m.group(1).replace(",", ""))
        except ValueError:
            pass
    m = re.search(r"買付け?等の期間[^0-9]{0,40}(\d{4})年(\d{1,2})月(\d{1,2})日[^0-9]{0,30}?(?:から|～|〜|-)[^0-9]{0,10}(\d{4})年(\d{1,2})月(\d{1,2})日", text)
    if m:
        out["start"], out["end"] = ymd(*m.group(1, 2, 3)), ymd(*m.group(4, 5, 6))
    ok = re.search(r"本公開買付けは成立|本公開買付けが成立|公開買付けは成立いたしました|成立いたしましたので", text)
    ng = re.search(r"本公開買付けは不成立|本公開買付けが不成立|成立しませんでした|不成立となりました", text)
    if ok and not ng:
        out["success"] = True
    elif ng and not ok:
        out["success"] = False
    if RE_MBO.search(text[:6000]):
        out["mbo"] = True
    m = RE_CODE.search(text[:3000])
    if m:
        out["code"] = m.group(1)
    # 予告型（「開始予定」を前に公表していた）: 先に公表した日。プレミアム・発表前の値動きはその日で見るべきなので、後で使わない印にする
    pre = [ymd(*g) for g in re.findall(r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:付|に公表|に公表しました|付けで公表)[^。]{0,80}?開始予定", text)]
    pre = [p for p in pre if p]
    if pre:
        out["pre_ann"] = min(pre)
    return out


# ─────────────────────────── 一覧
def items(since=None):
    """[{d, code, name, time, title, id, url}]"""
    got = {}
    if HIST.exists():
        for f in sorted(HIST.glob("????-??-??.json")):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            cols = j.get("cols") or []
            for r in j.get("rows") or []:
                x = dict(zip(cols, r))
                got[x["id"]] = {"d": j["d"], "code": x["c"], "name": x["n"], "time": x["t"], "title": x["ti"], "id": x["id"]}
    try:
        for day in json.loads(TDLIST.read_text(encoding="utf-8")).get("days") or []:
            for x in day.get("items") or []:
                got.setdefault(x["id"], {"d": day["d"], "code": x["code"], "name": x["name"], "time": x["time"],
                                         "title": x["title"], "id": x["id"]})
    except Exception as e:
        log(f"tdnet_list.json が読めない: {e}")
    if since:
        import tdnet_list
        d = date.fromisoformat(since)
        today = datetime.now(JST).date()
        have = {x["d"] for x in got.values()}
        while d <= today:
            if d.weekday() < 5 and d.isoformat() not in have:
                day = tdnet_list.fetch_day(d.isoformat())
                time.sleep(SLEEP)
                for x in (day or {}).get("items") or []:
                    got.setdefault(x["id"], {"d": d.isoformat(), "code": x["code"], "name": x["name"], "time": x["time"],
                                             "title": x["title"], "id": x["id"]})
            d += timedelta(days=1)
    return sorted(got.values(), key=lambda x: (x["d"], x.get("time") or "", x["id"]))


def target_of(it, kind, own, ex):
    """対象の会社のコード（4桁）。分からなければ None"""
    if own:
        return code4(it["code"])
    m = RE_CODE.search(norm(it["title"]))
    if m:
        return m.group(1)
    if ex.get("code"):
        return ex["code"]
    return None


# ─────────────────────────── 株価
def prices(codes, start):
    """{code: [(日付, 終値), …]}。取れなければ空"""
    if not codes:
        return {}
    try:
        import yfinance as yf
    except ImportError:
        log("yfinance が無い（株価の欄は空のまま）")
        return {}
    out = {}
    ts = [f"{c}.T" for c in sorted(codes)]
    for i in range(0, len(ts), 50):
        part = ts[i:i + 50]
        try:
            df = yf.download(part, start=start, interval="1d", progress=False, auto_adjust=False, group_by="ticker", threads=False)
        except Exception as e:
            log(f"株価が取れない: {e}")
            continue
        for t in part:
            try:
                s = (df[t]["Close"] if len(part) > 1 else df["Close"]).dropna()
                if hasattr(s, "columns"):
                    s = s.iloc[:, 0]
                out[t[:-2]] = [(ix.strftime("%Y-%m-%d"), float(v)) for ix, v in s.items() if v and v > 0]
            except Exception:
                pass
        time.sleep(SLEEP)
    return out


def moves(e, ser):
    """発表前1か月・プレミアム・発表後（翌日・5営業日後・最新）"""
    if not ser:
        return
    e["last_date"], e["last"] = ser[-1][0], round(ser[-1][1], 2)
    if e.get("price"):
        e["gap_last"] = round((ser[-1][1] / e["price"] - 1) * 100, 2)
    if not e.get("ann_known"):
        return                                    # 公表日が分からない件は、発表前・発表後の値動きを出さない
    d = e["ann"]
    late = (e.get("ann_time") or "99:99") >= "15:30"
    before = [x for x in ser if x[0] < d]
    if before:
        p0d, p0 = before[-1]                      # 公表日の前営業日の終値
        e["prev_close"], e["prev_date"] = round(p0, 2), p0d
        if len(before) >= 21:
            e["pre1m"] = round((p0 / before[-21][1] - 1) * 100, 2)
        if e.get("price"):
            e["premium"] = round((e["price"] / p0 - 1) * 100, 1)
    # 反応日: 15:30 以降の公表は次の営業日、それより前は当日
    after = [x for x in ser if (x[0] > d if late else x[0] >= d)]
    base = [x for x in ser if (x[0] <= d if late else x[0] < d)]
    if after and base:
        b = base[-1][1]
        e["react_date"] = after[0][0]
        e["n1"] = round((after[0][1] / b - 1) * 100, 2)
        if len(after) >= 5:
            e["n5"] = round((after[4][1] / b - 1) * 100, 2)


def main():
    args = sys.argv[1:]
    since = args[args.index("--since") + 1] if "--since" in args else None
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception as e:
            log(f"tob.json が読めない（作り直す）: {e}")
    elif not since:
        since = (datetime.now(JST).date() - timedelta(days=30)).isoformat()   # 初回は TDnet に残っている分を取り直す
        log(f"初回: {since} から TDnet を取り直す")
    docs = {x["id"]: x for x in prev.get("docs") or []}    # 読んだ開示（PDF の読み取り結果ごと）
    n_pdf = 0
    for it in items(since):
        c = classify(it["title"])
        if not c or it["id"] in docs:
            continue
        kind, own = c
        ex = {}
        if kind in ("start", "opinion", "result", "self"):
            try:
                ex = extract(pdf_text(http(f"{BASE}{it['id']}.pdf")))
                n_pdf += 1
            except Exception as e:
                log(f"  PDF が読めない {it['d']} {it['code']} {e}")
                ex = {"pdf_ng": True}
            time.sleep(SLEEP)
        # 期間の終わりが公表日より前＝過去の公開買付けの記載を拾った（変更の開示に前回の条件が書いてある等）。値を入れない
        if ex.get("end") and ex["end"] < it["d"] and kind != "result":
            for k in ("price", "start", "end"):
                ex.pop(k, None)
            ex["note"] = "本文の期間が公表日より前（過去の公開買付けの記載）のため、価格と期間を入れていない"
        tgt = target_of(it, kind, own, ex)
        docs[it["id"]] = {"id": it["id"], "d": it["d"], "time": it.get("time"), "by": code4(it["code"]), "by_name": it["name"],
                          "title": it["title"], "kind": kind, "own": own, "target": tgt,
                          **{k: v for k, v in ex.items() if k != "code"}}
    # 対象×最初の公表日でまとめる
    events = []
    for x in sorted(docs.values(), key=lambda x: (x["d"], x.get("time") or "", x["id"])):
        if not x.get("target"):
            continue
        ev = next((e for e in events if e["code"] == x["target"]
                   and (date.fromisoformat(x["d"]) - date.fromisoformat(e["ann"])).days <= GROUP_DAYS), None)
        if not ev:
            ev = {"code": x["target"], "ann": x["d"], "ann_time": x.get("time"), "self": x["kind"] == "self",
                  "mbo": False, "docs": []}
            events.append(ev)
        ev["docs"].append(x["id"])
        if len(ev["docs"]) == 1:
            # 最初に見えた開示が開始・意見表明・自社株の公開買付けでなければ、公表は記録を始める前（発表前の値動き・プレミアムは出さない）
            ev["ann_known"] = x["kind"] in ("start", "opinion", "self") and not (x.get("pre_ann") and x["pre_ann"] < x["d"])
            if x.get("pre_ann") and x["pre_ann"] < x["d"]:
                ev["pre_ann"] = x["pre_ann"]
        if x["own"]:
            ev["name"] = x["by_name"]
            if not ev.get("acquirer") and not ev["self"]:
                m = RE_ACQ.search(norm(x["title"]))
                if m:
                    ev["acquirer"] = m.group(1)
        elif not ev.get("acquirer"):
            m = RE_ACQ.search(norm(x["title"]))
            ev["acquirer"] = m.group(1) if m else x["by_name"]
        for k in ("price", "start", "end"):
            if x.get(k) and (k not in ev or x["kind"] in ("change",)):
                ev[k] = x[k]
        if x["kind"] == "change":
            ev["changed"] = True
        if x.get("mbo") or RE_MBO.search(norm(x["title"])):
            ev["mbo"] = True
        if x["kind"] == "result":
            ev["result_date"] = x["d"]
            if "success" in x:
                ev["success"] = x["success"]
    # 対象の分からない開始の開示（買付者の表題にコードが無い）は、同じ日に意見表明で始まった件の価格・期間の穴だけ埋める
    for x in docs.values():
        if x.get("target") or x["kind"] not in ("start",):
            continue
        cand = [e for e in events if e["ann"] == x["d"] and not e.get("price")]
        if len(cand) == 1:
            for k in ("price", "start", "end"):
                if x.get(k) and not cand[0].get(k):
                    cand[0][k] = x[k]
            cand[0]["docs"].append(x["id"])
    today = datetime.now(JST).date().isoformat()
    for e in events:
        e["status"] = ("終了" if e.get("result_date") or (e.get("end") and e["end"] < today)
                       else "実施中" if e.get("start") and e["start"] <= today
                       else "予定" if e.get("start") or e.get("ann_known") else "続報のみ")
    if "--no-price" not in args and events:
        lo = (min(date.fromisoformat(e["ann"]) for e in events) - timedelta(days=50)).isoformat()
        px = prices({e["code"] for e in events}, lo)
        for e in events:
            moves(e, px.get(e["code"]))
    events.sort(key=lambda e: (e["ann"], e["code"]), reverse=True)
    OUT.write_text(json.dumps({"updated": datetime.now(JST).isoformat(timespec="seconds"),
                               "note": "TDnet の表題と PDF（買付価格・期間・成立）から機械的に作った TOB・MBO の記録。"
                                       "プレミアム＝買付価格÷公表日の前営業日の終値−1。原本の PDF は再配布しない（URL は "
                                       + BASE + "＋id＋.pdf）",
                               "events": events, "docs": sorted(docs.values(), key=lambda x: (x["d"], x["id"]))},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    log(f"TOB・MBO: {len(events)}件（開示 {len(docs)}件・今回読んだ PDF {n_pdf}件）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
