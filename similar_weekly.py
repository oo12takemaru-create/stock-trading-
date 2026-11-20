# -*- coding: utf-8 -*-
"""そっくり銘柄・真逆銘柄（週1回）→ docs/similar/{コード}.json（株レーダー kaburadar.jp・2026-11-20）

■ 何か
  銘柄ごとに「値動きがいちばん似ている10銘柄」と「いちばん逆に動いた10銘柄」を、
  全銘柄の総当たりの相関係数から出す。銘柄ページ（stocks/{コード}.html）の中の節が読む。

■ 取得は増やさない
  収集側にもう貯まっている日足だけを読む。
    data/bars_backfill/YYYY-MM.csv.gz … 過去1年（2026-11-18 に1回だけ書いた・分割は当時の時点で遡って調整済み）
    data/bars/YYYY/YYYY-MM-DD.csv.gz  … 毎日の当日記録（その日に見えた値）
  同じ日が両方にあるときは backfill を使う（分割の調整が揃っているため）。
  backfill より後に権利落ちした株式分割は docs/splits.json の by_code（ex・ratio）で前の終値を割って揃える。

■ 計算の決まり（数字は下の定数）
  1. 期間: 直近 WINDOW(=250) 営業日の日次騰落率（終値÷前日終値−1）。日足が足りないうちはあるだけ（meta.n_days に書く）
  2. 騰落率を「無かったこと」にする日: その日か前日の出来高が0（値が付いていない）、終値が無い、
     1日で 0.4倍未満・2.5倍超（分割の取り込み漏れなど。ledger2_score と同じ線）
  3. 除く銘柄（どれか1つ）:
       - 期間の中で騰落率がある日が MIN_DAYS(=200) 日未満（上場から日が浅い・売買が少ない。上場60営業日未満もここで落ちる）
       - 期間の中で出来高0の日が ZERO_VOL_MAX(=20%) を超える
       - 期間の売買代金（終値×出来高）の中央値が MIN_VALUE_MED(=500万円) 未満
     内国株（tenbagger_universe.csv＝東証3市場）の4桁コードだけ。ETF・REIT・5桁（社債型の種類株式など）は入れない
  4. 2つの相関
       - raw: 騰落率そのものの相関
       - ex（市場全体の動きを引いた相関）: 各銘柄の騰落率から、同じ日の「対象の全銘柄の騰落率の平均（等金額）」×β を
         引いてから相関（β は銘柄ごとの回帰の傾き）。素の相関だと大型株どうしが「みんなトヨタに似ている」になるため。
         β を掛けずにそのまま引くと、値動きの小さい銘柄どうしが見かけ上 0.9 以上で似てしまうので β で引く
     どちらも、2つの銘柄の騰落率が両方そろった日だけで計算（ペアごと）。重なりが MIN_OVERLAP(=200) 日未満の組は出さない
  5. 銘柄ごとに、主役（MAIN = ex）の上位10（そっくり）・下位10（真逆）。同じ業種（33業種）を除いた上位10・下位10も別に持つ

■ 出力（docs/ → GitHub Pages）
  docs/similar/{コード}.json
    {"code","asof" 期間の最後の日,"from" 期間の最初の日,"n_days" 期間の日数,"main":"ex",
     "r": {コード: [銘柄名, 業種, 相関ex, 相関raw, 重なった日数]},   ← 4つの並びに出てくる銘柄だけ（重複しない）
     "s": [そっくり10のコード], "o": [真逆10], "sx": [業種が違うそっくり10], "ox": [業種が違う真逆10]}
  docs/similar/meta.json  計算した日・期間・決まり・件数
  docs/similar/index.json 銘柄ごとの「そっくり1位・真逆1位」だけ（銘柄ページの生成が冒頭の1行を焼く・約230KB）
  除いた銘柄のファイルは作らない（前の週にあれば消す）。画面は「ファイルが無い＝節を出さない」。

■ git の履歴
  週1回、全ファイル（約3,500）を書き換える。1ファイル 約1.6KB（gzip 約0.7KB）→ 1回 約2.5MB（zlib 後）→ 年 約130MB。
  docs/px1y（本体の週1回の書き換え）と同じ考え方・同じ桁。中身が同じなら書かない（書き換えない）。

使い方:
  python -X utf8 similar_weekly.py                       # 週1回（similar-weekly.yml）
  python -X utf8 similar_weekly.py --out <dir>           # 試験（本番の場所に書かない）
  python -X utf8 similar_weekly.py --report 7203 9984    # 計算だけして並びを表示
"""
import argparse
import csv
import gzip
import json
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "similar"

