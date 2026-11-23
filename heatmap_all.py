# -*- coding: utf-8 -*-
"""全銘柄のヒートマップ用データ → docs/heatmap_all.json（2026-11-23・kaburadar.jp/heatmap.html の「全銘柄」）

■ 何のため
  ヒートマップの「主要（約336）／全銘柄（約3,700）」の切替の「全銘柄」。主要（docs/heatmap.json）は今までどおり場中30分ごと。
  こちらは引け後の1日1回（movers-daily.yml の中・18:30 の回）。

■ 取得は増やさない（Yahoo に行かない）
  docs/prices.json（movers_daily.py が同じジョブで作った全銘柄の終値・騰落・出来高倍率・売買代金）を並べ替えるだけ。
  名前・業種は tenbagger_universe.csv（JPX の一覧から毎週作り直し）、時価総額は tenbagger_shares.csv の発行済株式数 × 終値。
  ★取引時間中の全銘柄の取得は今どこにも無い（場中に全銘柄を取っているワークフローは無い）ので、場中の更新はしない。
    場中に足すなら新しい取得（3,700銘柄÷200＝19回の問い合わせ×回数）になる。主要の30分ごと（1回1問い合わせ×約336銘柄）とは桁が違う。

■ 形（容量を小さく・列は heatmap.json の主要版と同じ名前）
  {"updated","trade_date","count","cols":[...],"secs":[業種名...],"rows":[[t,n,si,c,c5,c20,r,v,m,a], ...]}
    t コード / n 名前 / si 業種の番号（secs の添字）/ c 当日の騰落率%（小数1桁）/ c5・c20 5日・20日の騰落率%
    r 出来高倍率（当日÷直近20日平均）/ v 売買代金（百万円・整数。主要版の億円だと小型株が 0.0 になる）
    m 時価総額（億円・整数。株式数が無い銘柄は null）/ a 略称（名前と違うときだけ。無ければ ""）
    終値は入れない（gzip で約12KB。銘柄ページにある）。
  ★需給の列（2026-11-23 追加・統括「全銘柄表示でも需給の色を使える」）: heatmap.json と同じ名前・同じ作り方
    （heatmap_fetch.load_facts をそのまま使う＝ docs/shinyo_meigara.json・karauri.json・kessan.json。新しい取得なし）
    mr 信用倍率 / mb 信用買い残の5営業日前比% / sr 大口の空売り残高% / sx 前回報告比pt / sn 報告機関数 / kd 次の決算予定日
    行の後ろの null は省く。先頭に md（信用残の申込日）・sd（空売りの計算日）・mx（市場全体の買い残の大きな変化）。データの無い銘柄は null（地図では灰色）。
  gzip で約100KB（2026-11-20 の値で測った）
  取れない値は null。業種の無い銘柄（新規上場で JPX の一覧にまだ無い等）は地図に入れない（no_sector に数だけ）。

■ px_sanity の守り
  当日の騰落: 前日の終値（終値÷(1+騰落率)）と当日の終値の2点を px_sanity.problems に通す（0以下・1億円超・2.5倍の飛び）。
  加えて値幅が制限値幅の2倍を超えるもの（分割の取り込み漏れ・桁の誤り）は c を出さない。
  5日・20日も同じ2点の検査（基準の日の終値と当日）。引っかかった値は null にして、件数をログと JSON の sanity に出す。
  prices.json の stale（その日に取れず前回の値を残した銘柄）は c・c5・c20・r を null（地図では灰色）。

使い方: python -X utf8 heatmap_all.py [--out docs/heatmap_all.json]
"""
import argparse
import csv
import json
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from heatmap_abbr import abbr            # noqa: E402
from px_sanity import problems           # noqa: E402
from stale_guard import keep_newest      # noqa: E402
from stophigh import limit_width         # noqa: E402

JST = timezone(timedelta(hours=9))
COLS = ["t", "n", "si", "c", "c5", "c20", "r", "v", "m", "a", "mr", "mb", "sr", "sx", "sn", "kd"]
SECTORS = ["水産・農林業", "鉱業", "建設業", "食料品", "繊維製品", "パルプ・紙", "化学", "医薬品", "石油・石炭製品",
           "ゴム製品", "ガラス・土石製品", "鉄鋼", "非鉄金属", "金属製品", "機械", "電気機器", "輸送用機器", "精密機器",
           "その他製品", "電気・ガス業", "陸運業", "海運業", "空運業", "倉庫・運輸関連業", "情報・通信業", "卸売業",
           "小売業", "銀行業", "証券、商品先物取引業", "保険業", "その他金融業", "不動産業", "サービス業"]


