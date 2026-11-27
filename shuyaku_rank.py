# -*- coding: utf-8 -*-
"""売買代金の週の順位とシェア（東証33業種・銘柄）→ data/shuyaku/（非公開）と docs/rank_shift.json（公開）（2026-11-27）

■ 何のため
  「主役交代マップ」（仮称・kaburadar.jp でまだページは無い）の元。売買代金の順位が週ごとにどう入れ替わったかを線で見せる。
  サイト側のページは、手元の検証の結果を見てから作る（この PR はデータを貯め始めるだけ）。

■ 元データ（取得は増やさない。Yahoo には行かない）
  data/bars/YYYY/YYYY-MM-DD.csv.gz（daily_bars.py・毎日1ファイル）と data/bars_backfill/YYYY-MM.csv.gz（過去1年の詰め直し）。
  同じ日が両方にあれば data/bars/ を使う（sector_moves.py と同じ）。業種は tenbagger_universe.csv の sector33。
  売買代金 ＝ 終値 × 出来高（近似。J-Quants の売買代金と比べて上位20の顔ぶれは週平均 19.9/20 一致・手元で確認）。

■ 週と順位
  週 ＝ 月曜〜金曜。週の営業日の売買代金の合計で順位（1位が最大）。銘柄は内国株＝data/shuyaku/codes.json（tenbagger_universe.csv＝JPX の内国株の一覧から）に業種がある銘柄。
  ETF・REIT・指数・1306 は一覧に無いので入らない（英字入りのコード 285A なども内国株として入る。コードの範囲では分けない＝13xx〜16xx にも普通株があるため）。
  上場したばかりで一覧（毎週土曜に作り直し）にまだ無い銘柄は、一覧に入るまでの数日は入らない。
  業種は33業種の合計の順位。シェア ＝ 銘柄（業種）の週の売買代金 ÷ 内国株（33業種に入るもの）の週の合計。
  使わない行: px_sanity.py の規則1（終値が0以下・1億円超）だけ（売買代金を壊すのはこれだけだった。分割の日は終値と出来高が逆に動くので崩れない）。
  当週は暫定（provisional=1）として毎日上書きし、週が終わったら確定（provisional=0）。

■ 保存（非公開・docs/ の外）
  data/shuyaku/YYYY.csv（週の月曜の年ごと）: unit,code,week,week_end,n_days,rank,share_pct,tv_oku,provisional
    unit = sector（33行/週） / stock（上位 KEEP_STOCK 行/週）。tv_oku は売買代金（億円・小数1桁）。
  data/shuyaku/codes.json: {コード: [名前, 業種]}。一度でも見た銘柄を残す（上場廃止しても過去の業種の合計に使えるように）。
  毎日の実行で作り直すのは、日足が揃っている直近 REBUILD_WEEKS 週だけ。それより前の行は触らない
  （＝手元で作った過去分を初期値として入れておける: --seed で同じ列の CSV を読み、無い週だけ足す）。

■ 公開（docs/rank_shift.json・15〜25KB）
  過去 PUB_WEEKS 週の、業種33本と「期間中に一度でも PUB_TOP 位以内に入った銘柄」の順位とシェアと名前だけ。
  ★売買代金の値そのものは入れない（JPX 由来の値をダウンロードできる形にしない）★
  順位が KEEP_STOCK 位より下の週は null（圏外）。

使い方: python -X utf8 shuyaku_rank.py [--seed 過去分.csv] [--today YYYY-MM-DD]（movers-daily.yml の sector_moves.py の後）
"""
import argparse
import csv
import gzip
import json
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
MAX_PRICE = 1e8          # px_sanity.py の規則1と同じ
KEEP_STOCK = 100         # 保存する銘柄の順位（週ごと）
REBUILD_WEEKS = 10       # 毎日作り直す直近の週の数
PUB_WEEKS = 26           # 公開する週の数
PUB_TOP = 30             # 公開する銘柄＝期間中に一度でもこの順位以内
FIELDS = ["unit", "code", "week", "week_end", "n_days", "rank", "share_pct", "tv_oku", "provisional"]


def log(*a):
    print(*a, flush=True)


def is_stock(c, codes):
    """内国株か（codes.json に業種がある）。ETF・REIT・指数・1306 は一覧に無い"""
    return bool(c) and not c.startswith("^") and bool((codes.get(c) or ["", ""])[1])


def monday(d):
    dt = date.fromisoformat(d)
    return (dt - timedelta(days=dt.weekday())).isoformat()


