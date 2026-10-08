# -*- coding: utf-8 -*-
"""大量保有の「人物・機関」（積上⑧ v1）→ docs/holders.json ・ docs/holders_events.json ・ data/holders_featured.csv

■ 材料
  積上③（edinet_daily.py）の docs/edinet/YYYY-MM.jsonl（1行1書類）。ここは読むだけで、EDINET には取りに行かない。

■ 出すもの（docs/holders.json）
  holders[E番号]   … 提出者（保有者）ごとの現在の保有・売買履歴（60日間の取得/処分の表）・新規報告の後の騰落
  by_issuer[コード] … その銘柄を（共同保有の合計で）5%以上保有している保有者（銘柄ページの索引）
  latest           … いちばん新しい提出日の報告書の一覧（「昨日提出された報告書」）
  featured_moves   … 注目の保有者の直近30日の報告
  purpose_changes  … 保有目的が「純投資」から「重要提案行為等を行う」に変わった保有者×銘柄

■ 決まり
  - 状態（現在の保有）は「訂正」以外の最新の報告で決める。訂正報告書は過去の書類の差し替えで、
    いつの時点の保有かが混ざるため v1 では状態に使わない（件数・一覧には出す）
  - 保有中 = 最新の報告の共同保有の合計（ratio）が5%以上。5%未満の変更報告書は「報告義務の外に出た」
  - 個人は氏名（報告書記載名）と職業だけ。住所・連絡先は③で保存していない。提出事由（reason）は
    罠10（訂正内容の全文に担当者名・電話）があったので、ここには一切持ってこない
  - 保有目的は原文のまま（要約しない）
  - 注目50者 = 特例報告（証券会社・運用会社の定型提出）と訂正を除いた提出件数の上位50（機械で選ぶ）。
    data/holders_featured.csv の pin 列に "+"（必ず入れる）/ "-"（入れない）を書けば本人が足し引きできる。
    この列は作り直しても残す
  - 騰落 = 新規の大量保有報告書（type=大量保有）の提出日の翌営業日始値 → 20/60営業日後の終値（%）。
    ledger_score.py と同じ測り方。60営業日が出たら final（以後は取りに行かない）

使い方: python -X utf8 holders_build.py [--no-price]
"""
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
DOCS = HERE / "docs"
EDINET = DOCS / "edinet"
OUT = DOCS / "holders.json"
EVENTS = DOCS / "holders_events.json"
FEATURED = HERE / "data" / "holders_featured.csv"
JST = timezone(timedelta(hours=9))
FEATURED_N = 50
RECENT_DAYS = 30
HISTORY_DAYS = 60         # 売買履歴に残す範囲（データの最終日から暦日60日・報告書の「60日間の取得/処分」に揃える）
HISTORY_MAX = 300         # 1者あたりの上限（証券会社は60日で数千行になる）
H = (20, 60)
GIVE_UP_DAYS = 150

# 「重要提案行為等を行う予定はありません」のような否定は「重要提案」に数えない
_NEG = re.compile(r"重要提案行為(等)?[^。]{0,12}?(予定は(ありません|ない|ございません)|行いません|行わない|行う予定はな|"
                  r"考えておりません|意図(は|も)?(ありません|ない)|しない|いたしません)")


def is_proposal(t):
    return bool(t) and "重要提案" in t and not _NEG.search(t)