def log(*a):
    print(*a, flush=True)


def universe():
    u = {}
    with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            u[r["code"].strip()] = (unicodedata.normalize("NFKC", (r.get("name") or "").strip()),
                                    (r.get("sector33") or "").strip())
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
        log(f"株式数が読めない（時価総額は出さない）: {e}")
    return s


def rd1(x):
    return None if x is None else round(x, 1)


def ok_move(p, chg, check_limit):
    """終値 p と騰落率 chg(%) → この騰落率を使ってよいか"""
    if chg is None or p is None:
        return False
    if chg <= -100:
        return False
    base = p / (1 + chg / 100)
    if problems([("a", base), ("b", p)]):
        return False
    if check_limit and abs(p - base) > 2 * limit_width(base) + 1e-6:
        return False
    return True


def build(prices, u, sh, facts=None):
    items = prices.get("items") or {}
    si = {s: i for i, s in enumerate(SECTORS)}
    rows, bad, nosec = [], {"c": 0, "c5": 0, "c20": 0, "stale": 0}, []
    for c in sorted(items):
        it = items[c]
        n, s = u.get(c, ("", ""))
        if s not in si:
            nosec.append(c)
            continue
        p = it.get("p")
        if p is None or not (0 < p <= 1e8):
            continue
        stale = bool(it.get("stale"))
        vals = {}
        for k, lim in (("c", True), ("c5", False), ("c20", False)):
            v = it.get(k)
            if stale:
                v = None
            elif v is not None and not ok_move(p, v, lim):
                bad[k] += 1
                v = None
            vals[k] = v
        if stale:
            bad["stale"] += 1
        r = None if stale else it.get("vr")
        v = it.get("v")
        m = round(p * sh[c] / 1e8) if c in sh else None
        a = abbr(c, n) if n else ""
        rows.append([c, n or c, si[s], rd1(vals["c"]), vals["c5"], vals["c20"], r,
                     None if v is None else int(round(v)), m if m is None else max(m, 1),
                     a if a and a != n else ""] + [(facts or {}).get(c, {}).get(k) for k in COLS[10:]])
    for r in rows:                     # 後ろの null は省く（画面は無い列を null と読む・約15KB 小さくなる）
        while len(r) > 10 and r[-1] is None:
            r.pop()
    return rows, bad, nosec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "docs" / "heatmap_all.json"))
    ap.add_argument("--prices", default=str(HERE / "docs" / "prices.json"))
    args = ap.parse_args()
    prices = json.loads(Path(args.prices).read_text(encoding="utf-8"))
    if not prices.get("trade_date"):
        log("::error::prices.json に trade_date が無い")
        return 1
    try:
        from heatmap_fetch import load_facts
        facts, asof = load_facts()
    except Exception as e:
        log(f"::warning::需給の列を作れない（騰落だけで出す）: {e}")
        facts, asof = {}, {}
    rows, bad, nosec = build(prices, universe(), shares(), facts)
    if len(rows) < 3000:
        log(f"::error::銘柄が少なすぎる（{len(rows)}）。書かない")
        return 1
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"), "trade_date": prices["trade_date"],
           "count": len(rows), "src": "prices.json（movers_daily.py・引け後の1日1回）",
           "sanity": bad, "no_sector": len(nosec)}
    out.update({k: v for k, v in asof.items() if v})
    out.update({"cols": COLS, "secs": SECTORS, "rows": rows})
    if not keep_newest(args.out, out, "trade_date", "全銘柄のヒートマップ"):
        return 0
    sz = Path(args.out).stat().st_size
    up = sum(1 for r in rows if (r[3] or 0) > 0)
    dn = sum(1 for r in rows if (r[3] or 0) < 0)
    log(f"{Path(args.out).name}: {len(rows)}銘柄（上昇{up}・下落{dn}）取引日 {out['trade_date']}・{sz:,}バイト"
        f"・使わなかった値 {bad}・業種の無い銘柄 {len(nosec)}（{' '.join(nosec[:10])}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