def read_csv_gz(p):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def load_tv(root, since, codes):
    """{date: {code: 売買代金（円）}}（since 以降）。data/bars/ を優先。捨てた行の数も返す"""
    bars, back = root / "data" / "bars", root / "data" / "bars_backfill"
    by, drop = {}, 0

    def take(rows, store):
        nonlocal drop
        for r in rows:
            c = r.get("code") or ""
            if not is_stock(c, codes):
                continue
            try:
                cl, v = float(r["close"]), float(r["volume"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (0 < cl <= MAX_PRICE):
                drop += 1
                continue
            if v > 0:
                store[c] = cl * v
    for p in sorted(bars.glob("*/*.csv.gz")):
        d = p.name[:10]
        if d < since:
            continue
        take(read_csv_gz(p), by.setdefault(d, {}))
    have = set(by)
    for p in sorted(back.glob("*.csv.gz")):
        if p.name[:7] < since[:7]:
            continue
        tmp = defaultdict(list)
        for r in read_csv_gz(p):
            d = r.get("date") or ""
            if d >= since and d not in have:
                tmp[d].append(r)
        for d, rows in tmp.items():
            take(rows, by.setdefault(d, {}))
    # 取引日 = 銘柄の半分以上に足がある日（休場日に紛れた数行を日として数えない）
    mx = max((len(v) for v in by.values()), default=0)
    return {d: v for d, v in by.items() if len(v) >= mx * 0.5}, drop


def source_start(root):
    """日足の元のいちばん古い日（bars_backfill の最初のファイルの最初の行・無ければ data/bars の最初のファイル）"""
    back = sorted((root / "data" / "bars_backfill").glob("*.csv.gz"))
    if back:
        return min(r["date"] for r in read_csv_gz(back[0]) if r.get("date"))
    bars = sorted((root / "data" / "bars").glob("*/*.csv.gz"))
    return bars[0].name[:10] if bars else None


def update_codes(root):
    """data/shuyaku/codes.json を今の一覧で足して返す（消さない）"""
    p = root / "data" / "shuyaku" / "codes.json"
    try:
        codes = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        codes = {}
    with open(root / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            c = r["code"].strip()
            nm = unicodedata.normalize("NFKC", (r.get("name") or "").strip())
            sec = (r.get("sector33") or "").strip()
            if c and (nm or sec):
                old = codes.get(c, ["", ""])
                codes[c] = [nm or old[0], sec or old[1]]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(codes, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")
    return codes


def weekly_rows(daily, codes, today):
    """{date: {code: tv}} → 週ごとの行（FIELDS の dict）"""
    weeks = defaultdict(list)
    for d in daily:
        weeks[monday(d)].append(d)
    last = max(daily) if daily else ""
    rows = []
    for wk in sorted(weeks):
        ds = sorted(weeks[wk])
        fri = (date.fromisoformat(wk) + timedelta(days=4)).isoformat()
        prov = 1 if (ds[-1] == last and ds[-1] < fri and today <= fri) else 0
        tv = defaultdict(float)
        for d in ds:
            for c, v in daily[d].items():
                tv[c] += v
        sec = defaultdict(float)
        for c, v in tv.items():
            s = (codes.get(c) or ["", ""])[1]
            if s:
                sec[s] += v
        tot = sum(sec.values())
        if tot <= 0:
            continue
        base = dict(week=wk, week_end=ds[-1], n_days=len(ds), provisional=prov)
        for i, (s, v) in enumerate(sorted(sec.items(), key=lambda x: (-x[1], x[0])), 1):
            rows.append(dict(base, unit="sector", code=s, rank=i, share_pct=round(v / tot * 100, 3), tv_oku=round(v / 1e8, 1)))
        for i, (c, v) in enumerate(sorted(tv.items(), key=lambda x: (-x[1], x[0]))[:KEEP_STOCK], 1):
            rows.append(dict(base, unit="stock", code=c, rank=i, share_pct=round(v / tot * 100, 3), tv_oku=round(v / 1e8, 1)))
    return rows


def read_store(sdir):
    rows = []
    for p in sorted(sdir.glob("[0-9][0-9][0-9][0-9].csv")):
        with open(p, encoding="utf-8") as f:
            rows += list(csv.DictReader(f))
    return rows


def write_store(sdir, rows):
    by = defaultdict(list)
    for r in rows:
        by[r["week"][:4]].append(r)
    sdir.mkdir(parents=True, exist_ok=True)
    for y, rs in by.items():
        rs.sort(key=lambda r: (r["week"], r["unit"], int(r["rank"])))
        with open(sdir / f"{y}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for r in rs:
                w.writerow({k: r[k] for k in FIELDS})


def merge(old, new_rows, rebuild_weeks, seed_rows=()):
    """old（保存済み）に、作り直した週（rebuild_weeks）を差し替え、seed は無い週だけ足す"""
    keep = [r for r in old if r["week"] not in rebuild_weeks]
    have = {r["week"] for r in keep} | set(rebuild_weeks)
    seeded = [r for r in seed_rows if r["week"] not in have]
    return keep + [{k: str(v) for k, v in r.items()} for r in new_rows] + seeded, len({r["week"] for r in seeded})


def public_json(rows, codes, pub_weeks=PUB_WEEKS, pub_top=PUB_TOP):
    weeks = sorted({r["week"] for r in rows})[-pub_weeks:]
    wset = set(weeks)
    wi = {w: i for i, w in enumerate(weeks)}
    meta = {}
    sec_r, sec_s, st_r, st_s = defaultdict(dict), defaultdict(dict), defaultdict(dict), defaultdict(dict)
    for r in rows:
        w = r["week"]
        if w not in wset:
            continue
        meta[w] = (r["week_end"], int(r["n_days"]), int(r["provisional"]))
        k = wi[w]
        if r["unit"] == "sector":
            sec_r[r["code"]][k] = int(r["rank"])
            sec_s[r["code"]][k] = round(float(r["share_pct"]), 1)
        elif r["unit"] == "stock":
            st_r[r["code"]][k] = int(r["rank"])
            st_s[r["code"]][k] = round(float(r["share_pct"]), 2)
    n = len(weeks)
    secs = sorted(sec_r, key=lambda s: (sec_r[s].get(n - 1, 99), s))
    pick = [c for c in st_r if min(st_r[c].values()) <= pub_top]
    pick.sort(key=lambda c: (st_r[c].get(n - 1, 10 ** 6), c))
    sidx = {s: i for i, s in enumerate(secs)}
    items = []
    for c in pick:
        nm, s = codes.get(c) or ["", ""]
        items.append({"c": c, "n": nm or c, "s": sidx.get(s),
                      "r": [st_r[c].get(k) for k in range(n)], "sh": [st_s[c].get(k) for k in range(n)]})
    return {
        "name": "売買代金の順位の入れ替わり（週ごと・東証33業種と銘柄）",
        "note": "週（月〜金）の売買代金の合計の順位とシェア（%）。売買代金は成立した取引の金額で、どちらの向きの金額とも言えません。"
                "シェアは内国株の合計に対する割合。ETF・REIT は入れていません。銘柄は期間中に一度でも"
                f"{pub_top}位以内に入ったもの。r が null の週は{KEEP_STOCK}位より下。最後の週の p=1 は週の途中（暫定）。",
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "weeks": [{"w": w, "end": meta[w][0], "d": meta[w][1], "p": meta[w][2]} for w in weeks],
        "sector": {"names": secs, "r": [[sec_r[s].get(k) for k in range(n)] for s in secs],
                   "sh": [[sec_s[s].get(k) for k in range(n)] for s in secs]},
        "stock": items,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--out", default=None, help="公開 JSON（既定 docs/rank_shift.json）")
    ap.add_argument("--seed", default=None, help="過去分の CSV（FIELDS と同じ列・unit は sector/stock）。保存に無い週だけ足す")
    ap.add_argument("--today", default=None, help="今日（JST・暫定の判定用。テスト用）")
    ap.add_argument("--weeks", type=int, default=REBUILD_WEEKS)
    args = ap.parse_args(argv)
    root = Path(args.root)
    sdir = root / "data" / "shuyaku"
    today = args.today or datetime.now(JST).date().isoformat()
    since = (date.fromisoformat(monday(today)) - timedelta(weeks=args.weeks - 1)).isoformat()
    codes = update_codes(root)
    daily, drop = load_tv(root, since, codes)
    if not daily:
        log("::error::日足が無い")
        return 1
    # 日足の元（bars_backfill の最初の日）が since より後なら、その最初の週は途中からなので作り直さない（seed に任せる）
    src = source_start(root)
    full_from = since
    if src and src > since:
        full_from = src if src == monday(src) else (date.fromisoformat(monday(src)) + timedelta(weeks=1)).isoformat()
    new = [r for r in weekly_rows(daily, codes, today) if r["week"] >= full_from]
    if not new:
        log("::error::作り直せる週が無い")
        return 1
    rebuild = sorted({r["week"] for r in new})
    seed = []
    if args.seed:
        with open(args.seed, encoding="utf-8-sig") as f:
            seed = [r for r in csv.DictReader(f) if r.get("unit") in ("sector", "stock")]
    rows, nseed = merge(read_store(sdir), new, set(rebuild), seed)
    write_store(sdir, rows)
    pub = public_json(rows, codes)
    out = Path(args.out) if args.out else root / "docs" / "rank_shift.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(pub, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    last = pub["weeks"][-1]
    top = sorted(pub["stock"], key=lambda x: x["r"][-1] or 10 ** 6)[:3]
    log(f"日足 {min(daily)}〜{max(daily)}（{len(daily)}日・規則1で捨てた行 {drop}）・作り直した週 {rebuild[0]}〜{rebuild[-1]}（{len(rebuild)}週）"
        f"・seed で足した週 {nseed}")
    log(f"{out.name}: {len(pub['weeks'])}週・業種 {len(pub['sector']['names'])}・銘柄 {len(pub['stock'])}・{out.stat().st_size:,}バイト"
        f"・最後の週 {last['w']}（{last['end']}・{last['d']}日・暫定 {last['p']}）")
    log("最後の週の上位: " + " / ".join(f"{x['r'][-1]}位 {x['n']}" for x in top))
    return 0


if __name__ == "__main__":
    sys.exit(main())