def is_pure(t):
    return bool(t) and "純投資" in t


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def load_docs():
    rows = []
    for f in sorted(EDINET.glob("????-??.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    # 同じ書類が2回入ることは③が防いでいるが、念のため id で一意に
    uniq = {r["id"]: r for r in rows}
    return sorted(uniq.values(), key=lambda r: (r["d"], r.get("tm") or "", r["id"]))


def code_of(r):
    c = ((r.get("issuer") or {}).get("code") or "").strip()
    return c[:4] if len(c) == 5 and c.endswith("0") else c


def is_individual(h):
    return (h.get("kind") or "").startswith("個人")


# ──────────────────────────────────────── 注目50者
def featured(docs):
    cnt, names = Counter(), {}
    for r in docs:
        if r.get("special") or r["type"] == "訂正":
            continue
        for h in r.get("holders") or []:
            if h.get("e"):
                cnt[h["e"]] += 1
                names[h["e"]] = h.get("name") or names.get(h["e"], "")
    pins = {}
    if FEATURED.exists():
        with open(FEATURED, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if (r.get("pin") or "").strip() in ("+", "-"):
                    pins[r["edinet_code"]] = r["pin"].strip()
    ranked = [e for e, _ in sorted(cnt.items(), key=lambda x: (-x[1], x[0]))]
    auto = [e for e in ranked if pins.get(e) != "-"][:FEATURED_N]
    chosen = auto + [e for e, p in pins.items() if p == "+" and e not in auto]
    FEATURED.parent.mkdir(exist_ok=True)
    with open(FEATURED, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["edinet_code", "name", "count", "auto_rank", "pin"])
        rank = {e: i + 1 for i, e in enumerate(ranked)}
        for e in chosen + [e for e, p in pins.items() if p == "-"]:
            w.writerow([e, names.get(e, ""), cnt.get(e, 0), rank.get(e, ""), pins.get(e, "")])
    return set(chosen), rank


# ──────────────────────────────────────── 騰落（新規の大量保有報告書）
def score_events(docs, use_price):
    ev = jload(EVENTS, {}) or {}
    for r in docs:
        if r["type"] == "大量保有" and code_of(r) and r["id"] not in ev:
            ev[r["id"]] = {"code": code_of(r), "d": r["d"], "r20": None, "r60": None, "final": False}
    todo = [x for x in ev.values() if not x["final"]]
    if use_price and todo:
        sys.path.insert(0, str(HERE))
        from ledger_score import fetch, tick
        start = (date.fromisoformat(min(x["d"] for x in todo)) - timedelta(days=10)).isoformat()
        px = fetch({x["code"] for x in todo}, start)
        today = datetime.now(JST).date()
        for x in todo:
            p = px.get(tick(x["code"]))
            if p:
                opens, closes = p
                after = sorted(d for d in closes if d > x["d"])
                o = opens.get(after[0]) if after else None
                for h in H:
                    if x[f"r{h}"] is None and o and len(after) > h and closes.get(after[h]):
                        x[f"r{h}"] = round((closes[after[h]] / o - 1) * 100, 2)
            if x["r60"] is not None:
                x["final"] = True
            elif (today - date.fromisoformat(x["d"])).days > GIVE_UP_DAYS:
                x["final"] = True
                x["note"] = "価格が取れないまま150日たったので打ち切り"
        print(f"  騰落: 対象 {len(todo)} / 価格が取れた銘柄 {len(px)}")
    EVENTS.write_text(json.dumps(ev, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    return ev


# ──────────────────────────────────────── 本体
def build(docs, feat, rank, ev):
    H_ = {}
    state = {}                      # (E, コード) → 最新の訂正以外の報告での保有者行と書類
    first = {}                      # (E, コード) → 最初に見た報告（訂正以外）
    purpose_hist = defaultdict(list)

    def holder(h):
        e = h["e"]
        x = H_.get(e)
        if x is None:
            x = H_[e] = {"name": h.get("name") or e, "individual": is_individual(h), "kind": h.get("kind"),
                         "occupation": None, "n_reports": 0, "n_special": 0, "history": [], "events": [],
                         "featured": e in feat, "rank": rank.get(e)}
        if x["individual"] and h.get("occupation"):
            x["occupation"] = h["occupation"]
        return x

    for r in docs:
        c = code_of(r)
        for h in r.get("holders") or []:
            if not h.get("e"):
                continue
            x = holder(h)
            x["n_reports"] += 1
            x["n_special"] += bool(r.get("special"))
            if r["type"] == "訂正" or not c:
                continue
            k = (h["e"], c)
            first.setdefault(k, (r, h))
            state[k] = (r, h)
            if h.get("purpose"):
                ph = purpose_hist[k]
                if not ph or ph[-1]["purpose"] != h["purpose"]:
                    ph.append({"d": r["d"], "doc": r["id"], "purpose": h["purpose"]})
            for t in h.get("trades") or []:
                x["history"].append({"code": c, "name": r["issuer"].get("name"), "d": t.get("d"), "side": t.get("side"),
                                     "qty": t.get("qty"), "ratio": t.get("ratio"), "price": t.get("price"),
                                     "mkt": t.get("mkt"), "doc": r["id"]})
            if r["type"] == "大量保有":
                e_ = ev.get(r["id"]) or {}
                x["events"].append({"code": c, "name": r["issuer"].get("name"), "d": r["d"], "doc": r["id"],
                                    "ratio": h.get("ratio"), "r20": e_.get("r20"), "r60": e_.get("r60")})

    by_issuer = defaultdict(list)
    for (e, c), (r, h) in state.items():
        x = H_[e]
        x.setdefault("holdings", [])
        tot = r.get("ratio")
        if tot is None or tot < 5:
            x.setdefault("exited", []).append({"code": c, "name": r["issuer"].get("name"), "d": r["d"],
                                               "ratio_total": tot, "doc": r["id"]})
            continue
        f_r, _ = first[(e, c)]
        row = {"code": c, "name": r["issuer"].get("name"), "ratio_own": h.get("ratio"), "ratio_total": tot,
               "joint": (r.get("n_holders") or 1) > 1, "first_d": f_r["d"], "first_is_new": f_r["type"] == "大量保有",
               "last_d": r["d"], "doc": r["id"], "special": bool(r.get("special")),
               "purpose": h.get("purpose")}
        x["holdings"].append(row)
        by_issuer[c].append({"e": e, "name": x["name"], "individual": x["individual"], "ratio_own": h.get("ratio"),
                             "ratio_total": tot, "last_d": r["d"]})

    last = docs[-1]["d"] if docs else None
    hlo = (date.fromisoformat(last) - timedelta(days=HISTORY_DAYS)).isoformat() if last else ""
    for e, x in H_.items():
        x.setdefault("holdings", [])
        x["holdings"].sort(key=lambda y: -(y["ratio_own"] or 0))
        x["history"] = sorted({(y["d"], y["code"], y["side"], y["qty"], y["price"]): y for y in x["history"]}.values(),
                              key=lambda y: (y["d"] or "", y["code"]), reverse=True)
        x["n_history"] = sum(1 for y in x["history"] if (y["d"] or "") >= hlo)
        x["history"] = [y for y in x["history"] if (y["d"] or "") >= hlo][:HISTORY_MAX]
        x["events"].sort(key=lambda y: y["d"], reverse=True)
        r60 = [y["r60"] for y in x["events"] if y.get("r60") is not None]
        r20 = [y["r20"] for y in x["events"] if y.get("r20") is not None]
        x["stats"] = {"n_new": len(x["events"]), "n60": len(r60),
                      "avg60": round(sum(r60) / len(r60), 2) if r60 else None,
                      "up60": round(sum(1 for v in r60 if v > 0) / len(r60) * 100, 1) if r60 else None,
                      "n20": len(r20), "avg20": round(sum(r20) / len(r20), 2) if r20 else None}
        if x["individual"]:
            x.pop("kind", None)          # 個人は氏名と職業だけ
    for c in by_issuer:
        by_issuer[c].sort(key=lambda y: -(y["ratio_own"] or 0))

    changes = []
    for (e, c), ph in purpose_hist.items():
        for a, b in zip(ph, ph[1:]):
            if is_pure(a["purpose"]) and not is_proposal(a["purpose"]) and is_proposal(b["purpose"]):
                r, _ = state[(e, c)]
                changes.append({"e": e, "name": H_[e]["name"], "code": c, "issuer": r["issuer"].get("name"),
                                "d": b["d"], "doc": b["doc"], "before": a["purpose"], "after": b["purpose"]})
    changes.sort(key=lambda y: y["d"], reverse=True)

    def brief(r):
        return {"id": r["id"], "d": r["d"], "tm": r.get("tm"), "type": r["type"], "special": bool(r.get("special")),
                "code": code_of(r), "issuer": (r.get("issuer") or {}).get("name"),
                "ratio": r.get("ratio"), "ratio_prev": r.get("ratio_prev"),
                "holders": [{"e": h["e"], "name": h.get("name")} for h in r.get("holders") or [] if h.get("e")]}

    lo = (date.fromisoformat(last) - timedelta(days=RECENT_DAYS)).isoformat() if last else None
    moves = [brief(r) for r in reversed(docs) if r["d"] >= (lo or "") and r["type"] != "訂正"
             and any(h.get("e") in feat for h in r.get("holders") or [])]
    return H_, dict(by_issuer), {"date": last, "docs": [brief(r) for r in docs if r["d"] == last]}, moves, changes


# 個人の欄に電話・住所の形が紛れていないか（③の罠10の再発防止）
_PHONE = re.compile(r"0\d{1,4}[-‐－(（]\d{1,4}[-‐－)）]\d{3,4}")
_ADDR = re.compile(r"(東京都|北海道|(京都|大阪)府|.{2,3}県).{1,8}[市区町村].{0,12}\d+[-‐－丁番]")


def check_private(H_):
    bad = []
    for e, x in H_.items():
        if not x["individual"]:
            continue
        s = json.dumps({k: v for k, v in x.items() if k not in ("holdings", "history", "events", "exited")},
                       ensure_ascii=False) + " ".join(y.get("purpose") or "" for y in x["holdings"])
        if _PHONE.search(s) or _ADDR.search(s):
            bad.append(e)
    return bad


def main():
    docs = load_docs()
    if not docs:
        print("::error::docs/edinet が空")
        return 1
    feat, rank = featured(docs)
    ev = score_events(docs, "--no-price" not in sys.argv)
    H_, by_issuer, latest, moves, changes = build(docs, feat, rank, ev)
    bad = check_private(H_)
    if bad:
        print(f"::error::個人の欄に電話・住所の形: {bad[:5]}")
        return 1
    st = jload(EDINET / "_state.json", {}) or {}
    done = sorted(st.get("done") or [])
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "since": done[0] if done else docs[0]["d"], "until": docs[-1]["d"], "days_recorded": len(done),
           "n_docs": len(docs), "n_holders": len(H_), "featured": sorted(feat, key=lambda e: rank.get(e, 9999)),
           "note": "EDINET 大量保有報告書の集計。保有中＝訂正以外の最新の報告で共同保有の合計が5%以上。"
                   "騰落＝新規の大量保有報告書の提出日の翌営業日始値→20/60営業日後の終値。保有目的は原文。",
           "holders": H_, "by_issuer": by_issuer, "latest": latest, "featured_moves": moves,
           "purpose_changes": changes}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    hold = sum(1 for x in H_.values() if x["holdings"])
    print(f"holders.json: 書類{len(docs)} 保有者{len(H_)}（保有中あり{hold}・注目{len(feat)}） "
          f"銘柄{len(by_issuer)} 目的の変化{len(changes)} 記録{out['since']}〜{out['until']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
