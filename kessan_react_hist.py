# -*- coding: utf-8 -*-
"""決算反応の蓄積 → docs/kessan_react_hist/{code}.json ／ all.json ／ index.json

■ 何のためか（起動文 積上⑤ 2026-10-08）
  銘柄ページの「この銘柄は決算でどう動いてきたか」の履歴。決算発表ごとに1件を積む。
  元は決算速報（kessan_flash.json と kessan_flash_hist/）。記録は 2026-08-26 から
  （それより前の短信は TDnet の公開一覧から消えていて取れない）。決算のたびに増える。

■ 反応の測り方は kessan_react.py（決算の答え合わせ）と同じ
  前日終値 → 反応日の始値＝ギャップ、→ 反応日の終値＝騰落、出来高は反応日 ÷ 直前20営業日の平均。
  反応日は発表の時刻で決める:
    15:30 以降（引け後）… 翌営業日。前日終値＝発表日の終値（kessan_react と同じ）
    9:00 より前（寄り前）… 発表日。前日終値＝発表日の前営業日の終値
    9:00〜15:30（場中）  … 発表日。ギャップは発表より前の値なので出さない（null）。騰落だけ
■ 5日後・20日後は台帳・空売りの答え合わせと同じ
  起点＝発表日の翌営業日の始値、終点＝そこから h 営業日後の終値。

■ 追記のみ
  1件は「発表日・期・決算期」で一意。反応と5日後・20日後は値が出たときに埋めるだけで、
  一度入った値も短信の要点も書き換えない。短信の要点の null（予想修正の向きが不明など）は null のまま。

使い方: python kessan_react_hist.py
"""
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
OUT = DOCS / "kessan_react_hist"
JST = timezone(timedelta(hours=9))
H = (5, 20)
GIVE_UP_DAYS = 60       # 価格が取れないまま60暦日たった件は、反応・騰落を打ち切る
FLASH_KEYS = ("q", "fy", "sales_yoy", "op_yoy", "np_yoy", "progress_op", "fc_rev", "fc_rev_flag",
              "fc_range", "verdict")
FILL_KEYS = {"react_date", "prev_close", "open", "close", "gap", "chg", "vr", "r5", "r20", "final", "note"}


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def timing(t):
    if not t:
        return "引け後"         # 時刻が無ければ多数派（引け後）として扱う
    if t >= "15:30":
        return "引け後"
    if t < "09:00":
        return "寄り前"
    return "場中"


def load_flash():
    """発表日 → 決算速報の一覧（kessan_flash.json と月別履歴を合わせる）"""
    days = {}
    for f in sorted((DOCS / "kessan_flash_hist").glob("*.json")):
        for d in (jload(f, {}) or {}).get("days", []):
            days.setdefault(d["d"], d.get("items", []))
    for d in (jload(DOCS / "kessan_flash.json", {}) or {}).get("days", []):
        days[d["d"]] = d.get("items", [])        # 直近60日は新しい方（訂正を反映済み）
    return days


def tick(c):
    c = c.strip()
    return (c[:4] if len(c) == 5 and c.endswith("0") else c) + ".T"


def fetch(codes, start):
    import pandas as pd
    import yfinance as yf
    px = {}
    tks = sorted({tick(c) for c in codes})
    for i in range(0, len(tks), 60):
        chunk = tks[i:i + 60]
        try:
            df = yf.download(" ".join(chunk), start=start, progress=False, auto_adjust=False,
                             threads=False, group_by="ticker")
        except Exception as e:
            print(f"  価格取得失敗: {e}", file=sys.stderr)
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) < 2:
                    continue
                px[t] = {k.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["Close"]), float(r["Volume"] or 0))
                         for k, r in sub.iterrows()}
            except Exception:
                continue
        time.sleep(1.0)
    return px


