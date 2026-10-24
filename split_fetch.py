# -*- coding: utf-8 -*-
"""株式分割の発表と実施を集める → docs/splits.json ＋ docs/splits_hist/YYYY.jsonl（追記のみ）

■ 2つの出所
  1. TDnet の適時開示（発表）: 表題に「株式分割」を含む開示を拾い、本文PDFから
     分割比率（1:N）・基準日・効力発生日を抜く（split_extract.py）。
     一覧は docs/tdnet_list.json（決算速報 kessan-flash.yml が 15:30/16:30/18:30 に貯める）に加え、
     ここでも直近3営業日の一覧を取り直す（18:30 より後の開示を落とさないため）。
     TDnet の一覧は約1か月しか残らないので、それより前の「発表日」は取れない。
  2. JPX「新株落・権利落等一覧」（月次統計 17/18_kenri*.pdf）: 実施済みの分割の権利落ち日・確定日（基準日）・比率。
     2016年1月から公開されている（アーカイブ 00-archives-01〜10）。発表日は載っていない。

■ 追記のみ
  docs/splits_hist/YYYY.jsonl に1行1件で足していく（年は TDnet＝開示日・JPX＝基準日）。消さない・書き換えない。
  訂正・中止の開示も1行として足し、まとめるのは読む側（build_events）で行う。
  PDFが取れなかった（通信の失敗）開示は行にせず、次の回に読み直す（_state.json の pending）。

■ 原本PDFは保存しない・配らない。URL だけ持つ（統括方針 第3章）。

使い方:
  python -X utf8 split_fetch.py                     # 毎営業日（tdnet_list.json＋直近3営業日）
  python -X utf8 split_fetch.py --since 2026-09-15  # TDnet に残っている日を遡って取り込む（初回）
  python -X utf8 split_fetch.py --no-jpx            # JPX の月次一覧を見に行かない
"""
import json
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jp_bizday                                   # noqa: E402
import split_extract as X                          # noqa: E402

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
DOCS = HERE / "docs"
HIST = DOCS / "splits_hist"
STATE = HIST / "_state.json"
OUT = DOCS / "splits.json"
TDLIST = DOCS / "tdnet_list.json"

UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
JPX = "https://www.jpx.co.jp"
JPX_PAGES = (["/markets/statistics-equities/monthly/index.html"]
             + [f"/markets/statistics-equities/monthly/00-archives-{n:02d}.html" for n in range(1, 11)])
