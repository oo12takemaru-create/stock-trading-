# -*- coding: utf-8 -*-
"""予兆レーダー 7章「チャート条件を足す」＋ 急騰・急落の割合と逆引き（2026-11-19）

仮説・帯・急騰の定義は yochou_context_hyp.py（集計の前に別コミットで固定）。このファイルはそれを読んで数えるだけ。

■ 文脈（context）の計算
  出来事の base_date（知り得たあとの最初の始値の日）より前の、最後の営業日の終値までの値だけを使う。
  値動きの出来事（出来高の急増・窓・52週・年初来・ストップ高安）は base_date が翌営業日なので、出来事の日の終値まで。
  株価の表は ledger2_panel（分割調整済み A）。遡及は .cache/px5y（5年）、当日記録は data/bars_backfill ＋ data/bars。
  置き場（過去の行は書き換えない・別ファイル）:
    data/ledger2/context/backfill/YYYY-MM.csv.gz   遡及の出来事の文脈（--px5y で作り直すときだけ書く）
    data/ledger2/context/live/YYYY-MM.csv          当日記録の出来事の文脈（毎営業日に追記・(date, code) で重複しない）

■ 集計（yochou.py と同じ道具: stat_record・vs_all・日付を1観測・Bartlett・前半/後半）
  出来事1つ × 文脈1つ（帯）。仮説の帯と、同じ文脈の他の帯（比べるため）。窓 5・20・60。
  予兆に出す条件も yochou.py と同じ（対TOPIX と全銘柄との差の両方で confirmed・同じ向き・発生日数50日以上・20営業日）。

■ 急騰・急落（A）と逆引き（B）は株価の5年が要るので --px5y のときだけ計算し、data/ledger2/stats/surge.json に置く。
  毎営業日の実行はそれを読んで公開側（docs/yochou/stats.json・today.json・surge_before.json）に載せるだけ。

使い方:
  python -X utf8 yochou_context.py --px5y <dir>            # 遡及の文脈と急騰を作り直す（手元・手動）
  python -X utf8 yochou_context.py                         # 毎営業日（yochou.py の後）: 当日の文脈を追記→集計→公開側に足す
  --out <dir> --docs-out <dir>  試験用の書き先
"""
import argparse
import csv
import gzip
import io
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

import yochou as Y
import yochou_context_hyp as HY

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "ledger2"
DOCS_OUT = HERE / "docs" / "yochou"
FIELDS = ("ma25", "ma75", "ma200", "dev25", "hi52", "ret20", "vr20", "atr")
# 文脈を付ける種類（値動きの出来事。どれも引け後か立会中に知り得る＝base_date は翌営業日）
PRICE_TYPES = ("volume_surge", "gap_up", "gap_down", "high52_close", "low52_close", "ytd_high_close", "ytd_low_close",
               "stop_high", "stop_low", "stop_high_touch", "stop_low_touch")
SIGNAL_MIN_DAYS = Y.SIGNAL_MIN_DAYS
WINDOW = Y.WINDOW
MAX_CTX_SIGNALS = 30

CTX_JA = {
    ("ma200", "above"): "200日線より上", ("ma200", "below"): "200日線より下",
    ("dev25", "le-10"): "25日線より10%以上下", ("dev25", "gt-10"): "25日線からの乖離が−10%より上",
    ("ret20", "le10"): "直近20日の騰落率が+10%以下", ("ret20", "10to20"): "直近20日の騰落率が+10〜20%",
    ("ret20", "gt20"): "直近20日の騰落率が+20%超",
    ("hi52", "ge-5"): "52週高値から5%以内", ("hi52", "lt-5"): "52週高値から5%より下",
    ("atr", "lt3"): "値幅（ATR）が株価の3%未満", ("atr", "ge3"): "値幅（ATR）が株価の3%以上",
}


# 公開する文面の言い換え（仮説の中身は変えない。禁止語「買い」を含む言い回しだけ。yochou_context_hyp.py は先に固定したまま）
PUBLIC_FIX = (("新しい買い手が入った印", "新しい参加者が入った印"), ("過熱していない更新", "行き過ぎていない更新"))


def pub_text(t):
    for a, b in PUBLIC_FIX:
        t = t.replace(a, b)
    return t


def log(*a):
    print(*a, flush=True)


