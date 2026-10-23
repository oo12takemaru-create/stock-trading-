# -*- coding: utf-8 -*-
"""決算速報の履歴を、TDnet にまだ残っている短信から読み直す（保守用・手で1回ずつ走らせる）

■ いつ使うか
  kessan_flash.py の読み取りを直したとき。2026-10-24 に facts() の不具合
  （xsi:nil の自己終了タグ・nonNumeric の入れ子で値を取り違える）を直した際に作った。
  TDnet は開示から31日しか置かないので、**それより古い短信は読み直せない**（その行は今のまま残し、件数を出す）。

■ やること
  1. docs/kessan_flash_hist/*.json の各行について、文書ID から XBRL の zip を取り直して extract() し直す。
     訂正短信で上書き済みの行は、訂正短信の zip を読む（元の短信に戻さない）
  2. 判定（verdict）・進捗率・予想修正の向き（fc_rev）を、履歴の古い順に組み直す。
     fc_rev は「その行より前に見た同じ銘柄×期の予想」とだけ比べる
  3. 同じ zip から配当の状況を docs/dividend_hist/ に追記（kessan_div.py・既にある文書IDは書かない）
  4. 決算反応カルテ（docs/kessan_react_hist/{code}.json）の「短信の要点」欄を、直した値に合わせる。
     値動きの欄（反応・5日後・20日後）は触らない
  変わった行は --report に JSON で書き出す（何が・なぜ変わったかを確かめるため）

使い方:
  python -X utf8 kessan_flash_reread.py [--cache DIR] [--report out.json]
    --cache  落とした zip の置き場（{文書ID}.zip）。無ければ TDnet から1秒間隔で取る
"""
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import kessan_div
from kessan_flash import (HIST, JST, extract, fc_rev, fetch, fy_from_title, progress,
                          record_div, verdict)

HERE = Path(__file__).parent
KARTE = HERE / "docs" / "kessan_react_hist"
BASE = "https://www.release.tdnet.info/inbs/"
EXTRACT_KEYS = ("q", "cons", "std", "sales", "op", "ord", "np", "sales_yoy", "op_yoy", "np_yoy",
                "fc_sales", "fc_op", "fc_np", "fy", "fc_rev_flag", "fc_src", "fc_range")
KARTE_KEYS = ("q", "fy", "sales_yoy", "op_yoy", "np_yoy", "progress_op", "fc_rev", "fc_rev_flag",
              "fc_range", "verdict")


def zip_of(doc_id, cache):
    """文書ID（PDF名の数字）→ zip。PDF は 1401…、XBRL は 0812… で後ろが共通"""
    if cache:
        p = Path(cache) / f"{doc_id}.zip"
        if p.exists() and p.stat().st_size:
            return p.read_bytes()
    try:
        b = fetch(BASE + "0812" + doc_id[4:] + ".zip")
        time.sleep(1.0)
    except Exception:
        return None
    if cache:
        (Path(cache) / f"{doc_id}.zip").write_bytes(b)
    return b


def main():
    a = sys.argv[1:]
    cache = a[a.index("--cache") + 1] if "--cache" in a else None
    report = a[a.index("--report") + 1] if "--report" in a else None

    months = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(HIST.glob("*.json"))}
    rows = [(day["d"], it) for m in months.values() for day in m["days"] for it in day["items"]]
    rows.sort(key=lambda x: (x[0], x[1].get("t") or "", x[1]["c"]))

    stat = {"行": len(rows), "読み直し": 0, "取り直せない": 0, "REIT等で読まない": 0, "値が変わった行": 0}
    changed, gone, div_rows = [], [], []
    prev_fc = {}
    for d, it in rows:
        fixed = it.get("flag") == "訂正" and it.get("fix_pdf")
        doc = it["fix_pdf"].rsplit("/", 1)[1][:-4] if fixed else it.get("id")
        zb = zip_of(doc, cache) if doc else None
        before = {k: it.get(k) for k in (*EXTRACT_KEYS, "verdict", "progress_op", "fc_rev")}
        if zb is None:
            stat["取り直せない"] += 1
            gone.append((d, it["c"]))
        else:
            try:
                r = extract(zb)
            except Exception as e:
                r = {"skip": str(e)[:60]}
            if r.get("skip"):
                stat["REIT等で読まない"] += 1
            else:
                stat["読み直し"] += 1
                if not r.get("fy"):
                    r["fy"] = it.get("fy")        # 表題から拾った期（XBRLに無い様式）を残す
                for k in EXTRACT_KEYS:
                    if k in r:
                        it[k] = r[k]
                    else:
                        it.pop(k, None)
                item = {"code": it["c"], "name": it.get("n"), "time": it.get("t"), "id": doc,
                        "title": None, "pdf_url": it.get("fix_pdf") if fixed else it.get("pdf")}
                div_rows += record_div(zb, item, it.get("fix_date") if fixed else d, it,
                                       flag="訂正" if fixed else None)
                it["verdict"] = verdict(it.get("sales_yoy"), it.get("op_yoy"))
                it["progress_op"] = progress(it)
                it["fc_rev"] = fc_rev(it, prev_fc.get((it["c"], it.get("fy"))))
        if it.get("fc_op") is not None and it.get("fy"):
            prev_fc[(it["c"], it["fy"])] = it["fc_op"]
        after = {k: it.get(k) for k in before}
        if after != before:
            stat["値が変わった行"] += 1
            changed.append({"d": d, "c": it["c"], "n": it.get("n"), "id": it.get("id"),
                            "diff": {k: [before[k], after[k]] for k in before if before[k] != after[k]}})

    now = datetime.now(JST).isoformat(timespec="seconds")
    for mon, m in months.items():
        m["updated"] = now
        m["count"] = sum(x["count"] for x in m["days"])
        (HIST / f"{mon}.json").write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")),
                                          encoding="utf-8")
    n_div = kessan_div.append(div_rows)

    # ---- 決算反応カルテの「短信の要点」欄を合わせる（値動きの欄は触らない）
    by_key = {}
    for d, it in rows:
        by_key.setdefault((d, it["c"]), []).append(it)
    k_changed = 0
    for p in sorted(KARTE.glob("*.json")):
        if p.stem in ("all", "index"):
            continue
        rec = json.loads(p.read_text(encoding="utf-8"))
        dirty = False
        for e in rec.get("items", []):
            cands = by_key.get((e["date"], rec["code"])) or []
            # 同じ日に同じ銘柄が2本（訂正など）ある場合は、期と四半期が合うものを選ぶ
            hit = [x for x in cands if (x.get("q"), x.get("fy")) == (e.get("q"), e.get("fy"))] or \
                  (cands if len(cands) == 1 else [])
            if not hit:
                continue
            new = {k: hit[0].get(k) for k in KARTE_KEYS}
            if any(e.get(k) != new[k] for k in KARTE_KEYS):
                e.update(new)
                e["key"] = f"{e['date']}|{e.get('q')}|{e.get('fy')}"
                dirty = True
                k_changed += 1
        if dirty:
            p.write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(" / ".join(f"{k}={v}" for k, v in stat.items()))
    print(f"配当の記録 +{n_div}行 / 決算反応カルテの要点を直した件数 {k_changed}")
    if gone:
        print(f"取り直せない行の開示日: {gone[0][0]}〜{gone[-1][0]}")
    if report:
        Path(report).write_text(json.dumps({"stat": stat, "karte_changed": k_changed, "div_added": n_div,
                                            "gone": gone, "changed": changed},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
