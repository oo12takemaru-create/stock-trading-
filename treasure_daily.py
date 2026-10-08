# -*- coding: utf-8 -*-
"""宝探し 3入口＋今日の一枚（積上⑦ v1）→ docs/treasure.json ・ docs/treasure_hist/YYYY-MM-DD.json

■ 何のためか（起動文 積上⑦ 2026-10-08・設計書 p8〜9）
  「探し方がいろいろあって面白い」の本体。毎営業日、決まった条件に**映った**銘柄を並べるだけ。
  新しい予測はしない。条件は下の RULES で固定（変えるときは docs/treasure_changelog.md に日付と理由）。

■ 入口（v1）
  静けさ  … 出来高倍率 vr ≤ 0.5（20日平均の半分以下）かつ 20日騰落 |c20| ≤ 3% かつ 信用買残が5営業日で減少
             （prices.json ／ shinyo_meigara.json の bd＝直近5日の前日比の和）
  逆張り  … （空売り残高の合計が、報告のある銘柄の上位10% または 信用倍率＜1倍）かつ 52週高値から −30% 以下
             （karauri.json の stocks[].total ／ shinyo_meigara.json の b÷s ／ prices.json の hi52）
  今日の一枚 … 上の2入口＋既存の入口（十倍株の新規・出来高急増・大きく動いた日）に映った銘柄から、
             取引日を種にした乱数で1銘柄。同じ日に何度作っても同じ銘柄になる

■ 1日1回だけ確定する（追記のみ）
  treasure_hist/{取引日}.json が既にあれば、その日の中身は作り直さない（--force のときだけ作り直す）。
  夜のうちに信用残や空売りが更新されても、その日に映った銘柄・今日の一枚は変わらない。
  台帳（ledger.py の from_treasure）はこの履歴から source=treasure_quiet / treasure_contra / treasure_card を記録する。

■ 過去実績
  台帳の r20（映った翌営業日の始値→20営業日後の終値）が十分たまるまでは「計測中（記録n日分）」。
  数字は作らない。記録が60日分・採点済みが30件を超えたら自動で数字を出す（TRACK_MIN_*）。

使い方: python -X utf8 treasure_daily.py [--force]
"""
import csv
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
HIST = DOCS / "treasure_hist"
OUT = DOCS / "treasure.json"
LEDGER = DOCS / "ledger"
JST = timezone(timedelta(hours=9))

RULES = {
    "quiet": {"vr_max": 0.5, "c20_abs_max": 3.0, "margin_buy_5d": "減少", "value_min_oku": 0.01},
    "contra": {"karauri_top_pct": 10, "margin_ratio_max": 1.0, "hi52_max": -30.0},
    "volume": {"vr_min": 3.0, "value_min_oku": 1.0},
}
TRACK_MIN_DAYS = 60       # 「過去実績」を数字にする最低の記録日数（約3か月）
TRACK_MIN_SCORED = 30     # 同じく、r20 が出た件数
ENTRANCES = ["quiet", "contra", "tenbagger", "volume", "movers"]
LEDGER_SRC = {"quiet": "treasure_quiet", "contra": "treasure_contra", "card": "treasure_card"}


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def norm(c):
    c = str(c).strip()
    return c[:4] if len(c) == 5 and c.endswith("0") else c


def names():
    out = {}
    p = HERE / "tenbagger_universe.csv"
    if p.exists():
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                out[r["code"]] = (r.get("name") or r["code"], r.get("sector33") or "")
    return out