# ════════════════════════════════════════ 文脈の値
def ctx_matrices(pn):
    """{欄: [T, N]}。pn は ledger2_panel.Panel（調整済み A）"""
    import pandas as pd
    import ledger2_panel as P
    M = pn._metrics()
    T, N = pn.C.shape
    out = {k: np.full((T, N), np.nan) for k in FIELDS}
    for j in range(N):
        idx = np.flatnonzero(np.isfinite(pn.C[:, j]))
        if len(idx) < 2:
            continue
        c = pd.Series(pn.C[idx, j])
        h = pd.Series(pn.H[idx, j])
        l = pd.Series(pn.L[idx, j])
        for k, n in (("ma25", 25), ("ma75", 75), ("ma200", 200)):
            ma = c.rolling(n, min_periods=n).mean()
            out[k][idx, j] = np.where(ma.notna(), (c > ma).astype(float), np.nan)
        pc = c.shift(1)
        tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1, skipna=False)
        atr = tr.rolling(14, min_periods=14).mean()
        out["atr"][idx, j] = (atr / c * 100).values
    out["dev25"] = M["g25"].copy()
    out["ret20"] = M["c20"].copy()
    out["vr20"] = M["vr"].copy()
    hi = M["hi52"].copy()
    hi[~(M["n"] >= P.MIN_ROWS52)] = np.nan           # 履歴が短い銘柄の52週は出さない（prices_like と同じ）
    out["hi52"] = hi
    return out


def ctx_row_index(pn, base_date, date):
    """base_date より前の最後の営業日の行（出来事の日より後にはならない）"""
    i = int(np.searchsorted(np.array(pn.dates), base_date or date, side="left")) - 1
    j = pn.pos.get(date)
    if j is not None and i > j:
        i = j
    return i


def ctx_values(CM, i, j):
    out = {}
    for k in FIELDS:
        v = CM[k][i, j]
        if np.isfinite(v):
            out[k] = int(v) if k in ("ma25", "ma75", "ma200") else round(float(v), 2)
    return out


def compute_contexts(pn, CM, evs):
    """evs: [{date, code, base_date}] → {(date, code): ctx}"""
    out = {}
    for e in evs:
        key = (e["date"], e["code"])
        if key in out:
            continue
        j = pn.cpos.get(e["code"])
        if j is None:
            continue
        i = ctx_row_index(pn, e.get("base_date"), e["date"])
        if i < 0:
            continue
        out[key] = ctx_values(CM, i, j)
    return out