RECENT_DAYS = 90          # 「直近90日に発表された分割」
LOOKBACK_BD = 3           # 毎回取り直す TDnet の営業日数
PDF_KINDS = {"split", "correction", "cancel"}
JPX_SKIP = {"不動産投信(REIT)", "ETF", "ETN(JDR)", "ETF(JDR)", "<外国株>"}
SLEEP = 1.0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def http(url, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            if k == tries - 1:
                raise
        except Exception:
            if k == tries - 1:
                raise
        time.sleep(3 * (k + 1))


def code4(c):
    """TDnet の5桁（末尾0）→ 4桁。138A0 → 138A"""
    c = (c or "").strip().upper()
    return c[:4] if len(c) == 5 and c.endswith("0") else c


# ─────────────────────────── 履歴（追記のみ）
def load_hist():
    rows = []
    for f in sorted(HIST.glob("*.jsonl")):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    return rows


def append_hist(rows):
    by_year = {}
    for r in rows:
        y = (r.get("d") if r["src"] == "tdnet" else r.get("record") or r.get("ex") or "")[:4]
        by_year.setdefault(y, []).append(r)
    HIST.mkdir(parents=True, exist_ok=True)
    for y, rs in by_year.items():
        with (HIST / f"{y}.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            for r in rs:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"jpx_months": [], "pending": {}}


# ─────────────────────────── TDnet
def tdnet_items(since=None):
    """{id: item}。tdnet_list.json の全日＋直近の数営業日（または since 以降）を TDnet から取り直す"""
    import tdnet_list
    items = {}
    try:
        for day in json.loads(TDLIST.read_text(encoding="utf-8")).get("days", []):
            for it in day.get("items", []):
                items[it["id"]] = {**it, "d": day["d"]}
    except Exception as e:
        log(f"  tdnet_list.json を読めない（直接取得だけで続行）: {e}")
    today = datetime.now(JST).date()
    days = []
    if since:
        d = date.fromisoformat(since)
        while d <= today:
            if jp_bizday.is_bizday(d):
                days.append(d)
            d += timedelta(days=1)
    else:
        d = today
        while len(days) < LOOKBACK_BD:
            if jp_bizday.is_bizday(d):
                days.append(d)
            d -= timedelta(days=1)
    got_days = []
    for d in days:
        got = tdnet_list.fetch_day(d.isoformat())
        time.sleep(SLEEP)
        if got is None:
            log(f"  TDnet {d}: 取れない（残っていない・通信の失敗）")
            continue
        got_days.append(d.isoformat())
        for it in got["items"]:
            items[it["id"]] = {**it, "d": d.isoformat()}
    return items, got_days


def read_tdnet(items, known_ids, state):
    """株式分割に関係する開示を新しく行にする。PDFは split / correction / cancel だけ読む"""
    new, n_pdf = [], 0
    pending = state.setdefault("pending", {})
    for it in sorted(items.values(), key=lambda x: (x["d"], x.get("time") or "", x["id"])):
        if it["id"] in known_ids:
            continue
        kind = X.classify(it["title"])
        if not kind:
            continue
        row = {"src": "tdnet", "id": it["id"], "d": it["d"], "time": it.get("time"),
               "code": code4(it.get("code")), "name": it.get("name"), "place": it.get("place"),
               "title": it["title"], "kind": kind, "pdf": it.get("pdf_url")}
        if kind in PDF_KINDS:
            try:
                ex = X.extract(http(it["pdf_url"]))
                n_pdf += 1
            except Exception as e:
                # 通信の失敗は次の回に読み直す。TDnet の一覧から消えたら諦めて、取れなかった印の行を残す
                tries = pending.get(it["id"], 0) + 1
                pending[it["id"]] = tries
                log(f"  PDFが取れない {it['d']} {row['code']} {e}（{tries}回目）")
                if tries < 5:
                    time.sleep(SLEEP)
                    continue
                ex = {"extract_ok": False, "extract_note": f"PDFが取れない: {e}"}
            time.sleep(SLEEP)
            pending.pop(it["id"], None)
            row.update(ex)
            if row.get("record"):
                row["kenri"], row["ex"] = X.kenri_last(row["record"])
        new.append(row)
    return new, n_pdf


# ─────────────────────────── JPX 月次一覧
def read_jpx(state, only_new_page=False):
    done = set(state.get("jpx_months", []))
    pages = JPX_PAGES[:1] if (only_new_page and done) else JPX_PAGES
    links = {}
    for p in pages:
        try:
            s = http(JPX + p).decode("utf-8", "replace")
        except Exception as e:
            log(f"  JPX {p}: {e}")
            continue
        for u, ym in re.findall(r'href="(/markets/[^"]*_kenri(\d{4})\.pdf)"', s):
            links.setdefault(ym, JPX + u)
        time.sleep(SLEEP)
    rows, months = [], []
    for ym in sorted(links):
        month = f"20{ym[:2]}-{ym[2:]}"
        if month in done:
            continue
        try:
            recs = X.parse_jpx_kenri(X.pdf_text(http(links[ym])))
        except Exception as e:
            log(f"  JPX {month}: 読めない {e}")
            continue
        time.sleep(SLEEP)
        for r in recs:
            # REIT の投資口分割・ETF の受益権分割は種類の行で外してある。区分の見出しでも念のため外す
            if r["kind"] != "split" or r["section"] in JPX_SKIP:
                continue
            rows.append({"src": "jpx", "month": month, "code": r["code"], "name": r["name"],
                         "ex": r["ex"], "record": r["record"], "ratio": r["ratio"],
                         "section": r["section"], "pdf_list": links[ym]})
        months.append(month)
        log(f"  JPX {month}: 分割 {sum(1 for r in recs if r['kind'] == 'split')}件 / 全{len(recs)}行")
    state["jpx_months"] = sorted(done | set(months))
    return rows


# ─────────────────────────── まとめる（読む側）
def build_events(rows):
    """履歴の行 → 分割1件ごとのまとまり。キーは (銘柄, 基準日)。

    TDnet の決議の行が元。訂正の行は、同じ銘柄の直近60日の決議に「書かれていた項目だけ」上書きする。
    中止の行は status を「中止」にする。JPX の行は同じ (銘柄, 基準日) に権利落ち日を足し、
    無ければ JPX だけの1件（発表日なし）として立てる。"""
    td = sorted([r for r in rows if r["src"] == "tdnet"], key=lambda r: (r["d"], r.get("time") or "", r["id"]))
    ev, by_code = {}, {}
    for r in td:
        if r["kind"] == "split":
            e = {"code": r["code"], "name": r.get("name"), "ann": r["d"], "ann_time": r.get("time"),
                 "title": r["title"], "pdf": r.get("pdf"), "id": r["id"],
                 "ratio": r.get("ratio"), "record": r.get("record"), "effective": r.get("effective"),
                 "kenri": r.get("kenri"), "ex": r.get("ex"),
                 "shares_before": r.get("shares_before"), "shares_after": r.get("shares_after"),
                 "extract_ok": r.get("extract_ok"), "note": r.get("extract_note") or "",
                 "src": ["tdnet"], "status": "予定", "fix": []}
            key = (r["code"], r.get("record") or ("id:" + r["id"]))
            if key in ev:            # 同じ分割の続報（「株式分割及び…」が2本出る会社がある）。先の発表を残す
                continue
            ev[key] = e
            by_code.setdefault(r["code"], []).append(e)
        elif r["kind"] in ("correction", "cancel"):
            lo = (date.fromisoformat(r["d"]) - timedelta(days=60)).isoformat()
            cand = [e for e in by_code.get(r["code"], []) if lo <= e["ann"] <= r["d"]]
            if not cand:
                continue
            e = cand[-1]
            if r["kind"] == "cancel":
                e["status"] = "中止"
                e["fix"].append({"d": r["d"], "t": r["title"], "pdf": r.get("pdf")})
                continue
            ch = []
            for k in ("ratio", "record", "effective"):
                if r.get(k) is not None and r[k] != e.get(k):
                    ch.append(f"{k}: {e.get(k)}→{r[k]}")
                    e[k] = r[k]
            if e.get("record"):
                e["kenri"], e["ex"] = X.kenri_last(e["record"])
            e["fix"].append({"d": r["d"], "t": r["title"], "pdf": r.get("pdf"), "changed": ch})
    # キーを基準日で張り直す（訂正で基準日が変わった場合）
    ev = {(e["code"], e.get("record") or ("id:" + e["id"])): e for e in ev.values()}
    for r in rows:
        if r["src"] != "jpx":
            continue
        key = (r["code"], r["record"])
        if key in ev:
            e = ev[key]
            e["ex_jpx"] = r["ex"]          # JPX が公表した権利落ち日（計算した ex と突き合わせる）
            e["section"] = r.get("section")
            if "jpx" not in e["src"]:
                e["src"].append("jpx")
            if e.get("ratio") is None and r.get("ratio"):
                e["ratio"] = r["ratio"]
            continue
        ev[key] = {"code": r["code"], "name": r.get("name"), "ann": None, "ratio": r.get("ratio"),
                   "record": r["record"], "ex": r["ex"], "ex_jpx": r["ex"],
                   "kenri": X.add_bizdays(date.fromisoformat(r["ex"]), -1).isoformat()
                   if r["ex"] >= "2016-01-01" else None,
                   "effective": None, "section": r.get("section"),
                   "src": ["jpx"], "status": "実施済み", "fix": []}
    today = datetime.now(JST).date().isoformat()
    out = []
    for e in ev.values():
        if e["status"] != "中止" and e.get("record"):
            e["status"] = "予定" if e["record"] >= today else "実施済み"
        e["group"] = X.market_group(e.get("section"))
        out.append(e)
    return sorted(out, key=lambda e: (e.get("record") or e.get("ann") or "", e["code"]))


def slim(e, keys=("code", "name", "ann", "ann_time", "ratio", "record", "kenri", "ex", "ex_jpx",
                  "effective", "status", "src", "pdf", "title", "note", "fix", "group",
                  "shares_before", "shares_after")):
    return {k: e[k] for k in keys if e.get(k) not in (None, "", [])}


def main():
    args = sys.argv[1:]
    since = args[args.index("--since") + 1] if "--since" in args else None
    state = load_state()
    rows = load_hist()
    known = {r["id"] for r in rows if r["src"] == "tdnet"}
    known_jpx = {(r["code"], r["record"]) for r in rows if r["src"] == "jpx"}

    items, got_days = tdnet_items(since)
    new_td, n_pdf = read_tdnet(items, known, state)
    new_jpx = []
    if "--no-jpx" not in args:
        new_jpx = [r for r in read_jpx(state, only_new_page=not since)
                   if (r["code"], r["record"]) not in known_jpx]
    append_hist(new_td + new_jpx)
    rows += new_td + new_jpx

    st = state.setdefault("tdnet_days", [])
    state["tdnet_days"] = sorted(set(st) | set(got_days) |
                                 {d["d"] for d in json.loads(TDLIST.read_text(encoding="utf-8")).get("days", [])}
                                 if TDLIST.exists() else set(st) | set(got_days))
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")

    ev = build_events(rows)
    now = datetime.now(JST)
    today = now.date().isoformat()
    lo = (now.date() - timedelta(days=RECENT_DAYS)).isoformat()
    upcoming = [e for e in ev if e["status"] == "予定"]
    upcoming.sort(key=lambda e: (e.get("record") or "9999", e["code"]))
    recent = [e for e in ev if e.get("ann") and e["ann"] >= lo]
    recent.sort(key=lambda e: (e["ann"], e.get("ann_time") or ""), reverse=True)
    by_code = {}
    for e in ev:
        if e["status"] == "中止":
            continue
        by_code.setdefault(e["code"], []).append(
            {k: e[k] for k in ("ann", "ratio", "record", "kenri", "ex", "effective", "status") if e.get(k)})
    other = [slim(dict(code=r["code"], name=r.get("name"), ann=r["d"], title=r["title"], pdf=r.get("pdf"),
                       status=r["kind"]))
             for r in rows if r["src"] == "tdnet" and r["kind"] in ("gratis", "reverse") and r["d"] >= lo]
    tdays = state.get("tdnet_days", [])
    ok = [e for e in ev if "tdnet" in e["src"]]
    out = {
        "updated": now.isoformat(timespec="seconds"),
        "source": "TDnet（適時開示情報閲覧サービス）の開示本文と、JPX「新株落・権利落等一覧」（月次）",
        "note": ("分割比率・基準日・効力発生日は開示本文から機械的に抜いたもの。取れなかった項目は空で、推測では埋めていない。"
                 "権利付き最終日は基準日（休みならその前の営業日）の2営業日前として計算した。"
                 "TDnetの一覧は約1か月で消えるため、発表日が分かるのは tdnet_since 以降の開示だけ。"
                 "それより前の分割は JPX の月次一覧（権利落ち日・基準日・比率）だけで、発表日は持たない。"
                 "原本PDFは保存せず、URLだけを持つ。"),
        "tdnet_since": min(tdays) if tdays else None,
        "jpx_months": [min(state["jpx_months"]), max(state["jpx_months"])] if state.get("jpx_months") else None,
        "counts": {"events": len(ev), "tdnet": len(ok), "jpx_only": sum(1 for e in ev if e["src"] == ["jpx"]),
                   "upcoming": len(upcoming), "recent": len(recent),
                   "extract_ok": sum(1 for e in ok if e.get("extract_ok"))},
        "upcoming": [slim(e) for e in upcoming],
        "recent": [slim(e) for e in recent],
        "other_recent": other,
        "by_code": by_code,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kinds = Counter(r["kind"] for r in new_td)
    log(f"TDnet: 新しく {len(new_td)}行（{dict(kinds)}・PDF {n_pdf}本）／JPX: 新しく {len(new_jpx)}行")
    log(f"→ splits.json: 予定 {len(upcoming)}件・直近{RECENT_DAYS}日の発表 {len(recent)}件・全 {len(ev)}件"
        f"（TDnet {len(ok)}・うち3項目そろい {out['counts']['extract_ok']}）"
        f" {OUT.stat().st_size / 1024:.0f}KB")
    bad = [e for e in ok if not e.get("extract_ok") and e["status"] != "中止"]
    for e in bad[:10]:
        log(f"  ⚠ 欠け {e['ann']} {e['code']} {e.get('name')} {e.get('note')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