def ledger_rows(sources):
    rows = []
    for f in sorted(LEDGER.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("source") in sources:
                    rows.append(r)
    return rows


# ──────────────────────────────────────── 入口
def quiet(P, M):
    R = RULES["quiet"]
    out = []
    for c, p in P.items():
        m = M.get(c)
        vr, c20 = p.get("vr"), p.get("c20")
        if vr is None or c20 is None or not m or m.get("bd") is None:
            continue
        # 売買代金100万円未満（v<1・単位は百万円）は取引がほぼ成立していない日なので外す（2026-10-08 v1.1）
        if (p.get("v") or 0) < R["value_min_oku"] * 100:
            continue
        if vr <= R["vr_max"] and abs(c20) <= R["c20_abs_max"] and m["bd"] < 0:
            b0 = (m.get("b") or 0) - m["bd"]             # 5営業日前の買残
            out.append({"c": c, "vr": vr, "c20": c20, "b": m.get("b"), "bd": m["bd"],
                        "bd_pct": round(m["bd"] / b0 * 100, 1) if b0 > 0 else None, "p": p.get("p")})
    return out


def contra(P, M, K):
    R = RULES["contra"]
    tot = {}
    for s in K.get("stocks", []):
        c = norm(s["c"])
        if s.get("total") is not None:
            tot[c] = max(tot.get(c, 0), s["total"])
    ranked = sorted(tot.values(), reverse=True)
    k = max(1, round(len(ranked) * R["karauri_top_pct"] / 100))
    thr = ranked[k - 1] if ranked else None
    out = []
    for c, p in P.items():
        hi = p.get("hi52")
        if hi is None or hi > R["hi52_max"]:
            continue
        m = M.get(c) or {}
        ratio = round(m["b"] / m["s"], 2) if m.get("s") and m.get("b") is not None else None
        why = []
        if thr is not None and c in tot and tot[c] >= thr:
            why.append("karauri")
        if ratio is not None and ratio < R["margin_ratio_max"]:
            why.append("ratio")
        if why:
            out.append({"c": c, "hi52": hi, "karauri": tot.get(c), "ratio": ratio, "why": why, "p": p.get("p")})
    return out, {"karauri_threshold": thr, "karauri_stocks": len(ranked)}


def tenbagger_new(trade_date):
    """台帳に記録済みの「十倍株の新規入選」のうち、取引日から7暦日以内（週次なので直近の回）"""
    lo = (datetime.fromisoformat(trade_date) - timedelta(days=7)).date().isoformat()
    out = {}
    for r in ledger_rows({"tenbagger"}):
        if lo <= r["date"] <= trade_date:
            out[norm(r["code"])] = {"c": norm(r["code"]), "date": r["date"], "rank": (r.get("cond") or {}).get("rank")}
    return list(out.values())


def volume(P):
    R = RULES["volume"]
    return [{"c": c, "vr": p["vr"]} for c, p in P.items()
            if p.get("vr") is not None and p["vr"] >= R["vr_min"] and (p.get("v") or 0) >= R["value_min_oku"] * 100]


def movers(trade_date):
    m = jload(DOCS / "movers.json", {}) or {}
    if m.get("trade_date") != trade_date:
        return []
    return [{"c": it["c"], "chg": it.get("chg")} for side in ("up", "down") for it in m.get(side, [])]


# ──────────────────────────────────────── 型文（「映った理由」）
def reason(kind, x, meta=None):
    if kind == "quiet":
        v = "の5%未満" if x["vr"] < 0.05 else f"の{x['vr'] * 100:.0f}%"   # vr は0.1刻み。0.0は「ほぼ出来ていない」
        s = [f"出来高が20日平均{v}まで低下", f"20日間の値動きは{x['c20']:+.1f}%"]
        s.append(f"信用買残が5営業日で{x['bd']:+,}株" + (f"（{x['bd_pct']:+.1f}%）" if x.get("bd_pct") is not None else ""))
        return s
    if kind == "contra":
        s = []
        if "karauri" in x["why"]:
            s.append(f"空売り残高の合計が発行済株式の{x['karauri']:.2f}%（報告のある{meta['karauri_stocks']:,}銘柄の上位10%）")
        if "ratio" in x["why"]:
            s.append(f"信用倍率{x['ratio']:.2f}倍（売残が買残より多い）")
        s.append(f"52週高値から{x['hi52']:+.1f}%")
        return s
    if kind == "tenbagger":
        return [f"十倍株スキャナーに新しく入選（{x['date']}・{x.get('rank') or '—'}位）"]
    if kind == "volume":
        return [f"出来高が20日平均の{x['vr']:.1f}倍"]
    if kind == "movers":
        return [f"前日比{x['chg']:+.1f}%の値動き"]
    return []


def pick(trade_date, where):
    """取引日を種にした乱数で1つ。二段で選ぶ（2026-10-08 v1.1）:
    ①銘柄のある入口から1つ（入口ごとに同じ確率）②その入口に映った銘柄から1つ。
    銘柄を均等に選ぶと、件数の多い「出来高急増」「大きく動いた日」に偏るため。
    where は {銘柄: [映った入口]}。並べ替えてから選ぶので入力の順序に左右されない"""
    ents = sorted({e for ks in where.values() for e in ks}, key=ENTRANCES.index)
    if not ents:
        return None, 0
    h = int(hashlib.sha256(("kaburadar-treasure|" + trade_date).encode()).hexdigest(), 16)
    ent = ents[h % len(ents)]
    pool = sorted(c for c, ks in where.items() if ent in ks)
    return pool[(h // len(ents)) % len(pool)], len(where)


def build(trade_date):
    P = (jload(DOCS / "prices.json", {}) or {}).get("items", {})
    SM = jload(DOCS / "shinyo_meigara.json", {}) or {}
    M = {x["c"]: x for x in SM.get("items", [])}
    K = jload(DOCS / "karauri.json", {}) or {}
    NM = names()

    q = quiet(P, M)
    ct, meta = contra(P, M, K)
    tb = [x for x in tenbagger_new(trade_date) if x["c"] in P]
    vo = volume(P)
    mv = [x for x in movers(trade_date) if x["c"] in P]
    pools = {"quiet": q, "contra": ct, "tenbagger": tb, "volume": vo, "movers": mv}
    where = {}
    for k in ENTRANCES:
        for x in pools[k]:
            where.setdefault(x["c"], []).append(k)

    def nm(c):
        return NM.get(c, (c, ""))

    for k in ("quiet", "contra"):
        for x in pools[k]:
            x["n"], x["s"] = nm(x["c"])
            x["also"] = [e for e in where[x["c"]] if e != k]
            x["reasons"] = reason(k, x, meta)
    # 重なり順（映った入口の数が多い順）→ 条件値順（静けさ＝信用買残の減り方が大きい順・逆張り＝高値からの下げが深い順）
    q.sort(key=lambda x: (-len(x["also"]), x["bd_pct"] if x.get("bd_pct") is not None else 0, x["vr"], x["c"]))
    ct.sort(key=lambda x: (-len(x["also"]), x["hi52"], x["c"]))
    overlap = sorted(c for c, ks in where.items() if len(ks) >= 2 and ({"quiet", "contra"} & set(ks)))

    code, pool_n = pick(trade_date, where)
    card = None
    if code:
        idx = {k: {x["c"]: x for x in pools[k]} for k in ENTRANCES}
        n, s = nm(code)
        card = {"c": code, "n": n, "s": s, "from": where[code], "pool_n": pool_n,
                "reasons": {k: reason(k, idx[k][code], meta) for k in where[code]},
                "p": (P.get(code) or {}).get("p")}

    return {
        "trade_date": trade_date,
        "inputs": {"prices": trade_date, "shinyo_asof": SM.get("asof"), "karauri_report": K.get("report_date"),
                   "karauri_threshold_pct": meta["karauri_threshold"], "karauri_stocks": meta["karauri_stocks"]},
        "rules": RULES,
        "counts": {"quiet": len(q), "contra": len(ct), "overlap": len(overlap),
                   "tenbagger": len(tb), "volume": len(vo), "movers": len(mv)},
        "overlap": overlap,
        "card": card,
        "quiet": q,
        "contra": ct,
    }


# ──────────────────────────────────────── 過去実績（台帳から）
def track(hist_days):
    rows = ledger_rows(set(LEDGER_SRC.values()))
    out = {}
    for k, src in LEDGER_SRC.items():
        days = sum(1 for d in hist_days.values() if (d.get("card") if k == "card" else d.get(k)))
        rs = [r["r20"] for r in rows if r["source"] == src and r.get("r20") is not None]
        t = {"days": days, "since": min(hist_days) if hist_days else None, "n_scored": len(rs), "status": "measuring"}
        if days >= TRACK_MIN_DAYS and len(rs) >= TRACK_MIN_SCORED:
            t.update(status="ready", n=len(rs), avg20=round(sum(rs) / len(rs), 2),
                     up_rate=round(sum(1 for r in rs if r > 0) / len(rs) * 100, 1))
        out[k] = t
    return out


def main():
    force = "--force" in sys.argv
    prices = jload(DOCS / "prices.json", {}) or {}
    td = prices.get("trade_date")
    if not td or not prices.get("items"):
        print("::error::prices.json が無い・空")
        return 1
    HIST.mkdir(parents=True, exist_ok=True)
    hp = HIST / f"{td}.json"
    if hp.exists() and not force:
        day = jload(hp)
        print(f"{td} は確定済み（作り直さない）")
    else:
        day = build(td)
        hp.write_text(json.dumps(day, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{td} を確定: 静けさ{day['counts']['quiet']} 逆張り{day['counts']['contra']} "
              f"重なり{day['counts']['overlap']} 今日の一枚 {(day['card'] or {}).get('c')}")

    hist = {}
    for f in sorted(HIST.glob("????-??-??.json")):
        x = jload(f)
        if x:
            hist[f.stem] = x
    # 今月の最多（静けさ）。その月にほかに3日以上の記録があり、それらより多い日だけ true
    same = [v["counts"]["quiet"] for d, v in hist.items() if d[:7] == td[:7] and d != td]
    month_max = len(same) >= 3 and day["counts"]["quiet"] > max(same)

    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"), **day,
           "quiet_month_max": month_max, "track": track(hist), "hist_days": len(hist)}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"treasure.json を出力（記録 {len(hist)}日分・今月最多={month_max}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
