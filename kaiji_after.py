# -*- coding: utf-8 -*-
"""適時開示のあとの値動き → docs/kaiji_after/YYYY-MM.json（株レーダー ③今日の適時開示まとめ・⑤自社株買い・⑥優待・⑦上方修正 の答え合わせ）

起動文: kaburadar\\起動文_実務_日付つき一覧①②③_決算予定とTOBと今日の開示_2026-11-11.md

■ 何を出すか（機械の計算だけ。理由は推定しない・外れも消さない）
  その日に TDnet で開示した会社（コード）ごとに
    b  基準の日: その日の最初の開示が 15:30 より前（取引時間中）なら前の営業日、15:30 以降ならその日
    n1 基準の日の終値 → 次の営業日の終値 の騰落（%）
    n5 基準の日の終値 → 5営業日後の終値 の騰落（%）
  まだ日がたっていない値は入れない（次の回に埋める）。一度入れた値は変えない。
■ 材料
  docs/tdnet_hist/YYYY-MM-DD.json（tdnet_list.py が残す1日1ファイル）。無い日は docs/tdnet_list.json（直近10営業日）
  .cache/ohlc_recent.pkl（movers_daily.py が同じジョブで書く直近30本の日足＝Yahoo に取りに行かない）
  ETF・REIT など日足の無いコードは飛ばす。

使い方:
  python -X utf8 kaiji_after.py
"""
import json
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
HIST = DOCS / "tdnet_hist"
TDLIST = DOCS / "tdnet_list.json"
OUT = DOCS / "kaiji_after"
CACHE = ROOT / ".cache" / "ohlc_recent.pkl"
CLOSE_T = "15:30"


def code4(c):
    c = (c or "").strip()
    return c[:4] if len(c) == 5 else c


def load_days():
    """{日付: {code: 最初の時刻}}"""
    days = {}
    if HIST.exists():
        for f in sorted(HIST.glob("????-??-??.json")):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            cols = j.get("cols") or []
            ic, it = cols.index("c"), cols.index("t")
            for r in j.get("rows") or []:
                c, t = code4(r[ic]), r[it] or "99:99"
                cur = days.setdefault(j["d"], {})
                if c not in cur or t < cur[c]:
                    cur[c] = t
    if TDLIST.exists():
        try:
            for d in json.loads(TDLIST.read_text(encoding="utf-8")).get("days") or []:
                if d["d"] in days:
                    continue
                cur = days.setdefault(d["d"], {})
                for x in d.get("items") or []:
                    c, t = code4(x.get("code")), x.get("time") or "99:99"
                    if c not in cur or t < cur[c]:
                        cur[c] = t
        except Exception as e:
            print(f"tdnet_list.json が読めない: {e}", file=sys.stderr)
    return days


def closes():
    """{code: [(日付, 終値), …]}（古い順）"""
    with open(CACHE, "rb") as f:
        d = pickle.load(f)
    out = {}
    for t, df in d["data"].items():
        if "Close" not in df:
            continue
        s = df["Close"].dropna()
        out[t.replace(".T", "")] = [(ix.strftime("%Y-%m-%d"), float(v)) for ix, v in s.items() if v and v > 0]
    return out


def main():
    if not CACHE.exists():
        print("日足のキャッシュが無い（movers_daily.py の後で動く。今回は何もしない）")
        return 0
    days = load_days()
    if not days:
        print("開示の一覧が無い（何もしない）")
        return 0
    px = closes()
    cal = sorted({d for v in px.values() for d, _ in v})
    if not cal:
        print("日足が空（何もしない）")
        return 0
    lo = cal[0]
    OUT.mkdir(parents=True, exist_ok=True)
    stores = {}
    filled = 0
    for d, codes in sorted(days.items()):
        if d < lo or d > cal[-1]:
            continue
        ym = d[:7]
        if ym not in stores:
            f = OUT / f"{ym}.json"
            try:
                stores[ym] = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            except Exception:
                stores[ym] = {}
            stores[ym].setdefault("days", {})
        day = stores[ym]["days"].setdefault(d, {})
        for c, t in codes.items():
            ser = px.get(c)
            if not ser:
                continue
            idx = {dd: i for i, (dd, _) in enumerate(ser)}
            if t < CLOSE_T:
                prev = [dd for dd, _ in ser if dd < d]
                if not prev:
                    continue
                bi = idx[prev[-1]]
            else:
                if d not in idx:
                    continue
                bi = idx[d]
            bd, bc = ser[bi]
            rec = day.get(c) or {"b": bd}
            for k, h in (("n1", 1), ("n5", 5)):
                if rec.get(k) is None and bi + h < len(ser):
                    rec[k] = round((ser[bi + h][1] / bc - 1) * 100, 2)
                    filled += 1
            if len(rec) > 1:
                day[c] = rec
    for ym, st in stores.items():
        st["updated"] = datetime.now(JST).isoformat(timespec="seconds")
        st["note"] = ("その日に TDnet で開示した会社の、基準の日（15:30より前の開示は前の営業日・以降はその日）の終値から"
                      "次の営業日（n1）・5営業日後（n5）の終値までの騰落（%）。理由は推定しない")
        (OUT / f"{ym}.json").write_text(json.dumps(st, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    months = sorted(p.stem for p in OUT.glob("????-??.json"))
    (OUT / "index.json").write_text(json.dumps({"months": months}, ensure_ascii=False), encoding="utf-8")
    print(f"適時開示のあとの値動き: 書き足した値 {filled}件／{len(months)}か月")
    return 0


if __name__ == "__main__":
    sys.exit(main())
