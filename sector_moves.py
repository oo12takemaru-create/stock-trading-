# -*- coding: utf-8 -*-
"""業種の騰落率（東証33業種・全銘柄の日足から）→ docs/sector_moves.json（2026-11-23・ヒートマップの「業種の強さ」）

■ 何のため
  kaburadar.jp/heatmap.html の「業種の強さ」（横棒＝今日・5日・20日の騰落率／折れ線＝市場全体と比べた強さの20営業日）と、
  業種の中の一覧（業種の騰落率と銘柄の騰落率の差）の物差し。
  ★業種別の温度（docs/thermo_sector.json・thermo_sector33.json）とは別物★
    温度＝地合いの指数（移動平均からの位置などを 0〜100 に）。こちら＝業種に入っている銘柄の値動きの平均（%）。
    名前も JSON の name / note も分ける。

■ 元データ（取得は増やさない。Yahoo には行かない）
  data/bars/YYYY/YYYY-MM-DD.csv.gz（daily_bars.py・毎日1ファイル）と data/bars_backfill/YYYY-MM.csv.gz（過去1年の詰め直し）。
  同じ日が両方にあれば data/bars/ を使う。業種は tenbagger_universe.csv の sector33、株式数は tenbagger_shares.csv。

■ 1日の騰落率（銘柄ごと）＝ その日の終値 ÷ 前の取引日の終値 − 1（close 列。配当の調整はしない＝その日に見えていた値動き）
  使わない日（作り直さずに捨てる。件数はログに出す）:
    - 値幅が制限値幅（stophigh.py の LIMITS）の2倍を超える＝分割・併合の取り込み漏れか桁の誤り（制限値幅の拡大は最大2倍）
    - px_sanity.py の規則1（0以下・1億円超）
    - 分割の権利落ち日（docs/splits.json の ex / ex_jpx）
    - 前の取引日に足が無い（売買停止・上場の初日）

■ 業種の騰落率は2通り持つ（画面の主役は heatmap.html が md を使う。理由は報告書）
  w   時価総額の重みつき平均（重み＝前の取引日の終値×今の発行済株式数）。TOPIX の業種別指数に近い
  md  単純な中央値（業種の中のまん中の銘柄）。大型株1つに引っぱられない
  5日・20日は  w＝毎日の w をつなげた値（＝前の時価総額の重みで持ち続けた場合）、
              md＝銘柄ごとの5日・20日の騰落率（毎日の騰落率をつなげる。欠けが2割を超える銘柄は使わない）の中央値。
  市場全体（mkt）も同じ2通り（全銘柄）と、1306（TOPIX 連動ETF）の終値の騰落率。

■ 折れ線（画面が daily から作る）: 業種の毎日の騰落率をつないだ値 ÷ 市場全体をつないだ値 − 1（%）を直近20営業日。
  順位（rk）＝20営業日の「市場全体と比べた強さ」の順位（1＝いちばん強い）。rk5 は5営業日前の時点の同じ順位。
  up＝その20営業日の線の傾き（最小二乗）が上向き かつ 直近5営業日で線が上がった。

■ main_s33 主要銘柄（docs/heatmap.json）→ 東証33業種（secs の添字）。主要の地図は独自の業種名なので、
  画面が取引時間中の「今日」の棒を主要銘柄の前日比から作るときに使う（約340件・数KB）。

使い方: python -X utf8 sector_moves.py [--out docs/sector_moves.json]（movers-daily.yml の daily_bars.py の後）
"""
import argparse
import csv
import gzip
import json
import statistics
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
BARS = HERE / "data" / "bars"
BACK = HERE / "data" / "bars_backfill"
DAYS = 60            # 公開する営業日数
WARM = 30            # 順位の5日前・20日の窓のための余分（公開しない）
MIN_N = 3            # 業種の値を出す最低の銘柄数（その日に値がある銘柄）
MAX_PRICE = 1e8

sys.path.insert(0, str(HERE))
from stophigh import limit_width          # noqa: E402  制限値幅の表は1か所に（JPX の表・2026-10-24 確認）
from heatmap_day import SECTOR_SLUG       # noqa: E402  サイトの業種ページの名前


def log(*a):
    print(*a, flush=True)


def read_csv_gz(p):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def load_closes(need_days):
    """{date: {code: close}}（直近 need_days 営業日ぶん）。data/bars/ を優先"""
    by = {}
    daily = sorted(BARS.glob("*/*.csv.gz"))
    for p in daily:
        d = p.name[:10]
        by[d] = {r["code"]: float(r["close"]) for r in read_csv_gz(p) if r.get("close") not in (None, "")}
    have_daily = set(by)
    months = sorted(BACK.glob("*.csv.gz"))
    # 新しい月から必要なぶんだけ読む
    for p in reversed(months):
        if len(by) >= need_days + 5:
            break
        for r in read_csv_gz(p):
            d = r["date"]
            if d in have_daily or r.get("close") in (None, ""):
                continue
            by.setdefault(d, {})[r["code"]] = float(r["close"])
    # 取引日 = 銘柄の半分以上に足がある日（休場日に紛れた数行を日として数えない）
    mx = max((len(v) for v in by.values()), default=0)
    dates = sorted(d for d, v in by.items() if len(v) >= mx * 0.5)
    dates = dates[-need_days:]
    return dates, {d: by[d] for d in dates}


