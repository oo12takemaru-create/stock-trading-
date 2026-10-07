# -*- coding: utf-8 -*-
"""空売り機関トラッカー（積上④）→ docs/shorts.json ＋ docs/sellers.json

機関ごとのページ（kaburadar/shorts/{id}.html）の材料を1つにまとめる。
ページの生成はサイト側の gen_shorts.py が行う（ここはデータだけ）。

■ 材料
  data/sellers.csv            名寄せ表（報告書上の名前 → ID・表示名・グループ）
  karauri_events/events.json  報告の履歴（2026-06-01公表分〜）と20営業日後リターン。週1回 karauri_score.py が更新
  docs/karauri.json           今日の残高（銘柄ごと・機関ごと）。毎日 karauri_fetch.py が更新
  prices.json                 ETF・REITの見分けに使う（ここに無い銘柄は個別株の銘柄ページが無い）

■ events.json は週1回しか更新されない → 抜けた日はメモリ上で足す
  karauri_score.collect_events() で、JPXの日次ファイルのうち未取り込みの公表日だけを読む。
  **events.json には書き戻さない**（週1回の成績表ジョブとファイルを取り合わないため）。
  今日の残高（karauri.json）の基準日より後の報告は、残高に入っていないので使わない。

■ 推移グラフは「2026-06-01からの報告残高の増減（累積）」
  過去の残高の水準そのものは記録されていない。報告の履歴だけを6/1から足し上げる
  （karauri_hist.py と同じ考え方）。水準ではなく増減なので、0より下にもなる。
    新規 = +ratio / 増加・減少 = ratio − prev / 解消 = −prev（報告対象から外れるので全額）
  今日の残高（水準）は karauri.json の数字をそのまま出す（karauri.html と同じ）。

■ 「新規」の数え方（karauri.html の「今日の動き」と数が違う理由）
  ここは karauri_score.py と同じく、JPXの報告書にある「前回の残高割合」が0.5%未満（または空）で、
  今回0.5%以上の報告を新規と数える。いったん解消したあと再び0.5%以上に戻った報告も新規に入る
  （2026-10-07公表分の新規120件のうち80件がこれ）。karauri_fetch.py の「new」は
  自分の蓄積に無かった組み合わせだけを数え、集計の区切りも違うので、件数は一致しない。

■ 成績表1行（機関ごと）
  「この機関が新規に0.5%超を報告した銘柄」だけを集める（増加・減少・解消は混ぜない）。
  起点は公表日の翌営業日の始値、終点は20営業日後の終値（karauri_score.py と同じ d20）。
  母集団比 = その報告の20日後リターン − 同じ公表日に報告があった全銘柄の20日後リターンの平均、の平均。
  件数10未満は数字を出さない（karauri_score.py の MIN_N と同じ）。

■ 文言
  「報告」「増加」「減少」「解消」と統計値だけ。機関の良し悪しの評価語は出力に入れない。

使い方:
  python -X utf8 shorts_build.py            # JPXへの抜け取り込み・株価取得あり
  python -X utf8 shorts_build.py --offline  # ネットに出ない（events.json と karauri.json だけで組む・検証用）
"""
import csv
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

JST = timezone(timedelta(hours=9))
SELLERS_CSV = HERE / "data" / "sellers.csv"
EVENTS = HERE / "karauri_events" / "events.json"
KARAURI = HERE / "docs" / "karauri.json"
PRICES = HERE / "docs" / "prices.json"
OUT = HERE / "docs" / "shorts.json"
OUT_IDS = HERE / "docs" / "sellers.json"

MIN_N = 10            # karauri_score.py と同じ。これ未満は統計を出さない
MOVES_PER_SELLER = 30
RANK_TOP = 50
CLOSED_DAYS = 5       # 「全機関が解消した銘柄」を何公表日ぶん遡って出すか

KIND = {"新規": "new", "増加": "up", "減少": "down", "解消": "out"}


# ---------------------------------------------------------------- 名寄せ
def load_sellers():
    rows = list(csv.DictReader(SELLERS_CSV.open(encoding="utf-8")))
    by_name = {r["name"]: r for r in rows}
    return rows, by_name


# ---------------------------------------------------------------- 材料
def load_events(offline):
    store = json.loads(EVENTS.read_text(encoding="utf-8")) if EVENTS.exists() else []
    added = 0
    if not offline:
        import karauri_score as S
        mem = list(store)                    # 書き戻さない
        try:
            added = S.collect_events(mem)
            store = mem
        except Exception as e:
            print(f"  JPXの抜け取り込みに失敗（events.json の範囲で組む）: {e}", file=sys.stderr)
    return store, added


