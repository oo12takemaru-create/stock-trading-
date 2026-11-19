# -*- coding: utf-8 -*-
"""決算の前（見通しを上げていた会社）— 予定日のその日の記録と、公開用の小さな JSON（2026-11-19）

■ snapshot … data/ledger2/kessan_yotei_asof/YYYY-MM.jsonl に「その日の時点で公表されていた決算発表予定日」を1日1行
  docs/kessan.json（JPX の決算発表予定日）は毎回いちばん新しい届出で置き換わり、docs/kessan_yotei_hist は
  過ぎた日を「過ぎる前の最後の届出」で持つだけなので、「ある日の時点で何日と公表されていたか」は残らない。
  そこで毎日、その日の版（kessan.json の today がその日）から、その日より後・45暦日先までの予定日を {code: 予定日} で残す。
  - 同じ日の行がもうあれば書かない（後から予定日が変わっても書き換えない）
  - kessan.json がその日の版でなければ書かない（別の日の値で記録しない）
  台帳 v2 の検出関数 detect_pre_earnings（ledger2_detect.py）は、この行を「その日に分かっていた予定日」として読む。
  対照（予定日の10・5営業日前の全銘柄）は行にしない。この記録と data/bars から、集計のときにいつでも作れるため。

■ publish … docs/yochou/pre_earnings.json（サイトの予兆レーダー・マイボード・決算予定のページが読む）
  その日の時点で「前回の決算のあとに通期の営業利益の見通しを +5%超 上げていて、決算発表が10営業日以内に予定されている」会社の一覧。
  **過去の統計の数字は入れない。** 当日記録の件数と、答えが出た件数・その平均（全銘柄との差）だけを持つ（答えが出始めるまで 0件・null）。

使い方:
  python -X utf8 pre_earnings.py snapshot              # 毎営業日（ledger-daily.yml・Record v2 の前）
  python -X utf8 pre_earnings.py publish               # 毎営業日（ledger-daily.yml・Commit yochou の前）
  python -X utf8 pre_earnings.py publish --date 2026-11-19 --out <file> --data <dir>   # 試験
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ledger2_detect as L

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "ledger2"
OUT = HERE / "docs" / "yochou" / "pre_earnings.json"
JST = timezone(timedelta(hours=9))
LIST_DAYS = 10                    # 公開の一覧: 決算発表が何営業日以内か

RULE = ("前回の決算発表のあと（その日まで）に、通期の営業利益の見通しを5%を超えて上げる修正を出していて、"
        "JPX に届け出た決算発表の予定日が10営業日以内の会社。前回の決算が2026年10月5日（修正の記録の始まり）より前の会社、"
        "営業利益の見通しが無い会社（金融など）、修正の向きが読めない会社は入れていません。")
NOTE = ("条件に当たった事実の一覧です。すすめる銘柄ではありません。株レーダーは2026年11月19日から、この条件に当たった日と、"
        "その後の決算発表の前日までの値動きを記録しています。")


def jdump(obj, p):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


# ──────────────────────────────────────── snapshot
def snapshot(data, out_dir, today=None):
    kj = data.kessan_now()
    d = today or (kj or {}).get("today")
    if not d:
        print("kessan.json が読めない（記録しない）")
        return 0
    rec = L.yotei_from_kessan(kj, d)
    if not rec:
        print(f"kessan.json がその日（{d}）の版ではない（記録しない）")
        return 0
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    f = out_dir / f"{d[:7]}.jsonl"
    if any(r.get("d") == d for r in L.jsonl(f)):
        print(f"{d} の予定日の記録はもうある（書き換えない）")
        return 0
    rec["recorded_at"] = datetime.now(JST).isoformat(timespec="seconds")
    with open(f, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"{d} の予定日の記録: {len(rec['items'])}社（{d} の翌日〜{L.YOTEI_KEEP_DAYS}暦日先・JPX の版 {rec.get('jpx_asof')}）")
    return 0


# ──────────────────────────────────────── publish
def record_summary(data_dir):
    """当日記録の件数と答え（data/ledger2/events・scores_pre）"""
    data_dir = Path(data_dir)
    ev = {}
    for f in sorted((data_dir / "events").glob("*.jsonl")):
        for r in L.jsonl(f):
            if (r.get("event_type") or "").startswith("pre_earnings_up_") and r.get("origin") == "live":
                ev[r["event_id"]] = r
    sc = {}
    for f in sorted((data_dir / "scores_pre").glob("*.jsonl")):
        for r in L.jsonl(f):
            if r.get("event_id") in ev and r["event_id"] not in sc:
                sc[r["event_id"]] = r
    out = {"since": L.PRE_EARN_START, "windows": {}}
    for n in L.PRE_EARN_NS:
        ids = [k for k, r in ev.items() if r["event_type"] == f"pre_earnings_up_{n}"]
        scored = [sc[k] for k in ids if k in sc and sc[k].get("status") == "scored"]

        def avg(rows, key="vs_all"):
            v = [r[key] for r in rows if r.get(key) is not None]
            return round(sum(v) / len(v), 2) if v else None
        on = [r for r in scored if r.get("on_schedule")]
        sh = [r for r in scored if not r.get("on_schedule")]
        out["windows"][str(n)] = {
            "recorded": len(ids), "scored": len(scored),
            "avg_vs_all": avg(scored), "avg_vs_topix": avg(scored, "ret"),
            "on_schedule": {"scored": len(on), "avg_vs_all": avg(on)},
            "shifted": {"scored": len(sh), "avg_vs_all": avg(sh)},
            "invalid": sum(1 for k in ids if k in sc and sc[k].get("status") == "invalid"),
        }
    return out


def build(d, data, data_dir, now=None):
    rows, asof = L.pre_earnings_state(d, data, max_left=LIST_DAYS)
    if asof is None:
        return None
    meta = L.kessan_meta(data.kessan_now())
    items, cnt = [], {}
    for r in rows:
        cnt[r["status"]] = cnt.get(r["status"], 0) + 1
        if r["status"] != "hit":
            continue
        nm, q = meta.get(r["code"], ("", ""))
        last = r["up"][-1]
        items.append({
            "code": r["code"], "name": nm or None, "q": q or None,
            "yotei": r["yotei"], "days_left": r["left"],
            "rev": [{"d": x["d"], "pct": x["pct"]} for x in r["up"]],
            "rev_last": last["d"], "rev_last_pct": last["pct"],
            "note": f"見通しを上げていて、決算発表まであと{r['left']}営業日",
        })
    return {
        "v": 1, "updated": now or datetime.now(JST).isoformat(timespec="seconds"),
        "date": d, "jpx_asof": asof.get("jpx_asof"),
        "rule": RULE, "note": NOTE,
        "items": items,
        "checked": {"upcoming": len(rows), "hit": cnt.get("hit", 0), "no_raise": cnt.get("no_raise", 0),
                    "prev_before_records": cnt.get("prev_before_records", 0),
                    "prev_unknown": cnt.get("prev_unknown", 0), "unknown_dir": cnt.get("unknown_dir", 0),
                    "recent_kessan": cnt.get("recent_kessan", 0)},
        "record": record_summary(data_dir),
    }


def latest_asof_day(data, data_dir):
    """記録のある最新の日（予定日の記録の行・または今の kessan.json の日）"""
    days = []
    for f in sorted((Path(data_dir) / "kessan_yotei_asof").glob("*.jsonl")):
        days += [r["d"] for r in L.jsonl(f) if r.get("d")]
    kj = data.kessan_now() or {}
    if kj.get("today"):
        days.append(kj["today"])
    return max(days) if days else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("snapshot", "publish"))
    ap.add_argument("--date", help="この日（既定: kessan.json の today）")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--docs", help="docs/ を差し替える（試験用）")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    data = L.Sources(docs=a.docs) if a.docs else L.Sources()
    data.yotei_dir = Path(a.data) / "kessan_yotei_asof"
    if a.cmd == "snapshot":
        return snapshot(data, Path(a.data) / "kessan_yotei_asof", a.date)
    d = a.date or latest_asof_day(data, a.data)
    if not d:
        print("予定日の記録が無いので書かない")
        return 0
    obj = build(d, data, a.data)
    if not obj:
        print(f"{d} の予定日の記録が無いので書かない")
        return 0
    L_ = obj["checked"]
    jdump(obj, a.out)
    print(f"pre_earnings: {d}・該当 {len(obj['items'])}社／予定日が10営業日以内 {L_['upcoming']}社"
          f"（上げていない {L_['no_raise']}・前回の決算が記録より前 {L_['prev_before_records']}・前回の日が不明 {L_['prev_unknown']}"
          f"・向きが読めない {L_['unknown_dir']}・前回の決算が近い {L_['recent_kessan']}）・{Path(a.out).stat().st_size:,}B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
