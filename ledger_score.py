# -*- coding: utf-8 -*-
"""シグナル台帳の採点 → docs/ledger/*.jsonl の r5/r20/r60/final と docs/ledger/summary.json

■ 起点と終点は karauri_score.py と同じ
  起点 = 判定日（date）の**翌営業日の始値**。判定は引け後や朝に出るものが混ざるが、
  読者が確実に取引できる最初の値に揃える（空売りの答え合わせと同じ考え方）。
  終点 = 起点日から h 営業日後の終値。r5/r20/r60 は (終値 ÷ 始値 − 1) × 100（%）。
  銘柄は「コード.T」、市場全体（MKT）は日経平均（^N225）で測る。

■ 書き換えるのは r5/r20/r60/final だけ
  台帳は追記のみ。採点の欄以外が1文字でも変わっていたら書き込まずに止める。

■ 価格が取れないまま 120暦日たった行は final=true（r は null のまま・"note" を付ける）
  上場廃止・コード変更などで永久に埋まらない行が、毎日の取得対象に残り続けないように。

■ summary.json は内部確認用（サイトには出さない）
  判定元ごとの件数・r20 が出た件数・r20 の平均・r20 がプラスだった割合。
"""
import json
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
LEDGER = HERE / "docs" / "ledger"
JST = timezone(timedelta(hours=9))
H = (5, 20, 60)
GIVE_UP_DAYS = 120
SCORE_KEYS = {"r5", "r20", "r60", "final", "note"}


def tick(code):
    if code == "MKT":
        return "^N225"
    c = code.strip()
    return (c[:4] if len(c) == 5 and c.endswith("0") else c) + ".T"


def load():
    files = {}
    for f in sorted(LEDGER.glob("*.jsonl")):
        files[f] = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    return files


def fetch(codes, start):
    import pandas as pd
    import yfinance as yf
    px = {}
    tks = sorted({tick(c) for c in codes})
    B = 60
    for i in range(0, len(tks), B):
        chunk = tks[i:i + B]
        try:
            df = yf.download(" ".join(chunk), start=start, progress=False, auto_adjust=False,
                             threads=False, group_by="ticker")
        except Exception as e:
            print(f"  価格取得失敗: {e}", file=sys.stderr)
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                o, cl = sub["Open"].dropna(), sub["Close"].dropna()
                if len(cl) < 2:
                    continue
                px[t] = ({k.strftime("%Y-%m-%d"): float(v) for k, v in o.items()},
                         {k.strftime("%Y-%m-%d"): float(v) for k, v in cl.items()})
            except Exception:
                continue
        time.sleep(1.0)
    return px


def score_row(r, px, today):
    """r を採点して、変わったら True"""
    p = px.get(tick(r["code"]))
    before = (r.get("r5"), r.get("r20"), r.get("r60"), r.get("final"))
    if p:
        opens, closes = p
        after = sorted(d for d in closes if d > r["date"])
        if after:
            o = opens.get(after[0])
            for h in H:
                if r.get(f"r{h}") is None and len(after) > h and o:
                    c = closes.get(after[h])
                    if c:
                        r[f"r{h}"] = round((c / o - 1) * 100, 2)
    if r.get("r60") is not None:
        r["final"] = True
    elif (today - date.fromisoformat(r["date"])).days > GIVE_UP_DAYS:
        r["final"] = True
        r["note"] = "価格が取れないまま120日たったので採点を打ち切り"
    return before != (r.get("r5"), r.get("r20"), r.get("r60"), r.get("final"))


def summary(files, today):
    agg = defaultdict(lambda: {"n": 0, "n_r20": 0, "sum": 0.0, "up": 0})
    for rows in files.values():
        for r in rows:
            if r.get("fix_of"):
                continue
            a = agg[r["source"]]
            a["n"] += 1
            if r.get("r20") is not None:
                a["n_r20"] += 1
                a["sum"] += r["r20"]
                a["up"] += r["r20"] > 0
    out = {}
    for s, a in sorted(agg.items()):
        out[s] = {"count": a["n"], "scored_r20": a["n_r20"],
                  "r20_mean": round(a["sum"] / a["n_r20"], 2) if a["n_r20"] else None,
                  "r20_up_rate": round(a["up"] / a["n_r20"] * 100, 1) if a["n_r20"] else None}
    return {"updated": datetime.now(JST).isoformat(timespec="seconds"),
            "note": "内部確認用。サイトには出さない。r20＝判定日の翌営業日始値→20営業日後終値（%）",
            "total": sum(v["count"] for v in out.values()), "sources": out}


def main():
    files = load()
    today = datetime.now(JST).date()
    todo = [r for rows in files.values() for r in rows if not r.get("final")]
    print(f"台帳 {sum(len(v) for v in files.values())}件 / 採点待ち {len(todo)}件")
    if todo:
        start = (date.fromisoformat(min(r["date"] for r in todo)) - timedelta(days=10)).isoformat()
        px = fetch({r["code"] for r in todo}, start)
        print(f"  価格が取れた銘柄 {len(px)} / {len({tick(r['code']) for r in todo})}")
        changed = sum(score_row(r, px, today) for r in todo)
        print(f"  採点を更新 {changed}件")

    # 採点の欄以外が変わっていないことを確かめてから書く（追記のみの約束）
    orig = load()
    for f, rows in files.items():
        old = orig[f]
        if len(old) != len(rows):
            sys.exit(f"::error::{f.name} の行数が変わった（{len(old)}→{len(rows)}）。書き込みを止めます")
        for a, b in zip(old, rows):
            if {k: v for k, v in a.items() if k not in SCORE_KEYS} != \
               {k: v for k, v in b.items() if k not in SCORE_KEYS}:
                sys.exit(f"::error::{a.get('id')} の採点以外の欄が変わった。書き込みを止めます")
        f.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows),
                     encoding="utf-8")

    sm = summary(files, today)
    (LEDGER / "summary.json").write_text(json.dumps(sm, ensure_ascii=False, indent=1), encoding="utf-8")
    print("summary: " + " ".join(f"{k}={v['count']}件(r20 {v['scored_r20']})" for k, v in sm["sources"].items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