def fetch_prices(codes, start):
    """{code: (opens{d:v}, closes{d:v})}。取れなかった銘柄は入らない（推測しない）"""
    import pandas as pd
    import yfinance as yf

    def tick(c):
        return c.strip() + ".T"

    out = {}
    codes = sorted(codes)
    B = 200
    for i in range(0, len(codes), B):
        chunk = codes[i:i + B]
        try:
            df = yf.download(" ".join(tick(c) for c in chunk), start=start, progress=False,
                             auto_adjust=False, threads=False, group_by="ticker")
        except Exception as e:
            print(f"  株価取得失敗: {e}", file=sys.stderr)
            time.sleep(2)
            continue
        for c in chunk:
            try:
                sub = df[tick(c)] if isinstance(df.columns, pd.MultiIndex) else df
                o, cl = sub["Open"].dropna(), sub["Close"].dropna()
                if len(cl) < 2:
                    continue
                out[c] = ({k.strftime("%Y-%m-%d"): float(v) for k, v in o.items()},
                          {k.strftime("%Y-%m-%d"): float(v) for k, v in cl.items()})
            except Exception:
                continue
        time.sleep(2)
    return out


# ---------------------------------------------------------------- 集計
def universe_mean(events):
    """公表日ごとの d20 の平均（karauri_score.score() の rel と同じ母集団＝全種別）"""
    base = defaultdict(list)
    for e in events:
        if "d20" in e:
            base[e["pub"]].append(e["d20"])
    return {k: sum(v) / len(v) for k, v in base.items()}


def new_score(evs, rel):
    """新規の報告だけの20営業日後。件数不足なら low_n"""
    s = [e for e in evs if e["event"] == "新規" and "d20" in e]
    n = len(s)
    if n < MIN_N:
        return {"n": n, "low_n": True}
    d20 = [e["d20"] for e in s]
    return {
        "n": n, "low_n": False,
        "avg": round(sum(d20) / n, 2),
        "down_rate": round(100 * sum(1 for v in d20 if v < 0) / n, 1),
        "vs_universe": round(sum(e["d20"] - rel[e["pub"]] for e in s) / n, 2),
    }


def delta(e):
    """残高合計（pt）と銘柄数の変化。ratio は比率（0.0252）なので×100でptにする"""
    r, p = e.get("ratio") or 0.0, e.get("prev") or 0.0
    if e["event"] == "新規":
        return r * 100, 1
    if e["event"] == "解消":
        return -p * 100, -1
    return (r - p) * 100, 0