def load_split_ex():
    try:
        sp = json.loads((HERE / "docs" / "splits.json").read_text(encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for c, evs in (sp.get("by_code") or {}).items():
        for e in evs:
            for k in ("ex", "ex_jpx"):
                if e.get(k):
                    out.add((c, e[k]))
    for key in ("upcoming", "recent"):
        for e in sp.get(key) or []:
            for k in ("ex", "ex_jpx"):
                if e.get(k):
                    out.add((e.get("code"), e[k]))
    return out


def universe():
    u = {}
    with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            u[r["code"].strip()] = ((r.get("sector33") or "").strip(),
                                    unicodedata.normalize("NFKC", (r.get("name") or "").strip()))
    return u


def shares():
    s = {}
    try:
        with open(HERE / "tenbagger_shares.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                c = (r.get("ticker") or "").replace(".T", "").strip()
                try:
                    n = float(r.get("shares") or 0)
                except ValueError:
                    n = 0
                if c and n > 0:
                    s[c] = n
    except Exception as e:
        log(f"株式数が読めない（重みは使えない）: {e}")
    return s


def daily_returns(dates, closes, split_ex):
    """{date(2日目以降): {code: (騰落率%, 前日の終値)}}・捨てた件数"""
    out, drop = {}, {"limit": 0, "price": 0, "split": 0}
    for a, b in zip(dates, dates[1:]):
        pa, pb = closes[a], closes[b]
        day = {}
        for c, cb in pb.items():
            ca = pa.get(c)
            if ca is None:
                continue
            if not (0 < ca <= MAX_PRICE and 0 < cb <= MAX_PRICE):
                drop["price"] += 1
                continue
            if (c, b) in split_ex:
                drop["split"] += 1
                continue
            if abs(cb - ca) > 2 * limit_width(ca) + 1e-9:
                drop["limit"] += 1
                continue
            day[c] = ((cb / ca - 1) * 100, ca)
        out[b] = day
    return out, drop


def wavg(rows, sh):
    num = den = 0.0
    for c, (r, prev) in rows:
        w = sh.get(c, 0) * prev
        if w > 0:
            num += w * r
            den += w
    return num / den if den > 0 else None


def chain(vals):
    """毎日の騰落率(%)の並び → つないだ騰落率(%)。None は 0 とみなさず、その日は飛ばす"""
    x = 1.0
    for v in vals:
        if v is not None:
            x *= 1 + v / 100
    return (x - 1) * 100


def slope(ys):
    n = len(ys)
    if n < 3:
        return 0.0
    mx = (n - 1) / 2
    my = sum(ys) / n
    den = sum((i - mx) ** 2 for i in range(n))
    return sum((i - mx) * (y - my) for i, y in enumerate(ys)) / den if den else 0.0


def rs_line(sec, mkt, end, win=20):
    """end（daily の添字・含む）までの win 営業日の「市場全体と比べた強さ」の線（win+1 点・最初は0）"""
    pts, a, b = [0.0], 1.0, 1.0
    for k in range(end - win + 1, end + 1):
        s, m = sec[k], mkt[k]
        if s is not None:
            a *= 1 + s / 100
        if m is not None:
            b *= 1 + m / 100
        pts.append((a / b - 1) * 100)
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "docs" / "sector_moves.json"))
    args = ap.parse_args()

    need = DAYS + WARM + 1
    dates, closes = load_closes(need)
    if len(dates) < 30:
        log(f"::error::日足が足りない（{len(dates)}日）")
        return 1
    u = universe()
    sh = shares()
    rets, drop = daily_returns(dates, closes, load_split_ex())
    rdays = dates[1:]
    log(f"日足 {dates[0]}〜{dates[-1]}（{len(dates)}日）・捨てた日 {drop}")

    secs = sorted({s for s, _ in u.values() if s in SECTOR_SLUG}, key=list(SECTOR_SLUG).index)
    # 業種ごと・市場全体の毎日の値
    S = {s: {"w": [], "md": [], "n": []} for s in secs}
    M = {"w": [], "md": [], "n": []}
    for d in rdays:
        day = rets[d]
        by = {}
        for c, v in day.items():
            s = (u.get(c) or ("", ""))[0]
            if s in S:
                by.setdefault(s, []).append((c, v))
        allr = [x for s_ in by.values() for x in s_]
        M["w"].append(wavg(allr, sh))
        M["md"].append(statistics.median([v[0] for _, v in allr]) if allr else None)
        M["n"].append(len(allr))
        for s in secs:
            rows = by.get(s) or []
            ok = len(rows) >= MIN_N
            S[s]["w"].append(wavg(rows, sh) if ok else None)
            S[s]["md"].append(statistics.median([v[0] for _, v in rows]) if ok else None)
            S[s]["n"].append(len(rows))
    etf = []
    for a, b in zip(dates, dates[1:]):
        x, y = closes[a].get("1306"), closes[b].get("1306")
        etf.append((y / x - 1) * 100 if x and y else None)

    # 銘柄ごとの k 日の騰落率（md の5日・20日用）。欠けが2割を超える銘柄は使わない
    def stock_k(k):
        out = {}
        idx = range(len(rdays) - k, len(rdays))
        cnt = {}
        for i in idx:
            for c, (r, _) in rets[rdays[i]].items():
                cnt.setdefault(c, []).append(r)
        for c, rs in cnt.items():
            if len(rs) >= k * 0.8:
                out[c] = chain(rs)
        return out
    sk = {1: {c: v[0] for c, v in rets[rdays[-1]].items()}, 5: stock_k(5), 20: stock_k(20)}

    L = len(rdays)
    pub = slice(L - DAYS, L)
    rnd = lambda v, n=2: None if v is None else round(v, n)  # noqa: E731

    def ranks(method, end):
        vals = []
        for s in secs:
            line = rs_line(S[s][method], M[method], end)
            vals.append((line[-1], s))
        vals.sort(key=lambda x: -x[0])
        return {s: i + 1 for i, (_, s) in enumerate(vals)}

    rk = {m: ranks(m, L - 1) for m in ("w", "md")}
    rk5 = {m: ranks(m, L - 6) for m in ("w", "md")}
    items = []
    for s in secs:
        members = [c for c, (ss, _) in u.items() if ss == s]
        o = {"s": s, "slug": SECTOR_SLUG.get(s), "n": len(members),
             "w": [rnd(v) for v in S[s]["w"][pub]], "md": [rnd(v) for v in S[s]["md"][pub]]}
        for m in ("w", "md"):
            line = rs_line(S[s][m], M[m], L - 1)
            r5 = None
            if m == "w":
                k1, k5, k20 = S[s]["w"][-1], chain(S[s]["w"][-5:]), chain(S[s]["w"][-20:])
            else:
                vs = {k: [sk[k][c] for c in members if c in sk[k]] for k in (1, 5, 20)}
                k1, k5, k20 = (statistics.median(vs[k]) if len(vs[k]) >= MIN_N else None for k in (1, 5, 20))
            r5 = line[-1] - line[-6]
            o[m + "_r"] = [rnd(k1), rnd(k5), rnd(k20)]             # 今日・5日・20日（%）
            o[m + "_rs"] = rnd(line[-1])                           # 20日の市場全体と比べた強さ（pt）
            o[m + "_rk"] = rk[m][s]
            o[m + "_rk5"] = rk5[m][s]
            o[m + "_up"] = 1 if (slope(line) > 0 and r5 > 0) else 0
        items.append(o)

    mk = {"w": [rnd(v) for v in M["w"][pub]], "md": [rnd(v) for v in M["md"][pub]], "etf": [rnd(v) for v in etf[pub]],
          "n": M["n"][-1]}
    for m in ("w", "md"):
        if m == "w":
            mk["w_r"] = [rnd(M["w"][-1]), rnd(chain(M["w"][-5:])), rnd(chain(M["w"][-20:]))]
        else:
            mk["md_r"] = [rnd(statistics.median(list(sk[k].values()))) if sk[k] else None for k in (1, 5, 20)]
    mk["etf_r"] = [rnd(etf[-1]), rnd(chain(etf[-5:])), rnd(chain(etf[-20:]))]

    # 主要銘柄（docs/heatmap.json・場中30分ごと）の東証33業種。主要の地図は独自の業種名（「半導体製造装置」など）なので、
    # 画面が取引時間中の「今日」の棒を主要銘柄から作るときの対応表に使う（{コード: secs の添字}）
    main_s33 = {}
    try:
        from daily_scanner_v2_8_0 import STOCKS
        for t in STOCKS:
            c = t.replace(".T", "")
            s = (u.get(c) or ("", ""))[0]
            if s in secs:
                main_s33[c] = secs.index(s)
    except Exception as e:
        log(f"主要銘柄の対応表を作れない（場中の今日の棒は出ない）: {e}")

    out = {
        "name": "業種の騰落率（東証33業種・全銘柄の日足から）",
        "note": "業種に入っている銘柄の値動き（%）。業種別の温度（thermo_sector.json・地合いの指数）とは別の数字です。"
                "w＝時価総額の重みつき平均、md＝中央値。値幅制限の2倍を超える動き・分割の権利落ち日は使いません。",
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "trade_date": rdays[-1],
        "dates": rdays[pub.start:],
        "dropped": drop,
        "mkt": mk,
        "secs": secs,
        "main_s33": main_s33,
        "items": items,
    }
    p = Path(args.out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    top = sorted(items, key=lambda x: x["md_rk"])
    log(f"{p.name}: {len(items)}業種・{len(out['dates'])}日（{out['dates'][0]}〜{out['trade_date']}）・{p.stat().st_size:,}バイト")
    log("強さ（中央値・20日）上位: " + " / ".join(f"{x['s']} {x['md_rs']:+.1f}pt（{x['md_rk5']}→{x['md_rk']}位）" for x in top[:5]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
