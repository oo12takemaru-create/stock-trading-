# -*- coding: utf-8 -*-
"""決算またぎ帳の過去の分（seed）を手元で1回だけ作る（2026-11-29）→ data/kessan_ato/seed.csv.gz

■ 材料（手元にあるもの・新しい取得なし）
  --fins   J-Quants 決算サマリー（/v2/fins/summary を日ごとに取った pickle。dict: 日付→行のリスト）。複数可。
           無料プランの範囲（2024-06-03〜2026-09-03）。使うのは DiscDate・DiscTime・Code・DocType・CurFYEn だけ。
           決算短信（*FinancialStatements*。REIT は除く）の、同じ銘柄・同じ決算期・同じ四半期の最初の開示。
  --panel  yfinance の5年の日足（px_sanity 済み。{cal, codes, open, close, ...}）
  収集側の docs/kessan_flash_hist・docs/tdnet_hist（2026-08-26〜）も重ねる（同じ銘柄・同じ日は J-Quants を優先）。

■ 出力
  data/kessan_ato/seed.csv.gz … 回ごとの「発表日・時刻・四半期・決算期・起点の日・騰落率（%）」だけ。
  5営業日後まで値が出そろった回だけ入れる（それより新しい回は毎週 kessan_ato.py が収集側の日足で測る）。
  生の日足・決算の数字（売上・利益・予想）は入れない。

使い方（手元で）:
  python -X utf8 kessan_ato_seed.py --fins <fins.pkl> <fins_ext.pkl> --panel <panel.pkl>
"""
import argparse
import pickle
import sys
from collections import Counter

import kessan_ato as K


def events_from_fins(paths):
    d = {}
    for p in paths:
        x = pickle.load(open(p, "rb"))
        for k, v in x.items():
            d.setdefault(k, v)
    seen, out = set(), []
    rows = [r for day in sorted(d) for r in d[day]]
    rows.sort(key=lambda r: (r["DiscDate"], r.get("DiscTime") or ""))
    for r in rows:
        dt = r.get("DocType") or ""
        if "FinancialStatements" not in dt or "REIT" in dt:
            continue
        q = K.norm_q(dt[:2])
        if not q:
            continue
        c = K.code4(r["Code"])
        fy = (r.get("CurFYEn") or "")[:7] or None
        key = (c, fy, q)
        if key in seen:
            continue
        seen.add(key)
        out.append({"code": c, "date": r["DiscDate"], "time": (r.get("DiscTime") or "")[:5] or None,
                    "q": q, "fy": fy, "src": "jq"})
    return out, (max(d) if d else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fins", nargs="+", required=True)
    ap.add_argument("--panel", required=True)
    a = ap.parse_args()
    fe, fins_last = events_from_fins(a.fins)
    K.log(f"J-Quants の決算短信 {len(fe)}回（〜{fins_last}）")
    ev = K.merge_events(fe, K.events_from_flash(K.ROOT), K.events_from_tdnet(K.ROOT))
    K.log(f"重ねたあと {len(ev)}回")
    P = pickle.load(open(a.panel, "rb"))
    px = K.Px(P["cal"], [str(c) for c in P["codes"]], P["open"], P["close"])
    K.log(f"日足 {px.cal[0]}〜{px.cal[-1]}（{px.T}日・{len(px.col)}銘柄）")
    rows, why = [], Counter()
    for k, e in ev.items():
        m = K.measure(px, e)
        if m is None:
            why["測れない（日足が無い・起点が表の外）"] += 1
            continue
        if m["d5"] is None and px.idx(e["date"], e.get("time"), m["timing"])[1] + K.H - 1 >= px.T:
            why["5営業日後がまだ（毎週の計算に回す）"] += 1
            continue
        e.update(m)
        rows.append(e)
    K.write_seed(rows)
    K.log(f"seed {len(rows)}回 → {K.SEED}  {dict(why)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