def fill(e, bars, today):
    """反応・5日後・20日後を、まだ空いているところだけ埋める"""
    days = sorted(bars)
    d = e["date"]
    if e["timing"] == "引け後":
        after = [x for x in days if x > d]
        react = after[0] if after else None
        prev = d if d in bars else max([x for x in days if x <= d], default=None)
    else:
        react = d if d in bars else None
        prev = max([x for x in days if x < d], default=None)
    if e.get("chg") is None and react and prev:
        pc = bars[prev][1]
        o, c, v = bars[react]
        i = days.index(react)
        vols = [bars[x][2] for x in days[max(0, i - 20):i] if bars[x][2]]
        e.update({"react_date": react, "prev_close": round(pc, 2), "open": round(o, 2), "close": round(c, 2),
                  "gap": round((o / pc - 1) * 100, 2) if e["timing"] != "場中" else None,
                  "chg": round((c / pc - 1) * 100, 2),
                  "vr": round(v / (sum(vols) / len(vols)), 1) if vols and v else None})
    after = [x for x in days if x > d]
    if after:
        o = bars[after[0]][0]
        for h in H:
            if e.get(f"r{h}") is None and len(after) > h and o:
                e[f"r{h}"] = round((bars[after[h]][1] / o - 1) * 100, 2)
    if e.get("r20") is not None and e.get("chg") is not None:
        e["final"] = True
    elif (today - date.fromisoformat(d)).days > GIVE_UP_DAYS:
        e["final"] = True
        e["note"] = "価格が取れないまま60日たったので打ち切り"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).date()
    store = {}
    for f in OUT.glob("*.json"):
        if f.stem not in ("all", "index"):
            store[f.stem] = jload(f)
    before = json.dumps(store, sort_keys=True)

    # 1) 決算速報から、まだ無い発表を足す（8/26 以降を初回に遡る）
    added = 0
    for d, items in sorted(load_flash().items()):
        for it in items:
            c = it["c"]
            rec = store.setdefault(c, {"code": c, "name": it.get("n"), "items": []})
            key = f"{d}|{it.get('q')}|{it.get('fy')}"
            if any(x["key"] == key for x in rec["items"]):
                continue
            rec["items"].append({"key": key, "date": d, "time": it.get("t"), "timing": timing(it.get("t")),
                                 **{k: it.get(k) for k in FLASH_KEYS},
                                 "react_date": None, "prev_close": None, "open": None, "close": None,
                                 "gap": None, "chg": None, "vr": None, "r5": None, "r20": None,
                                 "final": False})
            added += 1
    print(f"新しい決算発表 +{added}件")

    # 2) 未確定の件に値を入れる
    todo = [(c, e) for c, r in store.items() for e in r["items"] if not e.get("final")]
    if todo:
        start = (date.fromisoformat(min(e["date"] for _, e in todo)) - timedelta(days=45)).isoformat()
        px = fetch({c for c, _ in todo}, start)
        print(f"  未確定 {len(todo)}件 / 価格が取れた銘柄 {len(px)}")
        for c, e in todo:
            bars = px.get(tick(c))
            if bars:
                fill(e, bars, today)
            elif (today - date.fromisoformat(e["date"])).days > GIVE_UP_DAYS:
                e["final"] = True
                e["note"] = "価格が取れないまま60日たったので打ち切り"

    # 3) 追記のみの確認: 埋める欄以外が変わっていないこと
    old = json.loads(before)
    for c, r in old.items():
        for a, b in zip(r["items"], store[c]["items"]):
            if {k: v for k, v in a.items() if k not in FILL_KEYS} != {k: v for k, v in b.items() if k not in FILL_KEYS}:
                sys.exit(f"::error::{c} {a['key']} の埋める欄以外が変わった。書き込みを止めます")
            for k in FILL_KEYS - {"final", "note"}:
                if a.get(k) is not None and a.get(k) != b.get(k):
                    sys.exit(f"::error::{c} {a['key']} の {k} が書き換わった（{a[k]}→{b.get(k)}）")

    # 4) 書く: 銘柄ごと＋サイト用のまとめ（all.json）＋監視用（index.json）
    for c, r in store.items():
        r["items"].sort(key=lambda x: x["date"])
        (OUT / f"{c}.json").write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    cols = ["date", "time", "timing", "q", "react_date", "gap", "chg", "vr", "r5", "r20",
            "sales_yoy", "op_yoy", "progress_op", "fc_rev", "fc_rev_flag", "verdict"]
    allj = {"updated": datetime.now(JST).isoformat(timespec="seconds"), "since": "2026-08-26", "cols": cols,
            "items": {c: [[e.get(k) for k in cols] for e in r["items"]] for c, r in sorted(store.items())}}
    (OUT / "all.json").write_text(json.dumps(allj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    n = sum(len(r["items"]) for r in store.values())
    (OUT / "index.json").write_text(json.dumps({
        "updated": allj["updated"], "since": "2026-08-26", "stocks": len(store), "events": n,
        "reacted": sum(1 for r in store.values() for e in r["items"] if e.get("chg") is not None),
        "r20": sum(1 for r in store.values() for e in r["items"] if e.get("r20") is not None),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"銘柄 {len(store)} / 決算 {n}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
