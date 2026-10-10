# -*- coding: utf-8 -*-
"""株レーダーの閲覧数・面白い率を Supabase から書き出す（進化① 2026-10-10）

■ 出すもの（どちらも **サイトにはまだ出さない**。11月の「注目度の高い銘柄」と、ページの良し悪しの判断材料）
  docs/views.json     毎日。直近7日・30日の閲覧数の上位、昨日の急上昇（前の7日の平均と比べて）
  docs/feedback.json  週1回（月曜）。ページの型ごとの「面白い／いまいち」と面白い率（直近28日）

■ 元データ: ルールトレード会員基盤の Supabase の kr_views / kr_feedback（patch_2026-10-10_kaburadar.sql）。
  書き込みは kaburadar の Vercel 関数 api/kr.js だけ。ここは読むだけ（service role・GitHub Secrets）
使い方: python -X utf8 kr_export.py [--feedback]
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
TOP = 100


def get(table, query):
    base, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not base or not key:
        raise SystemExit("::error::SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が無い")
    rows, off = [], 0
    while True:
        req = urllib.request.Request(f"{base}/rest/v1/{table}?{query}&limit=1000&offset={off}",
                                     headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=60) as r:
            part = json.loads(r.read().decode("utf-8"))
        rows += part
        if len(part) < 1000:
            return rows
        off += 1000


def names():
    """証券コード → 銘柄名（十倍株ユニバース。表示用に添えるだけ）"""
    import csv
    out = {}
    try:
        with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[r["code"]] = r["name"]
    except Exception:
        pass
    return out


def jsave(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def views():
    today = datetime.now(JST).date()
    yday = today - timedelta(days=1)
    since = (today - timedelta(days=31)).isoformat()
    rows = get("kr_views", f"select=code,date,count&date=gte.{since}")
    nm = names()
    by = {}
    for r in rows:
        by.setdefault(r["code"], {})[r["date"]] = r["count"]

    def total(days):
        lo = (yday - timedelta(days=days - 1)).isoformat()
        t = {c: sum(n for d, n in v.items() if lo <= d <= yday.isoformat()) for c, v in by.items()}
        return [{"code": c, "name": nm.get(c), "views": n} for c, n in sorted(t.items(), key=lambda kv: -kv[1]) if n > 0][:TOP]

    surge = []
    for c, v in by.items():
        y = v.get(yday.isoformat(), 0)
        prev = [v.get((yday - timedelta(days=k)).isoformat(), 0) for k in range(1, 8)]
        base = sum(prev) / 7
        if y >= 5 and y >= base * 3:          # 少なすぎる数の倍率は意味が無いので5回以上
            surge.append({"code": c, "name": nm.get(c), "yesterday": y, "avg7": round(base, 1),
                          "ratio": round(y / base, 1) if base else None})
    surge.sort(key=lambda x: (-(x["ratio"] or 1e9), -x["yesterday"]))
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"), "date": yday.isoformat(),
           "note": "株レーダーのページ別閲覧数（ボット・30分以内の再読込を除く）。サイトにはまだ出していない",
           "pages": len(by), "total_yesterday": sum(v.get(yday.isoformat(), 0) for v in by.values()),
           "top7": total(7), "top30": total(30), "surge": surge[:50]}
    jsave(DOCS / "views.json", out)
    print(f"views.json: {out['pages']}ページ・昨日 {out['total_yesterday']}閲覧・急上昇 {len(surge)}")


def feedback():
    today = datetime.now(JST).date()
    since = (today - timedelta(days=28)).isoformat()
    rows = get("kr_feedback", f"select=page,choice,date,count&date=gte.{since}")
    agg = {}
    for r in rows:
        a = agg.setdefault(r["page"], {"good": 0, "meh": 0})
        a[r["choice"]] += r["count"]
    pages = [{"page": p, "good": a["good"], "meh": a["meh"], "n": a["good"] + a["meh"],
              "rate": round(a["good"] / (a["good"] + a["meh"]) * 100, 1)} for p, a in agg.items() if a["good"] + a["meh"]]
    pages.sort(key=lambda x: -x["n"])
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"), "from": since, "to": today.isoformat(),
           "note": "各ページの「面白い(good)／いまいち(meh)」直近28日。rate=面白い率%。サイトには出していない",
           "pages": pages}
    jsave(DOCS / "feedback.json", out)
    print(f"feedback.json: {len(pages)}ページ")


if __name__ == "__main__":
    views()
    if "--feedback" in sys.argv or datetime.now(JST).weekday() == 0 or not (DOCS / "feedback.json").exists():
        feedback()