def write_ctx_csv(path, rows, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    if mode == "w":
        w.writerow(("date", "code") + FIELDS)
    for (d, c), x in sorted(rows.items()):
        w.writerow([d, c] + ["" if x.get(k) is None else x[k] for k in FIELDS])
    data = buf.getvalue()
    if path.suffix == ".gz":
        with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
            f.write(data)
    else:
        with open(path, mode, encoding="utf-8", newline="\n") as f:
            if mode == "a" and path.stat().st_size == 0:
                f.write(",".join(("date", "code") + FIELDS) + "\n")
            f.write(data)


def read_ctx(dir_):
    out = {}
    for f in sorted(Path(dir_).glob("*.csv*")):
        op = gzip.open if f.suffix == ".gz" else open
        with op(f, "rt", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                x = {}
                for k in FIELDS:
                    v = r.get(k)
                    if v not in (None, ""):
                        x[k] = float(v)
                out[(r["date"], r["code"])] = x
    return out


def band_of(ctx, name):
    """帯の名前（無ければ None）"""
    if name == "ma200":
        v = ctx.get("ma200")
        return None if v is None else ("above" if v >= 1 else "below")
    v = ctx.get(name)
    if v is None:
        return None
    for b, lo, hi in HY.BANDS[name]:
        if (lo is None or v > lo) and (hi is None or v <= hi):
            return b
    return None


# ════════════════════════════════════════ 文脈つきの統計
def ctx_groups():
    """[(type, ctx_name, band, hyp_id or None)]: 仮説の帯 ＋ 同じ文脈の他の帯（比べるため）"""
    out, seen = [], set()
    for hid, t, cn, b, _, _ in HY.HYPOTHESES:
        out.append((t, cn, b, hid))
        seen.add((t, cn, b))
    for hid, t, cn, b, _, _ in HY.HYPOTHESES:
        for bb in [x[0] for x in HY.BANDS[cn]]:
            if (t, cn, bb) not in seen:
                out.append((t, cn, bb, None))
                seen.add((t, cn, bb))
    return out


def build_ctx_stats(events, scores, ctx, baseline):
    groups = ctx_groups()
    want = defaultdict(list)                      # type → [(cn, band, hid)]
    for t, cn, b, hid in groups:
        want[t].append((cn, b, hid))
    obs = defaultdict(list)                       # (t, cn, b, h) → [(date, v, origin, base_date)]
    for (eid, h), s in scores.items():
        if h not in HY.WINDOWS_CTX or s.get("status") == "invalid":
            continue
        e = events.get(eid)
        if not e or e["event_type"] not in want or e["code"] == "MKT":
            continue
        v = s.get("ret")
        if v is None:
            continue
        cx = ctx.get((e["date"], e["code"]))
        if not cx:
            continue
        org = "live" if e.get("origin") == "live" else "backfill"
        for cn, b, _ in want[e["event_type"]]:
            if band_of(cx, cn) == b:
                obs[(e["event_type"], cn, b, h)].append((e["date"], v, org, e.get("base_date")))
    ordinal = Y.biz_ordinal(sorted({o[0] for xs in obs.values() for o in xs}))
    hyp = {hid: (t, cn, b, d, txt) for hid, t, cn, b, d, txt in HY.HYPOTHESES}
    recs = []
    for t, cn, b, hid in groups:
        for h in HY.WINDOWS_CTX:
            o = obs.get((t, cn, b, h), [])
            if not o:
                recs.append({"event_type": t, "context": f"{cn}:{b}", "window": h, "n": 0, "days": 0,
                             "status": "insufficient", "hyp_id": hid})
                continue
            rec = Y.stat_record(t, f"ctx:{cn}:{b}", h, [x[:3] for x in o], ordinal)
            rec["status_topix"] = rec["status"]
            if baseline.get(h):
                rec.update(Y.vs_all(o, baseline[h], ordinal, h))
            rec["signal_ok"] = (rec["status"] == "confirmed" and rec.get("vs_all_status") == "confirmed"
                                and rec.get("vs_all_direction") == rec["direction"] != 0)
            rec["context"] = f"{cn}:{b}"
            rec["context_label"] = CTX_JA.get((cn, b), f"{cn}:{b}")
            rec["hyp_id"] = hid
            if hid:
                rec["hypothesis"] = pub_text(hyp[hid][4])
                rec["hypothesis_dir"] = hyp[hid][3]
                rec["hypothesis_match"] = None if rec["status"] == "insufficient" else rec["direction"] == hyp[hid][3]
            else:
                rec["hypothesis"] = "（比べるための帯。仮説ではない）"
                rec["hypothesis_dir"] = 0
                rec["hypothesis_match"] = None
            rec["side"] = "up" if rec.get("direction", 0) > 0 else ("down" if rec.get("direction", 0) < 0 else None)
            rec["by_year"] = by_year(o, baseline.get(h, {}))
            recs.append(rec)
    return recs


def by_year(o, base):
    """年ごと: 件数・発生日数・対TOPIX の日ごとの平均・全銘柄との差の日ごとの平均・中央値"""
    ys = defaultdict(lambda: defaultdict(list))
    ysa = defaultdict(lambda: defaultdict(list))
    vals = defaultdict(list)
    for d, v, _, b in o:
        ys[d[:4]][d].append(v)
        vals[d[:4]].append(v)
        if b in base:
            ysa[d[:4]][d].append(v - base[b][0])
    out = {}
    for y in sorted(ys):
        dm = [sum(x) / len(x) for x in ys[y].values()]
        da = [sum(x) / len(x) for x in ysa[y].values()]
        out[y] = {"n": len(vals[y]), "days": len(dm), "day_mean": round(sum(dm) / len(dm), 3),
                  "median": round(Y.median(vals[y]), 3),
                  "vs_all_day_mean": round(sum(da) / len(da), 3) if da else None}
    return out


# ════════════════════════════════════════ 急騰・急落（A）と逆引き（B）
def surge_matrices(pn):
    """{定義: [T, N] の 1/0/NaN}（base 行 i の始値から i..i+N−1 行の終値）"""
    import pandas as pd
    import warnings
    T, N = pn.C.shape
    C = pd.DataFrame(pn.C)
    O = pn.O
    prev = C.ffill().shift(1)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        J = np.abs(np.log(C / prev)).values
    J = np.where(np.isfinite(J), J, 0.0)
    out = {}
    for name, d in HY.SURGE_DEFS.items():
        n = d["days"]
        rev = C.iloc[::-1]
        fmax = rev.rolling(n, min_periods=1).max().iloc[::-1].values
        fmin = rev.rolling(n, min_periods=1).min().iloc[::-1].values
        # 飛び: i+1..i+n−1 行の中に 0.4倍未満・2.5倍超
        jr = pd.DataFrame(J).iloc[::-1].rolling(n - 1, min_periods=1).max().iloc[::-1].shift(-1).values
        endc = np.full((T, N), np.nan)
        endc[: T - n + 1] = pn.C[n - 1:]
        ok = np.isfinite(O) & (O > 0) & np.isfinite(endc) & ~(np.nan_to_num(jr) > np.log(2.5))
        with np.errstate(invalid="ignore", divide="ignore"):
            if d["kind"] == "max":
                f = (fmax / O - 1) >= d["th"]
            else:
                f = (fmin / O - 1) <= d["th"]
        m = np.where(ok, f.astype(float), np.nan)
        out[name] = m
    return out


def stock_cols(pn):
    """全銘柄の基準に入れる列（物差しの指数・^ で始まるものは除く）"""
    return np.array([not c.startswith("^") for c in pn.codes])


def surge_eval(obs, base_rate, ordinal, n_days):
    """obs: [(base_date, flag)] → 割合・基準・倍率・日付単位の検定"""
    if not obs:
        return {"n": 0, "days": 0, "status": "insufficient"}
    by_day = defaultdict(list)
    for d, f in obs:
        by_day[d].append(f)
    days = sorted(by_day)
    hits = sum(f for _, f in obs)
    n = len(obs)
    br = sum(base_rate[d] for d, _ in obs) / n
    diffs = [sum(by_day[d]) / len(by_day[d]) - base_rate[d] for d in days]
    rec = {"n": n, "hits": int(hits), "rate": round(hits / n * 100, 3), "base_rate": round(br * 100, 3),
           "lift": round((hits / n) / br, 3) if br > 0 else None, "days": len(days),
           "period": [days[0], days[-1]]}
    if len(days) < 2:
        rec["status"] = "insufficient"
        return rec
    m, t, p, _ = Y.date_level_test(diffs, [ordinal[d] for d in days], n_days)
    half = len(days) // 2
    h1 = sum(diffs[:half]) / half if half else 0
    h2 = sum(diffs[half:]) / (len(days) - half)
    dr = Y.sign(m)
    if len(days) < Y.MIN_DAYS:
        st = "insufficient"
    elif p is not None and p < 0.05 and dr != 0 and Y.sign(h1) == dr and Y.sign(h2) == dr:
        st = "confirmed"
    elif p is not None and p < 0.10:
        st = "weak"
    else:
        st = "rejected"
    rec.update({"day_mean_diff": round(m * 100, 3), "p_value": round(p, 5) if p is not None else None,
                "half1": round(h1 * 100, 3), "half2": round(h2 * 100, 3), "direction": dr, "status": st})
    ys = defaultdict(list)
    for d, f in obs:
        ys[d[:4]].append((d, f))
    rec["by_year"] = {y: {"n": len(v), "rate": round(sum(f for _, f in v) / len(v) * 100, 3),
                          "base_rate": round(sum(base_rate[d] for d, _ in v) / len(v) * 100, 3)} for y, v in sorted(ys.items())}
    for y, x in rec["by_year"].items():
        x["lift"] = round(x["rate"] / x["base_rate"], 3) if x["base_rate"] > 0 else None
    return rec


def build_surge(pn, events, ctx):
    SM = surge_matrices(pn)
    cols = stock_cols(pn)
    dates = np.array(pn.dates)
    base_rate = {}
    for name, m in SM.items():
        with np.errstate(invalid="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                r = np.nanmean(m[:, cols], axis=1)
                cnt = np.isfinite(m[:, cols]).sum(axis=1)
        base_rate[name] = {pn.dates[i]: float(r[i]) for i in range(len(r)) if cnt[i] >= 500}
    # 出来事 → base 行
    groups = defaultdict(lambda: defaultdict(list))     # key → def → [(base_date, flag)]
    hyp_by_type = defaultdict(list)
    for t, cn, b, hid in ctx_groups():
        hyp_by_type[t].append((cn, b, hid))
    for e in events.values():
        if e["code"] == "MKT":
            continue
        j = pn.cpos.get(e["code"])
        if j is None:
            continue
        bd = e.get("base_date") or e["date"]
        i = int(np.searchsorted(dates, bd, side="left"))
        if i >= len(dates):
            continue
        b0 = pn.dates[i]
        keys = [(e["event_type"], "all")]
        cx = ctx.get((e["date"], e["code"]))
        if cx:
            for cn, b, _ in hyp_by_type.get(e["event_type"], []):
                if band_of(cx, cn) == b:
                    keys.append((e["event_type"], f"{cn}:{b}"))
        for name, m in SM.items():
            f = m[i, j]
            if not np.isfinite(f) or b0 not in base_rate[name]:
                continue
            for k in keys:
                groups[k][name].append((b0, float(f)))
    ordinal = Y.biz_ordinal(sorted(pn.dates))
    out = []
    for (t, band), defs in sorted(groups.items()):
        rec = {"event_type": t, "label": Y.label_of(t), "band": band}
        for name in HY.SURGE_DEFS:
            rec[name] = surge_eval(defs.get(name, []), base_rate[name], ordinal, HY.SURGE_DEFS[name]["days"])
        out.append(rec)
    # 全銘柄の割合（期間全体）
    overall = {name: round(sum(v.values()) / len(v) * 100, 3) for name, v in base_rate.items() if v}
    return out, overall, SM, base_rate


def build_reverse(pn, events, SM):
    """B: 急騰の始まり → その前20営業日（始まりの日を含む）に base_date があった出来事の種類"""
    cols = stock_cols(pn)
    T, N = pn.C.shape
    dates = np.array(pn.dates)
    types = sorted({e["event_type"] for e in events.values() if e["code"] != "MKT"})
    tix = {t: k for k, t in enumerate(types)}
    # 出来事の行列（種類ごとに [T, N] の bool）。メモリを抑えるため、種類ごとに (i, j) の集合で持つ
    pts = defaultdict(set)
    for e in events.values():
        if e["code"] == "MKT":
            continue
        j = pn.cpos.get(e["code"])
        if j is None:
            continue
        i = int(np.searchsorted(dates, e.get("base_date") or e["date"], side="left"))
        if i < T:
            pts[e["event_type"]].add((i, j))
    L = HY.LOOKBACK
    out = {}
    for name in ("surge20", "surge60x2"):
        m = SM[name]
        gap = HY.SURGE_EPISODE_GAP[name]
        eps = []                                     # (i, j)
        for j in np.flatnonzero(cols):
            q = np.flatnonzero(m[:, j] == 1)
            last = -10 ** 9
            for i in q:
                if i - last > gap:
                    eps.append((int(i), int(j)))
                last = i
        # 基準: 始まりの日ごとに、全銘柄（その日に始値がある）のうち前20営業日に出来事があった割合
        valid = np.isfinite(pn.O) & (pn.O > 0)
        rows = []
        for t in types:
            if not pts[t]:
                continue
            # その種類の記録がある期間だけ（最初の出来事の行から前20営業日がそろう「始まり」だけを数える）
            first = min(i for i, _ in pts[t])
            eps_t = [(i, j) for i, j in eps if i >= first + L - 1]
            A = np.zeros((T, N), dtype=np.int32)
            for i, j in pts[t]:
                A[i, j] = 1
            cs = np.cumsum(A, axis=0)
            W = cs.copy()
            W[L:] = cs[L:] - cs[:-L]                    # 行 i−L+1..i の合計
            had = W > 0
            day_base = {}
            for i in {i for i, _ in eps_t}:
                v = valid[i] & cols
                nv = v.sum()
                day_base[i] = (had[i] & v).sum() / nv if nv else 0.0
            ys = defaultdict(lambda: [0, 0, 0.0])
            for i, j in eps_t:
                y = ys[pn.dates[i][:4]]
                y[0] += 1
                y[1] += int(had[i, j])
                y[2] += day_base[i]
            n = len(eps_t)
            hit = sum(v[1] for v in ys.values())
            br = sum(v[2] for v in ys.values()) / n if n else 0
            rows.append({"event_type": t, "label": Y.label_of(t), "n_surges": n, "had_before": hit,
                         "from": pn.dates[min(i for i, _ in eps_t)] if eps_t else None,
                         "rate": round(hit / n * 100, 3) if n else None, "base_rate": round(br * 100, 3),
                         "lift": round((hit / n) / br, 3) if n and br > 0 else None,
                         "by_year": {y: {"n": v[0], "rate": round(v[1] / v[0] * 100, 2),
                                         "base_rate": round(v[2] / v[0] * 100, 2)} for y, v in sorted(ys.items())}})
        rows.sort(key=lambda r: -(r["lift"] or 0))
        out[name] = {"n_surges": len(eps), "period": [pn.dates[min(i for i, _ in eps)], pn.dates[max(i for i, _ in eps)]] if eps else None,
                     "by_year": dict(sorted(Counter(pn.dates[i][:4] for i, _ in eps).items())),
                     "rows": rows}
    return out


# ════════════════════════════════════════ 公開
def ctx_text(e, rec):
    who = e["code"]
    med = rec["median"]
    share = f"マイナスだった割合{rec['neg_rate']:.0f}%" if med is not None and med < 0 else f"プラスだった割合{rec['pos_rate']:.0f}%"
    va = f"（同じ日の全銘柄との差は中央値{rec['median_vs_all']:+.1f}%）" if rec.get("median_vs_all") is not None else ""
    return (f"【予兆】{who} {Y.phrase_of(e)}（{rec['context_label']}）。{Y.period_label(rec['period'])}で同じ条件は"
            f"{rec['n']:,}回（{rec['days']:,}日）。{rec['window']}営業日後の対TOPIXは中央値{med:+.1f}%{va}、{share}。")


def surge_fields(srec):
    if not srec:
        return None
    o = {}
    for name in HY.SURGE_DEFS:
        x = srec.get(name) or {}
        o[name] = {k: x.get(k) for k in ("n", "hits", "rate", "base_rate", "lift", "days", "status", "p_value")}
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px5y", help="yochou_px5y.py の出力。あれば遡及の文脈と急騰（A・B）を作り直す")
    ap.add_argument("--data", default=str(DATA))
    ap.add_argument("--out", default=None)
    ap.add_argument("--docs-out", default=str(DOCS_OUT))
    ap.add_argument("--docs", default=str(HERE / "docs"))
    ap.add_argument("--date", help="today.json の日付（既定: today.json の date）")
    a = ap.parse_args()
    t0 = time.time()
    data_dir, out = Path(a.data), Path(a.out or a.data)
    docs_out = Path(a.docs_out)
    now = datetime.now(Y.JST).isoformat(timespec="seconds")
    import ledger2_panel as P

    events, scores = Y.load_all(data_dir)
    log(f"出来事 {len(events)}件・答え {len(scores)}行  {time.time() - t0:.0f}秒")
    bf_ctx_dir = out / "context" / "backfill"
    live_ctx_dir = out / "context" / "live"

    if a.px5y:
        pn = P.build(HERE, px5y=a.px5y)
        log(f"株価の表 {len(pn.dates)}日 × {len(pn.codes)}銘柄  {time.time() - t0:.0f}秒")
        CM = ctx_matrices(pn)
        log(f"文脈の値  {time.time() - t0:.0f}秒")
        evs = [e for e in events.values() if e.get("origin") == "backfill" and e["event_type"] in PRICE_TYPES]
        ctx_bf = compute_contexts(pn, CM, evs)
        by_m = defaultdict(dict)
        for k, v in ctx_bf.items():
            by_m[k[0][:7]][k] = v
        for ym, rows in by_m.items():
            write_ctx_csv(bf_ctx_dir / f"{ym}.csv.gz", rows)
        log(f"遡及の文脈 {len(ctx_bf)}件（{len(by_m)}か月）  {time.time() - t0:.0f}秒")
    else:
        pn = CM = None

    # ── 当日記録の文脈（追記のみ）
    live_ctx = read_ctx(live_ctx_dir) if live_ctx_dir.exists() else {}
    todo = [e for e in events.values() if e.get("origin") == "live" and not e.get("imported_from")
            and e["event_type"] in PRICE_TYPES and (e["date"], e["code"]) not in live_ctx]
    if todo:
        if pn is None:
            pn = P.build(HERE)
            CM = ctx_matrices(pn)
        new = compute_contexts(pn, CM, todo)
        by_m = defaultdict(dict)
        for k, v in new.items():
            by_m[k[0][:7]][k] = v
        for ym, rows in by_m.items():
            p = live_ctx_dir / f"{ym}.csv"
            live_ctx_dir.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("", encoding="utf-8")
            write_ctx_csv(p, rows, mode="a")
        live_ctx.update(new)
        log(f"当日記録の文脈を追記 {len(new)}件")

    ctx = read_ctx(bf_ctx_dir) if bf_ctx_dir.exists() else {}
    ctx.update(live_ctx)
    baseline = Y.load_baseline(data_dir)
    recs = build_ctx_stats(events, scores, ctx, baseline)
    n_hyp = len(HY.HYPOTHESES)
    n_tests = sum(1 for r in recs if r.get("hyp_id"))
    conf = [r for r in recs if r.get("hyp_id") and r.get("status") == "confirmed"]
    log(f"文脈つきの統計 {len(recs)}件（仮説 {n_hyp}個・仮説の帯×窓 {n_tests}件のうち confirmed {len(conf)}）")

    # ── 急騰（A・B）: --px5y のときだけ作り直す
    surge_path = out / "stats" / "surge.json"
    if a.px5y:
        srecs, overall, SM, _ = build_surge(pn, events, ctx)
        rev = build_reverse(pn, events, SM)
        Y.write_json(surge_path, {
            "schema": "yochou_surge/1", "updated": now, "definitions": HY.SURGE_DEFS,
            "episode_gap": HY.SURGE_EPISODE_GAP, "lookback": HY.LOOKBACK,
            "price_period": [pn.dates[0], pn.dates[-1]], "overall_base_rate": overall,
            "method": "生の値動き（分割調整済み・配当は未調整）。基準＝同じ base 日の全銘柄（^ で始まる指数を除く・その日に500銘柄以上）"
                      "の割合。検定は日付を1観測（その日のサイン銘柄の割合−その日の全銘柄の割合）・Bartlett（h=定義の日数）・前半/後半",
            "caution": "上場廃止・統合で今いない銘柄は入らない（生き残りの偏り。急落の割合は低めに出る）。株価は取得日時点で Yahoo が見せる値。",
            "records": srecs, "reverse": rev})
        log(f"急騰の統計 {len(srecs)}件  {time.time() - t0:.0f}秒")
    surge = Y.L.jload(surge_path, {}) or {}
    s_idx = {(r["event_type"], r["band"]): r for r in surge.get("records") or []}

    # ── 統計の全量（data/）
    Y.write_json(out / "stats" / "context.json", {
        "schema": "yochou_context/1", "updated": now,
        "hypotheses": [{"id": h, "event_type": t, "context": f"{cn}:{b}", "dir": d, "text": pub_text(txt)}
                       for h, t, cn, b, d, txt in HY.HYPOTHESES],
        "bands": HY.BANDS, "windows": list(HY.WINDOWS_CTX), "n_hypotheses": n_hyp,
        "n_hyp_tests": n_tests, "n_all_tests": sum(1 for r in recs if r.get("days")),
        "method": "出来事1つ×文脈1つ。値・検定・ステータスは yochou.py と同じ（対TOPIX・全銘柄との差・日付を1観測・前半/後半）。"
                  "文脈は base_date より前の最後の営業日の終値までの値",
        "records": recs})

    # ── 公開: stats.json に文脈つきの種類と急騰の欄を足す（既存の行は変えない）
    sp = docs_out / "stats.json"
    st = Y.L.jload(sp, None)
    if st:
        keep = ("band", "window", "n", "days", "median", "mean", "pos_rate", "p_value", "status", "period", "day_mean",
                "status_topix", "vs_all_day_mean", "vs_all_p", "vs_all_status", "median_vs_all", "pos_rate_vs_all",
                "base_median", "signal_ok", "side", "hypothesis_dir", "hypothesis_match", "live_n", "backfill_n")
        types = [x for x in st.get("types") or [] if not x.get("context")]
        for x in types:
            sf = surge_fields(s_idx.get((x["type"], "all")))
            if sf:
                x.update(sf)
        by = defaultdict(list)
        for r in recs:
            if r.get("days"):
                by[(r["event_type"], r["context"])].append(r)
        for (t, cx), rows in sorted(by.items()):
            r0 = rows[0]
            item = {"type": t, "label": Y.label_of(t), "context": cx, "context_label": r0.get("context_label"),
                    "metric": "対TOPIXの超過", "hypothesis": r0.get("hypothesis"), "hyp_id": r0.get("hyp_id"),
                    "rows": [{k: r.get(k) for k in keep} for r in rows]}
            sf = surge_fields(s_idx.get((t, cx)))
            if sf:
                item.update(sf)
            types.append(item)
        st["types"] = types
        st["context_rule"] = ("文脈つき（context の欄）は 出来事1つ×チャートの文脈1つ。仮説は集計の前に書いた"
                              f"{n_hyp}個（yochou_context_hyp.py）。判定の規則は文脈なしと同じ")
        st["surge_rule"] = ("surge20=20営業日以内に終値が基準価格（知り得たあとの最初の始値）の+30%以上・surge60x2=60営業日以内に2倍以上・"
                            "drop20=20営業日以内に−30%以下。生の値動き。rate はサインの割合（%）、base_rate は同じ日の全銘柄の割合（%）、"
                            "lift はその比。割合が小さいことに注意（急騰と急落の倍率を並べて見る）")
        Y.write_json(sp, st)

    # ── 公開: 逆引き
    if surge.get("reverse"):
        rv = surge["reverse"]
        Y.write_json(docs_out / "surge_before.json", {
            "v": 1, "updated": surge.get("updated"), "definitions": {k: HY.SURGE_DEFS[k]["label"] for k in rv},
            "lookback": f"急騰の始まりの日を含む前{HY.LOOKBACK}営業日",
            "episode_rule": "同じ銘柄で、急騰の条件が成り立つ日が前の成り立つ日から20営業日（2倍は60営業日）以内に続くものは1回にまとめる",
            "caution": "急騰した銘柄を後から見た数字。サインが出た銘柄の大半は急騰していない（stats.json の surge20 の rate を参照）。"
                       "上場廃止・統合で今いない銘柄は含まない。",
            **{k: {"n_surges": v["n_surges"], "period": v["period"],
                   "rows": [{kk: r[kk] for kk in ("event_type", "label", "n_surges", "from", "had_before", "rate", "base_rate", "lift")}
                            for r in v["rows"]]} for k, v in rv.items()}})

    # ── 公開: today.json（全 signal に side、急騰の欄、文脈つきの予兆）
    tp = docs_out / "today.json"
    td = Y.L.jload(tp, None)
    if td:
        day = a.date or td.get("date")
        base_sigs = [s for s in td.get("signals") or [] if not s.get("context")]
        for s in base_sigs:
            if "side" not in s:
                s["side"] = "up" if (s.get("median") or 0) > 0 else "down"
            sf = surge_fields(s_idx.get((s["type"], "all")))
            if sf:
                s["surge"] = sf
        ridx = {(r["event_type"], r["context"], r["window"]): r for r in recs if r.get("days")}
        cands = []
        for e in events.values():
            if e["date"] != day or e.get("origin") != "live" or e.get("imported_from") or e["code"] == "MKT":
                continue
            cx = ctx.get((e["date"], e["code"]))
            if not cx:
                continue
            for cn in HY.BANDS:
                b = band_of(cx, cn)
                rec = ridx.get((e["event_type"], f"{cn}:{b}", WINDOW)) if b else None
                # 出すのは先に書いた仮説の帯だけ（比べるための帯は統計に残すが予兆には出さない）
                if not rec or not rec.get("hyp_id") or not rec.get("signal_ok") or rec["days"] < SIGNAL_MIN_DAYS:
                    continue
                text = ctx_text(e, rec)
                if Y.banned_in(text):
                    log(f"::warning::禁止語を含むので出さない: {text}")
                    continue
                x = {"event_id": e["event_id"], "code": e["code"], "name": "", "type": e["event_type"],
                     "label": Y.label_of(e["event_type"]), "context": rec["context"], "context_label": rec["context_label"],
                     "text": text, "n": rec["n"], "days": rec["days"], "window": WINDOW,
                     "median": round(rec["median"], 1), "pos_rate": round(rec["pos_rate"]),
                     "period": f"{rec['period'][0][:7]}〜{rec['period'][1][:7]}", "status": rec["status"],
                     "status_vs_all": rec.get("vs_all_status"),
                     "median_vs_all": round(rec["median_vs_all"], 1) if rec.get("median_vs_all") is not None else None,
                     "basis": "backfill" if rec.get("live_n", 0) == 0 else "live+backfill", "live_n": rec.get("live_n", 0),
                     "side": rec["side"], "hyp_id": rec.get("hyp_id")}
                sf = surge_fields(s_idx.get((e["event_type"], rec["context"])))
                if sf:
                    x["surge"] = sf
                cands.append(x)
        nm = Y.names(a.docs)
        for x in cands:
            x["name"] = nm.get(x["code"], "")
        # 並び: yochou.py と同じ（種類×文脈を発生日数の多い順に、各組から1件ずつ・同じ組の中はコード順）
        by_t = defaultdict(list)
        for x in sorted(cands, key=lambda s: s["code"]):
            by_t[(x["type"], x["context"])].append(x)
        order = sorted(by_t, key=lambda k: (-by_t[k][0]["days"], k))
        picked = []
        while len(picked) < MAX_CTX_SIGNALS and any(by_t[k] for k in order):
            for k in order:
                if by_t[k] and len(picked) < MAX_CTX_SIGNALS:
                    picked.append(by_t[k].pop(0))
        td["signals"] = base_sigs + picked
        td["n_matched_context"] = len(cands)
        td["context_rule"] = (f"文脈つき（context の欄）: その日の出来事のチャートの文脈（{', '.join(HY.BANDS)} の帯）ごとの"
                              f"{WINDOW}営業日の統計（集計の前に書いた仮説の帯だけ）が、対TOPIX と全銘柄との差の両方で confirmed・同じ向き・発生日数{SIGNAL_MIN_DAYS}日以上。"
                              f"上限{MAX_CTX_SIGNALS}件。side は up=その後強かった側・down=弱かった側")
        Y.write_json(tp, td)
        # 出した文脈つきの予兆を残す（追記のみ・(event_id, context) で重複しない）
        sd = out / "signals_context"
        sd.mkdir(parents=True, exist_ok=True)
        seen = set()
        for f in sd.glob("*.jsonl"):
            for r in Y.L.jsonl(f):
                seen.add((r["event_id"], r["context"]))
        newr = [x for x in picked if (x["event_id"], x["context"]) not in seen]
        if newr:
            with open(sd / f"{day[:7]}.jsonl", "a", encoding="utf-8", newline="\n") as f:
                for x in newr:
                    f.write(json.dumps({"date": day, "published_at": now, **x}, ensure_ascii=False, separators=(",", ":")) + "\n")
        log(f"today.json（{day}）: 文脈つきの候補 {len(cands)}件 → {len(picked)}件を足した（新しく記録 {len(newr)}件）")
    log(f"おわり  {time.time() - t0:.0f}秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