def main():
    offline = "--offline" in sys.argv
    rows, by_name = load_sellers()
    store, added = load_events(offline)
    k = json.loads(KARAURI.read_text(encoding="utf-8"))
    snap_calc = k.get("report_date")
    try:
        stock_codes = set(json.loads(PRICES.read_text(encoding="utf-8")).get("items", {}))
    except Exception:
        stock_codes = set()

    # 今日の残高の基準日より後の報告は残高に入っていないので使わない
    evs = [e for e in store if not snap_calc or e["calc"] <= snap_calc]
    skipped_future = len(store) - len(evs)
    rel = universe_mean(evs)
    pubs = sorted({e["pub"] for e in evs})

    unknown = sorted({e["seller"] for e in evs if e["seller"] not in by_name}
                     | {x["s"] for s in k["stocks"] for x in s["sellers"] if x["s"] not in by_name})
    if unknown:
        # 名寄せ表に無い機関はページを作れない。黙って落とさず、ログとJSONに残す
        print(f"  ★名寄せ表に無い機関名 {len(unknown)}件: {unknown}", file=sys.stderr)

    # 今日の残高（機関ごと）
    hold = defaultdict(list)                       # 報告書名 -> [{c,n,r,last}]
    names = {}
    for s in k["stocks"]:
        names[s["c"]] = s["n"]
        for x in s["sellers"]:
            hold[x["s"]].append({"c": s["c"], "n": s["n"], "r": x["r"], "last": x.get("d")})

    by_seller = defaultdict(list)
    for e in evs:
        by_seller[e["seller"]].append(e)
        names.setdefault(e["code"], e["name"])

    # 初回報告日（いまの報告が始まった新規の公表日）。解消のあと新規があれば、そちらから数える
    first, lastev = {}, {}
    for e in sorted(evs, key=lambda x: x["pub"]):
        key = (e["seller"], e["code"])
        lastev[key] = (e["pub"], KIND[e["event"]])      # 直近の増減（この組み合わせの最後の報告）
        if e["event"] == "新規":
            first[key] = e["pub"]
        elif e["event"] == "解消":
            first.pop(key, None)

    # 初回報告日からの騰落のための株価（翌営業日の始値 → 直近の終値）
    need = {c for s, hs in hold.items() for h in hs
            for c in [h["c"]] if (s, c) in first and c in stock_codes}
    px = {}
    if need and not offline:
        start = (datetime.fromisoformat(min(first[(s, h["c"])] for s, hs in hold.items()
                                            for h in hs if (s, h["c"]) in first))
                 - timedelta(days=7)).date().isoformat()
        print(f"  株価: {len(need)}銘柄（{start}〜）")
        px = fetch_prices(need, start)
        print(f"  株価が取れた銘柄 {len(px)}")

    def since_first(code, pub):
        p = px.get(code)
        if not p:
            return None
        opens, closes = p
        after = sorted(d for d in opens if d > pub)
        if not after or not closes:
            return None
        o = opens.get(after[0])
        last_c = closes[max(closes)]
        return round((last_c / o - 1) * 100, 1) if o else None

    sellers_out = []
    for r in rows:
        if not r["id"]:
            continue                                # 除外（個人）
        nm = r["name"]
        sevs = by_seller.get(nm, [])
        hs = sorted(hold.get(nm, []), key=lambda h: -h["r"])
        level, cnt = sum(h["r"] for h in hs), len(hs)

        # 推移: 2026-06-01 からの報告残高の増減（累積）。報告の履歴だけで閉じる。
        # 今日の残高（karauri.json）を終点に引き戻す方法は使わない。今日の残高は
        # JPXに残っている直近の報告から組んだもので、それより前から変化の無い長期保有を
        # 含まないため、履歴と食い違う（2026-10-08 に銘柄数が負になる機関が出た）
        per_pub = defaultdict(lambda: [0.0, 0])
        for e in sevs:
            dv, dc = delta(e)
            per_pub[e["pub"]][0] += dv
            per_pub[e["pub"]][1] += dc
        series, L, C = [], 0.0, 0
        for p in pubs:
            L += per_pub[p][0]
            C += per_pub[p][1]
            series.append([p, round(L, 2), C])
        # 直近の公表日に報告が無ければ増減は0（報告が無い＝変化なし）
        last = per_pub[pubs[-1]] if pubs else None

        holdings = []
        for h in hs:
            f = first.get((nm, h["c"]))
            le = lastev.get((nm, h["c"]))
            holdings.append({
                "c": h["c"], "n": h["n"], "r": h["r"], "last": h["last"],
                "last_kind": le[1] if le else None,  # None = 6/1以降に変化の報告なし
                "last_pub": le[0] if le else None,
                "first": f,                         # None = 2026-06-01公表分より前から
                "chg": since_first(h["c"], f) if f else None,
                "stock": h["c"] in stock_codes,     # False = ETF・REITなど（銘柄ページ無し）
            })
        moves = [{"pub": e["pub"], "c": e["code"], "n": e["name"], "kind": KIND[e["event"]],
                  "r": round((e.get("ratio") or 0) * 100, 2), "prev": round((e.get("prev") or 0) * 100, 2)}
                 for e in sorted(sevs, key=lambda x: (x["pub"], x["code"]), reverse=True)[:MOVES_PER_SELLER]]

        sellers_out.append({
            "id": r["id"], "name": nm, "display": r["display"],
            "group_id": r["group_id"] or None, "group_display": r["group_display"] or None,
            # 水準は今日の残高（karauri.html と同じ数字）。前日比は直近の公表日の報告による増減
            "today": {"stocks": cnt, "ratio_sum": round(level, 2),
                      "d_stocks": last[1] if last else None,
                      "d_ratio": round(last[0], 2) if last else None,
                      "pub": pubs[-1] if pubs else None},
            "score": new_score(sevs, rel),
            "reports": len(sevs),
            "series": series,
            "holdings": holdings,
            "moves": moves,
        })

    # 一覧ページ用
    last_pub = pubs[-1] if pubs else None
    yest = {"pub": last_pub, "new": [], "up": [], "down": [], "out": []}
    for e in evs:
        if e["pub"] == last_pub and e["seller"] in by_name and by_name[e["seller"]]["id"]:
            yest[KIND[e["event"]]].append({
                "c": e["code"], "n": e["name"], "id": by_name[e["seller"]]["id"],
                "r": round((e.get("ratio") or 0) * 100, 2), "prev": round((e.get("prev") or 0) * 100, 2)})
    for kk in ("new", "up", "down", "out"):
        yest[kk].sort(key=lambda x: -abs(x["r"] - x["prev"]))

    rank = sorted((s for s in k["stocks"] if s["c"] in stock_codes), key=lambda s: -s["total"])[:RANK_TOP]
    rank = [{"c": s["c"], "n": s["n"], "total": s["total"], "cnt": s["cnt"]} for s in rank]

    # 全機関が解消した銘柄: 直近の公表日に解消があり、今日の残高に1機関も残っていない
    now_codes = {s["c"] for s in k["stocks"]}
    recent = set(pubs[-CLOSED_DAYS:])
    closed = {}
    for e in evs:
        if e["event"] == "解消" and e["pub"] in recent and e["code"] not in now_codes:
            closed[e["code"]] = max(closed.get(e["code"], ""), e["pub"])
    closed = [{"c": c, "n": names.get(c, c), "pub": p, "stock": c in stock_codes}
              for c, p in sorted(closed.items(), key=lambda x: (x[1], x[0]), reverse=True)]

    allnew = [e for e in evs if e["event"] == "新規" and "d20" in e]
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "JPX「空売り残高に関する情報」（当サイトが2026-06-01公表分から毎営業日蓄積）＋日足（Yahoo Finance）",
        "asof": snap_calc,
        "period": {"from": pubs[0] if pubs else None, "to": last_pub, "publish_days": len(pubs)},
        "method": ("成績は「新規に0.5%超を報告した銘柄」だけを数えた。起点は公表日の翌営業日の始値、"
                   "終点は20営業日後の終値。母集団比は、同じ公表日に報告があった全銘柄の20営業日後の平均との差。"
                   f"件数{MIN_N}未満の機関は統計を出さない。"),
        "caveats": [
            "蓄積は2026-06-01公表分からで、期間が短い",
            "上場廃止・統合した銘柄は株価が取れず集計から外れる（生存バイアス）",
            "推移は2026-06-01からの報告残高の増減の累積で、残高の水準そのものではない",
            "新規は「前回の報告が0.5%未満（または初めて）で、今回0.5%以上」の報告。いったん解消して戻った報告も含む",
            "過去にこうだったという記録であって、この機関に倣えば結果が出るという意味ではない",
        ],
        "universe_new": ({"n": len(allnew),
                          "avg": round(sum(e["d20"] for e in allnew) / len(allnew), 2),
                          "down_rate": round(100 * sum(1 for e in allnew if e["d20"] < 0) / len(allnew), 1)}
                         if allnew else None),
        "unknown_sellers": unknown,
        "sellers": sellers_out,
        "yesterday": yest,
        "rank_total": rank,
        "all_closed": closed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    groups = defaultdict(list)
    for r in rows:
        if r["id"] and r["group_id"]:
            groups[r["group_id"]].append(r["id"])
    ids = {
        "updated": out["updated"],
        "note": "報告書上の機関名 → 株レーダーの機関ページID（kaburadar/shorts/{id}.html）。名寄せ表 data/sellers.csv から作る",
        "ids": {r["name"]: r["id"] for r in rows if r["id"]},
        "sellers": {r["id"]: {"display": r["display"], "name": r["name"],
                              "group_id": r["group_id"] or None} for r in rows if r["id"]},
        "groups": {g: {"display": next(r["group_display"] for r in rows if r["group_id"] == g),
                       "members": m} for g, m in groups.items()},
    }
    OUT_IDS.write_text(json.dumps(ids, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"OK shorts.json: 機関{len(sellers_out)} / 報告{len(evs)}件（JPXから抜けを足した {added}件・"
          f"基準日より後で除外 {skipped_future}件） / 公表日 {pubs[0] if pubs else '-'}〜{last_pub} "
          f"({OUT.stat().st_size // 1024}KB)")
    shown = sum(1 for s in sellers_out if not s["score"]["low_n"])
    print(f"  成績を出せる機関 {shown} / 件数不足 {len(sellers_out) - shown}")
    print(f"  順位 {len(rank)} / 全機関が解消 {len(closed)} / 昨日の動き "
          + " ".join(f"{kk}={len(yest[kk])}" for kk in ("new", "up", "down", "out")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