WINDOW = 250            # 直近の営業日（騰落率の数）
MIN_DAYS = 200          # 期間の中で騰落率がある日がこれ未満の銘柄は除く
MIN_OVERLAP = 200       # 2銘柄の重なりがこれ未満の組は出さない
ZERO_VOL_MAX = 0.20     # 出来高0の日の割合がこれを超える銘柄は除く
MIN_VALUE_MED = 5e6     # 売買代金の中央値（円）がこれ未満の銘柄は除く（500万円）
JUMP_LO, JUMP_HI = 0.4, 2.5
TOPN = 10
MAIN = "ex"             # 画面の主役（理由は _報告_規制の保存とそっくり銘柄.md）


def log(*a):
    print(*a, flush=True)


def fnum(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


# ──────────────────────────────────────── 読み込み
def load_universe(root):
    out = {}
    with open(root / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            c = (r.get("code") or "").strip()
            if len(c) == 4:              # 5桁（社債型の種類株式など）は普通株ではないので入れない
                out[c] = {"name": unicodedata.normalize("NFKC", r.get("name") or "").strip(),
                          "sector": (r.get("sector33") or "").strip() or None}
    return out


def _read_csv_gz(p, rows, codes):
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = r.get("code")
            if c in codes:
                rows[(r["date"], c)] = (fnum(r.get("close")), fnum(r.get("volume")))


def load_bars(root, codes):
    """{(date, code): (close, volume)}。同じ日は backfill を優先。backfill の最後の日も返す"""
    live, bf = {}, {}
    for p in sorted((root / "data" / "bars").glob("*/*.csv.gz")):
        _read_csv_gz(p, live, codes)
    for p in sorted((root / "data" / "bars_backfill").glob("*.csv.gz")):
        _read_csv_gz(p, bf, codes)
    bf_end = max((d for d, _ in bf), default="")
    rows = {k: v for k, v in live.items() if k[0] > bf_end}
    rows.update(bf)
    return rows, bf_end


def split_events(root, bf_end):
    """backfill より後に権利落ちした分割 {code: [(ex, ratio)]}"""
    try:
        sj = json.loads((root / "docs" / "splits.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    for c, lst in (sj.get("by_code") or {}).items():
        for s in lst or []:
            ex, r = s.get("ex"), s.get("ratio")
            if ex and r and ex > bf_end and float(r) > 0:
                out.setdefault(c, []).append((ex, float(r)))
    return out


def build_matrix(rows, splits):
    dates = sorted({d for d, _ in rows})
    codes = sorted({c for _, c in rows})
    di = {d: i for i, d in enumerate(dates)}
    ci = {c: i for i, c in enumerate(codes)}
    C = np.full((len(dates), len(codes)), np.nan)
    V = np.full((len(dates), len(codes)), np.nan)
    for (d, c), (cl, vo) in rows.items():
        C[di[d], ci[c]] = cl if cl and cl > 0 else np.nan
        V[di[d], ci[c]] = vo if vo is not None else np.nan
    for c, lst in splits.items():
        if c not in ci:
            continue
        j = ci[c]
        for ex, r in lst:                      # 権利落ちの前の終値を ratio で割る（株数は掛ける）
            m = np.array([d < ex for d in dates])
            C[m, j] = C[m, j] / r
            V[m, j] = V[m, j] * r
    return dates, codes, C, V


# ──────────────────────────────────────── 計算
def returns(C, V):
    """日次騰落率（行は C の2行目から）。出来高0の日・前日が0の日・飛びは NaN"""
    R = C[1:] / C[:-1] - 1.0
    bad = ~np.isfinite(R) | (V[1:] == 0) | (V[:-1] == 0) | ~np.isfinite(V[1:]) | ~np.isfinite(V[:-1])
    bad |= (R + 1 < JUMP_LO) | (R + 1 > JUMP_HI)
    R[bad] = np.nan
    return R


def eligible(C, V, R):
    """除く銘柄の判定。返り値 (使う銘柄の bool, 理由の数)"""
    n_ret = np.isfinite(R).sum(axis=0)
    have = np.isfinite(C) & np.isfinite(V)
    zero = ((V == 0) & have).sum(axis=0)
    nday = have.sum(axis=0)
    zr = np.where(nday > 0, zero / np.maximum(nday, 1), 1.0)
    val = np.where(have, C * V, np.nan)
    with np.errstate(all="ignore"):
        med = np.nanmedian(np.where(np.isfinite(val), val, np.nan), axis=0)
    med = np.where(np.isfinite(med), med, 0.0)
    ok_days = n_ret >= MIN_DAYS
    ok_zero = zr <= ZERO_VOL_MAX
    ok_val = med >= MIN_VALUE_MED
    ok = ok_days & ok_zero & ok_val
    why = {"days_lt_min": int((~ok_days).sum()), "zero_volume": int((ok_days & ~ok_zero).sum()),
           "low_value": int((ok_days & ok_zero & ~ok_val).sum())}
    return ok, why


def pair_corr(R):
    """ペアごとに「両方そろった日」だけで相関を出す（NaN を含んだまま行列で）。返り値 (相関, 重なり日数)"""
    M = np.isfinite(R).astype(np.float64)
    X = np.where(M > 0, R, 0.0)
    N = M.T @ M
    Sx = X.T @ M                 # Sx[i,j] = j がある日の i の和
    Sxx = (X * X).T @ M
    Sxy = X.T @ X
    with np.errstate(all="ignore"):
        cov = N * Sxy - Sx * Sx.T
        vi = N * Sxx - Sx * Sx
        corr = cov / np.sqrt(vi * vi.T)
    corr[N < MIN_OVERLAP] = np.nan
    np.fill_diagonal(corr, np.nan)
    return corr, N.astype(np.int32)


def market_removed(R):
    """市場全体の動きを引いた騰落率: r_i − β_i × m（m＝同じ日の対象全銘柄の平均・β_i は期間の回帰の傾き）。
    ただ m を引く（β=1）と、値動きの小さい銘柄（優待株・社債型の種類株など）はどれも「−m」になって
    互いに 0.9 以上で似てしまう（2026-11-20 に手元で確認）。銘柄ごとの β で引けばそうならない"""
    m = np.nanmean(R, axis=1)
    out = np.full_like(R, np.nan)
    for j in range(R.shape[1]):
        r = R[:, j]
        k = np.isfinite(r) & np.isfinite(m)
        if k.sum() < 3:
            continue
        mk = m[k] - m[k].mean()
        vm = (mk * mk).sum()
        beta = ((r[k] - r[k].mean()) * mk).sum() / vm if vm > 0 else 0.0
        out[k, j] = r[k] - beta * m[k]
    return out


def compute(root=ROOT, window=WINDOW):
    t0 = time.time()
    uni = load_universe(root)
    rows, bf_end = load_bars(root, set(uni))
    dates, codes, C, V = build_matrix(rows, split_events(root, bf_end))
    # 日付: 対象の銘柄の半分以上に足がある日だけ（祝日のずれ・取りこぼしの日を入れない）
    full = np.isfinite(C).sum(axis=1) >= 0.5 * len(codes)
    dates = [d for d, k in zip(dates, full) if k]
    C, V = C[full], V[full]
    if len(dates) > window + 1:
        dates, C, V = dates[-(window + 1):], C[-(window + 1):], V[-(window + 1):]
    R = returns(C, V)
    ok, why = eligible(C, V, R)
    codes_ok = [c for c, k in zip(codes, ok) if k]
    Rk = R[:, ok]
    t1 = time.time()
    raw, N = pair_corr(Rk)
    ex, _ = pair_corr(market_removed(Rk))
    t2 = time.time()
    meta = {"asof": dates[-1], "from": dates[1], "n_days": len(dates) - 1, "n_codes": len(codes_ok),
            "n_universe": len(codes), "excluded": why, "bars_backfill_end": bf_end,
            "sec_load": round(t1 - t0, 1), "sec_corr": round(t2 - t1, 1)}
    return {"codes": codes_ok, "raw": raw, "ex": ex, "N": N, "uni": uni, "meta": meta, "R": Rk}


def rank_lists(res, main=MAIN):
    """銘柄ごとの4つの並び。{code: {"s","o","sx","ox"} (インデックスの並び)}"""
    codes, uni = res["codes"], res["uni"]
    A = res[main]
    sec = np.array([uni.get(c, {}).get("sector") or "" for c in codes])
    out = {}
    for i, c in enumerate(codes):
        a = A[i]
        valid = np.isfinite(a)
        if valid.sum() < TOPN * 2:
            continue
        idx = np.where(valid)[0]
        order = idx[np.argsort(-a[idx], kind="stable")]
        other = order[sec[order] != sec[i]] if sec[i] else order
        out[c] = {"s": order[:TOPN], "o": order[::-1][:TOPN], "sx": other[:TOPN], "ox": other[::-1][:TOPN]}
    return out


def r2(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 2)


def file_obj(res, i, lists):
    codes, uni = res["codes"], res["uni"]
    used = []
    for k in ("s", "o", "sx", "ox"):
        for j in lists[k]:
            if j not in used:
                used.append(j)
    r = {}
    for j in used:
        cj = codes[j]
        u = uni.get(cj, {})
        r[cj] = [u.get("name") or cj, u.get("sector"), r2(res["ex"][i, j]), r2(res["raw"][i, j]), int(res["N"][i, j])]
    m = res["meta"]
    return {"code": codes[i], "asof": m["asof"], "from": m["from"], "n_days": m["n_days"], "main": MAIN,
            "r": r, **{k: [codes[j] for j in lists[k]] for k in ("s", "o", "sx", "ox")}}


def write_all(res, out):
    out.mkdir(parents=True, exist_ok=True)
    lists = rank_lists(res)
    ci = {c: i for i, c in enumerate(res["codes"])}
    wrote = same = 0
    keep = {"meta.json", "index.json"}
    m0 = res["meta"]
    index = {"asof": m0["asof"], "from": m0["from"], "n_days": m0["n_days"], "main": MAIN,
             "cols": ["そっくり1位のコード", "銘柄名", "相関", "真逆1位のコード", "銘柄名", "相関"], "items": {}}
    files = {}
    for c, ls in lists.items():
        obj = file_obj(res, ci[c], ls)
        s0, o0 = obj["s"][0], obj["o"][0]
        index["items"][c] = [s0, obj["r"][s0][0], obj["r"][s0][2], o0, obj["r"][o0][0], obj["r"][o0][2]]
        files[f"{c}.json"] = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    # 銘柄ページの生成（gen_stocks.py）が冒頭の1行を焼くための索引（1ファイル・約3,000銘柄×1行・約230KB）
    files["index.json"] = json.dumps(index, ensure_ascii=False, separators=(",", ":"))
    for name, txt in files.items():
        p = out / name
        keep.add(name)
        if p.exists() and p.read_text(encoding="utf-8") == txt:
            same += 1
            continue
        p.write_text(txt, encoding="utf-8", newline="\n")
        wrote += 1
    removed = 0
    for p in out.glob("*.json"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    if wrote == 0 and removed == 0 and (out / "meta.json").exists():
        log(f"中身は前回と同じ（銘柄 {len(lists)}）。meta.json も書き換えない")
        return wrote, same, removed
    m = dict(res["meta"])
    m.update({"updated": datetime.now(JST).isoformat(timespec="seconds"), "main": MAIN, "n_files": len(lists),
              "rule": {"window": WINDOW, "min_days": MIN_DAYS, "min_overlap": MIN_OVERLAP,
                       "zero_volume_max": ZERO_VOL_MAX, "min_value_median_yen": MIN_VALUE_MED,
                       "jump": [JUMP_LO, JUMP_HI], "topn": TOPN,
                       "ex": "各銘柄の騰落率から、同じ日の対象全銘柄の平均（等金額）×β（銘柄ごとの回帰の傾き）を引いてから相関",
                       "raw": "騰落率そのものの相関", "price": "日足の終値（株式分割は調整済み・配当は未調整）"},
              "source": "収集側の日足（data/bars_backfill・data/bars）から自前で計算"})
    (out / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    log(f"書いた {wrote} / 同じ {same} / 消した {removed} / 銘柄 {len(lists)}")
    return wrote, same, removed


def report(res, codes):
    lists = rank_lists(res)
    ci = {c: i for i, c in enumerate(res["codes"])}
    uni = res["uni"]
    for c in codes:
        if c not in lists:
            log(f"{c}: 除外（データが足りない・売買が少ない）")
            continue
        i = ci[c]
        log(f"== {c} {uni[c]['name']}（{uni[c]['sector']}）")
        for k, title in (("s", "そっくり"), ("o", "真逆"), ("sx", "そっくり・異業種"), ("ox", "真逆・異業種")):
            log(f"  [{title}]")
            for j in lists[c][k]:
                cj = res["codes"][j]
                log(f"    {cj} {uni[cj]['name'][:14]:<14} {uni[cj]['sector'] or '':<8} ex={res['ex'][i, j]:+.3f} raw={res['raw'][i, j]:+.3f} n={res['N'][i, j]}")
        # 素の相関で並べたときの上位（主役を決める比較用）
        a = res["raw"][i]
        idx = np.where(np.isfinite(a))[0]
        top = idx[np.argsort(-a[idx])][:TOPN]
        log("  [参考: 素の相関の上位] " + "・".join(f"{res['codes'][j]} {uni[res['codes'][j]]['name'][:8]}({a[j]:+.2f})" for j in top))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--report", nargs="*")
    a = ap.parse_args()
    res = compute()
    log(json.dumps(res["meta"], ensure_ascii=False))
    if a.report is not None:
        report(res, a.report)
        return 0
    if res["meta"]["n_days"] < MIN_OVERLAP:
        log(f"::error::期間が {res['meta']['n_days']} 日しかない（{MIN_OVERLAP} 日未満）。書かない")
        return 1
    write_all(res, Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
