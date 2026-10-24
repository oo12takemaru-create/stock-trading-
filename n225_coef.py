# -*- coding: utf-8 -*-
"""日経平均の寄与度とウエート（株レーダーが自分で割り戻した係数）→ data/n225_coef.json

■ 本人決定（2026-10-24）: 寄与度とウエートは自己解釈で作る。守る線
  1. 日経の配布物（月末ウエート CSV）の**原本は保存しない・配らない**。取得したらメモリの上で係数を割り戻し、
     そのまま捨てる（ファイルにも JSON にも書かない。ウエートの値そのものも持たない）
  2. 表示するのは、ここで割り戻した係数から毎日計算した寄与度とウエートだけ
  3. 検算（Σ寄与度 と 日経平均の前日比）の差が ±3円を超えた回は表示を止める。推測で埋めない

■ 割り戻し（月に1回・月末ウエートが新しくなったとき）
  入力: 月末時点の構成銘柄ウエート w_i（%）・同じ日の各銘柄の終値 P_i（yfinance）・同じ日の日経平均の終値
  係数の比 c_i ∝ w_i ÷ P_i。比を「係数1の銘柄」（いちばん多い）で割って、0.1 刻みの値に丸める。
  （日経平均の株価換算係数は 0.1 刻み。分割で 0.1×N になるので 2.4・7.2・22.5 なども出る）
  全体を何倍しても寄与度とウエートは変わらない（除数も同じ倍率になる）ので、「1」を何に合わせるかは結果に効かない。
  除数 D ＝ Σ(P_i × c_i) ÷ 日経平均（月末の終値）

■ 毎日（heatmap_fetch.py の apply から）
  寄与度（円）＝（当日の株価 − 前日終値）× c_i ÷ D、ウエート（%）＝ P_i × c_i ÷ Σ(P_j × c_j)
  Σ寄与度 と 日経平均（^N225）の前日比の差が ±3円を超えたら、その回は寄与度もウエートも出さない（印だけ）

■ 月の途中の変化
  - 株式分割: docs/splits.json の権利落ち日で係数に比率を掛ける（yfinance は過去の株価を分割後の値に直すので、
    係数も同じ「いまの株数の基準」にそろえる。basis＝その基準の日）
  - 構成銘柄の入替・臨時の係数変更: 検算が2営業日以上合わなくなったら、最初に合わなくなった日を境に
    「係数が分からない銘柄（新しく入った銘柄）」と、合わない分をいちばんよく説明する銘柄（最大3つ）の係数だけを
    その日からの終値と日経平均の終値で割り戻す。0.1 刻みに乗り、その日以降の**全営業日**で検算が ±3円に収まったときだけ使う
    （収まらなければ使わない＝表示は止まったまま。次の月末ウエートで割り戻し直す）。3営業日たまるまでは試さない

使い方: python -X utf8 n225_coef.py            # heatmap.yml から（毎回）
        python -X utf8 n225_coef.py --force    # 月末ウエートを今すぐ取り直す
"""
import csv
import io
import json
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "n225_coef.json"
N225_CSV = HERE / "data" / "n225.csv"
SPLITS = HERE / "docs" / "splits.json"
WEIGHT_URL = "https://indexes.nikkei.co.jp/nkave/archives/file/nikkei_stock_average_weight_jp.csv"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar/1.0; +https://kaburadar.jp)"}
TOL = 3.0             # 検算の許容（円）
GRID = 0.1            # 株価換算係数の刻み
GRID_TOL = 0.005      # 丸めの残差の許容（相対）。ウエートは小数4桁（%）なので、ウエートの小さい銘柄はその分を足す
MAX_OFFGRID = 3       # 既定値に乗らない銘柄がこれより多い月は割り戻しを使わない
MAX_EXTRA = 3         # 入替・臨時変更のとき、新しく入った銘柄のほかに割り戻し直す銘柄の数の上限
MIN_REFIT_DAYS = 3
KEEP_CHECKS = 60


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def today_jst():
    return datetime.now(JST).date()


# ─────────────────────────── 状態
def load_state(path=OUT):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st, path=OUT):
    st["updated"] = datetime.now(JST).isoformat(timespec="seconds")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(st, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")


def members():
    with open(N225_CSV, encoding="utf-8") as f:
        return [r["code"] for r in csv.DictReader(f)]


def load_splits():
    """{code: [(ex, ratio), ...]}（分割の権利落ち日と比率。中止は splits.json の by_code に入っていない）"""
    try:
        d = json.loads(SPLITS.read_text(encoding="utf-8"))
    except Exception as e:
        log(f"splits.json を読めない（分割の調整なしで続ける）: {e}")
        return {}
    out = {}
    for c, evs in (d.get("by_code") or {}).items():
        for e in evs:
            ex, r = e.get("ex"), e.get("ratio")
            if ex and r and r > 0:
                out.setdefault(c, []).append((ex, float(r)))
    return out


def split_factor(splits, code, after, upto):
    """権利落ち日が (after, upto] にある分割の比率の積"""
    f = 1.0
    for ex, r in splits.get(code, []):
        if after < ex <= upto:
            f *= r
    return f


def rebase(st, splits, upto):
    """係数を upto の株数の基準にそろえる（分割の権利落ち日を過ぎた銘柄は比率を掛ける）"""
    b = st.get("basis")
    if not b or upto <= b:
        return []
    done = []
    for seg in st.get("segs", []):
        for c in list(seg["coef"]):
            f = split_factor(splits, c, b, upto)
            if f != 1.0:
                seg["coef"][c] = round(seg["coef"][c] * f, 6)
                done.append(f"{c}×{f:g}")
    st["basis"] = upto
    if done:
        st.setdefault("split_log", []).append({"basis": upto, "adj": sorted(set(done))})
        st["split_log"] = st["split_log"][-20:]
    return sorted(set(done))


def coef_now(st, splits, upto):
    """いま（upto）の基準の係数と除数。保存はしない（heatmap_fetch の apply 用）"""
    seg = (st.get("segs") or [None])[-1]
    if not seg:
        return None, None
    b = st.get("basis") or upto
    return {c: v * split_factor(splits, c, b, upto) for c, v in seg["coef"].items()}, seg["D"]


# ─────────────────────────── 月末ウエート（原本は保存しない）
def fetch_weights():
    """(asof 'YYYY-MM-DD', {code: w%})。CSV はメモリの上で読むだけ。ファイルには書かない"""
    raw = urllib.request.urlopen(urllib.request.Request(WEIGHT_URL, headers=UA), timeout=60).read()
    txt = None
    for enc in ("cp932", "utf-8-sig"):
        try:
            txt = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    del raw
    if txt is None:
        raise ValueError("文字コードが読めない")
    w, asof = {}, set()
    for r in csv.reader(io.StringIO(txt)):
        if len(r) >= 6 and r[1].strip()[:1].isdigit() and r[5].strip().endswith("%"):
            asof.add(r[0].strip().replace("/", "-"))
            w[r[1].strip()] = float(r[5].strip().rstrip("%"))
    del txt
    if len(asof) != 1 or not 220 <= len(w) <= 230:
        raise ValueError(f"様式が変わった可能性（日付{len(asof)}種・{len(w)}銘柄）")
    return asof.pop(), w


def round_grid(x):
    return round(round(x / GRID) * GRID, 6)


def derive(w, px_asof, n225_asof):
    """月末ウエートと同じ日の終値 → 係数・除数・丸めの残差。w と px_asof は {code: 値}"""
    ratio = {c: w[c] / px_asof[c] for c in w if px_asof.get(c)}
    vals = sorted(ratio.values())
    med = vals[len(vals) // 2]
    ones = [v for v in vals if abs(v / med - 1) < 0.02]
    k = len(ones) / sum(ones)
    coef, worst, off = {}, [], []
    for c, v in ratio.items():
        x = v * k
        g = round_grid(x)
        if g <= 0:
            off.append(c)
            continue
        res = abs(x / g - 1)
        tol = GRID_TOL + 0.00005 / max(w[c], 1e-6) * 1.5      # ウエートの4桁目の丸めの分を足す
        coef[c] = g
        worst.append((round(res, 5), c, round(x, 4), g))
        if res > tol:
            off.append(c)
    worst.sort(reverse=True)
    D = sum(px_asof[c] * coef[c] for c in coef) / n225_asof
    return coef, D, {"resid_max": worst[0][0] if worst else None, "resid_top": [list(x) for x in worst[:5]],
                     "offgrid": sorted(off), "missing_px": sorted(set(w) - set(ratio))}


# ─────────────────────────── 株価
def yf_closes(codes, start):
    """{code: {date: close}}（^N225 は 'N225'）。auto_adjust=False の Close（分割は調整済み・配当は調整しない）"""
    import yfinance as yf
    tk = sorted({c + ".T" for c in codes}) + ["^N225"]
    df = yf.download(tk, start=start, progress=False, auto_adjust=False, group_by="ticker", threads=True)
    out = {}
    for t in tk:
        try:
            s = df[t]["Close"].dropna()
        except Exception:
            continue
        out["N225" if t == "^N225" else t[:-2]] = {str(i.date()): float(v) for i, v in s.items()}
    return out


def closed_days(px, now=None):
    """終値が確定した営業日（日経平均の日足のある日。当日は 15:35 を過ぎてから）"""
    now = now or datetime.now(JST)
    days = sorted(px.get("N225", {}))
    if days and days[-1] == now.date().isoformat() and (now.hour, now.minute) < (15, 35):
        days = days[:-1]
    return days


# ─────────────────────────── 検算
def seg_at(st, t):
    segs = st.get("segs") or []
    cur = None
    for s in segs:
        if s["from"] <= t:
            cur = s
    return cur


def check_day(seg, px, t, p):
    """(差, Σ寄与度, 前日比)。価格の欠けがあれば None"""
    tot = 0.0
    for c, k in seg["coef"].items():
        a, b = px.get(c, {}).get(t), px.get(c, {}).get(p)
        if a is None or b is None:
            return None
        tot += (a - b) * k
    s = tot / seg["D"]
    chg = px["N225"][t] - px["N225"][p]
    return s - chg, s, chg


def run_checks(st, px, days):
    """基準日の翌営業日から、各営業日の検算。[{d, chg, sum, diff, ok}]"""
    asof = st["base"]["asof"]
    out = []
    for i, t in enumerate(days):
        if t <= asof or i == 0:
            continue
        p = days[i - 1]
        seg = seg_at(st, t)
        # 入替の日は「前日の終値」も新しい係数で数える（除数はその前日の終値で続くように決めてある）
        r = check_day(seg, px, t, p)
        if r is None:
            out.append({"d": t, "ok": False, "why": "株価の欠け"})
            continue
        diff, s, chg = r
        out.append({"d": t, "chg": round(chg, 2), "sum": round(s, 2), "diff": round(diff, 2), "ok": abs(diff) <= TOL})
    return out


def refit(seg, px, days, f_idx, mem, skip):
    """入替・臨時変更の割り戻し。days[f_idx] から最後の日までの終値で、分からない係数だけを解く。
    返す: (新しい seg, 説明) / (None, 理由)"""
    import numpy as np
    p = days[f_idx - 1]
    span = days[f_idx:]
    new = [c for c in mem if c not in seg["coef"]]
    base = {c: v for c, v in seg["coef"].items() if c in mem}
    N = px["N225"]
    if any(px.get(c, {}).get(t) is None for c in mem for t in [p] + span):
        return None, "株価の欠けがあり割り戻せない"

    def solve(free):
        fixed = [c for c in mem if c not in free]
        A = [[px[c][t] - px[c][p] * N[t] / N[p] for c in free] for t in span]
        b = [sum((px[c][p] * N[t] / N[p] - px[c][t]) * base[c] for c in fixed) for t in span]
        A, b = np.array(A, dtype=float), np.array(b, dtype=float)
        if len(span) < len(free) + 2:
            return None, None
        x = np.linalg.lstsq(A, b, rcond=None)[0] if free else np.array([])
        return x, float(np.sqrt(np.mean((A @ x - b) ** 2))) if free else float(np.sqrt(np.mean(b ** 2)))

    def verify(free, x):
        co = dict(base)
        for c, v in zip(free, x):
            g = round_grid(v)
            if g <= 0 or abs(v - g) > max(0.02, 0.01 * g):
                return None
            co[c] = g
        D = sum(px[c][p] * co[c] for c in mem) / N[p]
        sg = {"from": span[0], "D": D, "coef": co}
        prev = p
        for t in span:
            r = check_day(sg, px, t, prev)
            if r is None or abs(r[0]) > TOL:
                return None
            prev = t
        return sg

    free = list(new)
    cands = [c for c in mem if c not in free and c not in skip]
    for _ in range(MAX_EXTRA + 1):
        x, _e = solve(free)
        if x is None:
            return None, f"日数が足りない（{len(span)}営業日・未知{len(free)}）"
        sg = verify(free, x)
        if sg:
            ch = {c: [seg["coef"].get(c), sg["coef"][c]] for c in free}
            return sg, ch
        if len(free) >= len(new) + MAX_EXTRA:
            break
        best = None
        for j in cands:
            xj, e = solve(free + [j])
            if xj is not None and (best is None or e < best[1]):
                best = (j, e)
        if not best:
            break
        free.append(best[0])
        cands.remove(best[0])
    return None, "0.1刻みの係数で検算が ±3円に収まる組が見つからない"


# ─────────────────────────── 毎回の更新
def last_month_end(d):
    return d.replace(day=1) - timedelta(days=1)


def refresh(force=False, now=None):
    now = now or datetime.now(JST)
    today = now.date()
    st = load_state()
    mem = members()
    splits = load_splits()
    base = st.get("base") or {}

    # 1. 月末ウエート（前月末の分がまだ無いときだけ取りに行く。取れたら係数を割り戻して原本は捨てる）
    want_new = force or not base or base.get("asof", "") < (last_month_end(today) - timedelta(days=6)).isoformat()
    if want_new:
        try:
            asof, w = fetch_weights()
            if force or asof > base.get("asof", ""):
                px = yf_closes(set(w) | set(mem), (date.fromisoformat(asof) - timedelta(days=45)).isoformat())
                n = px.get("N225", {}).get(asof)
                pa = {c: px.get(c, {}).get(asof) for c in w}
                if n is None:
                    raise ValueError(f"{asof} の日経平均の終値が取れない")
                coef, D, stats = derive(w, pa, n)
                del w, pa
                if len(stats["offgrid"]) > MAX_OFFGRID or stats["missing_px"]:
                    log(f"::warning::割り戻しが既定値に乗らない（{stats}）。前の係数のまま")
                    st.setdefault("rejected", []).append({"asof": asof, "at": now.isoformat(timespec="seconds"), **stats})
                    st["rejected"] = st["rejected"][-6:]
                else:
                    # 同じ月の各営業日で検算（月の途中に入替が無ければ ±0.1円ほどで合う）
                    tmp = {"base": {"asof": "0000"}, "segs": [{"from": "0000", "D": D, "coef": coef}]}
                    days = [t for t in sorted(px["N225"]) if t <= asof]
                    m0 = asof[:7]
                    days_m = [t for t in days if t >= (date.fromisoformat(m0 + "-01") - timedelta(days=7)).isoformat()]
                    chk = [r for r in run_checks(tmp, px, days_m) if r["d"].startswith(m0)]
                    mx = max((abs(r.get("diff", 99)) for r in chk), default=None)
                    st = {"note": ("株レーダーが日経の公表する月末時点の構成銘柄ウエートと各銘柄の終値から割り戻した係数。"
                                   "日経の公式の株価換算係数・除数ではない。ウエートの原本は保存していない。"),
                          "basis": today.isoformat(),
                          "base": {"asof": asof, "made": now.isoformat(timespec="seconds"), "n": len(coef),
                                   "D": round(D, 6), **stats,
                                   "in_month": {"days": len(chk), "max_abs_diff": mx}},
                          "segs": [{"from": asof, "D": D, "coef": coef}],
                          "checks": [], "split_log": []}
                    log(f"月末ウエート {asof}: {len(coef)}銘柄を割り戻し（除数 {D:.4f}・丸めの残差 最大 {stats['resid_max']}・"
                        f"既定値に乗らない {stats['offgrid'] or 'なし'}・同じ月の検算 最大 {mx}円/{len(chk)}日）")
            else:
                log(f"月末ウエートはまだ {asof} のまま（係数は {base.get('asof')} の分）")
        except Exception as e:
            log(f"::warning::月末ウエートを取れなかった（前の係数のまま）: {e}")

    if not st.get("segs"):
        log("係数がまだ無い")
        return 1

    # 2. 分割の調整と、確定した営業日の検算（入替・臨時変更のときは割り戻し直す）
    asof = st["base"]["asof"]
    px = yf_closes(set(mem) | set(st["segs"][-1]["coef"]),
                   (date.fromisoformat(asof) - timedelta(days=10)).isoformat())
    days = closed_days(px, now)
    adj = rebase(st, splits, today.isoformat())
    if adj:
        log(f"分割の調整: {'・'.join(adj)}")
    chk = run_checks(st, px, days)
    st.pop("refit_note", None)
    bad = [r for r in chk if not r["ok"]]
    last_from = st["segs"][-1]["from"]
    after = [r for r in chk if r["d"] >= last_from and (r["d"] > asof)]
    after_bad = [r for r in after if not r["ok"]]
    if after and not after[-1]["ok"] and len(after_bad) >= 2:
        f = after_bad[0]["d"]
        fi = days.index(f)
        skip = {c for c in mem for ex, _ in splits.get(c, []) if days[fi - 1] < ex <= days[-1]}
        sg, info = refit(st["segs"][-1], px, days, fi, mem, skip)
        if sg:
            st["segs"].append(sg)
            st.setdefault("refits", []).append({"from": f, "at": now.isoformat(timespec="seconds"),
                                                "changed": info, "days": len(days) - fi})
            log(f"入替・係数の変更を割り戻した（{f}〜・{info}）")
            chk = run_checks(st, px, days)
            bad = [r for r in chk if not r["ok"]]
        else:
            st["refit_note"] = {"from": f, "why": info, "at": now.isoformat(timespec="seconds")}
            log(f"::warning::{f} から検算が合わない。割り戻せなかった: {info}")
    mset, cset = set(mem), set(st["segs"][-1]["coef"])
    st["members_diff"] = {"no_coef": sorted(mset - cset), "not_member": sorted(cset - mset)}
    st["checks"] = chk[-KEEP_CHECKS:]
    save_state(st)
    lc = chk[-1] if chk else None
    log(f"検算: {len(chk)}営業日・合わない {len(bad)}日" + (f"（直近 {lc['d']} 差 {lc.get('diff')}円）" if lc else ""))
    return 0


# ─────────────────────────── heatmap_fetch.py から（ザラ場中5回＋引け後）
def apply(items, df, now=None):
    """heatmap_225 の items に ct（寄与度・円）と wt（ウエート・%）を足す。返す: 先頭に置く nk の要約。
    検算が ±3円を超えたら items には何も足さない（印だけ返す）"""
    import pandas as pd
    now = now or datetime.now(JST)
    st = load_state()
    if not st.get("segs"):
        return {"ok": False, "why": "係数がまだ無い"}
    try:
        n = df["^N225"]["Close"].dropna()
    except Exception:
        return {"ok": False, "why": "日経平均の値が取れない"}
    if len(n) < 2:
        return {"ok": False, "why": "日経平均の値が取れない"}
    td = n.index[-1].strftime("%Y-%m-%d")
    coef, D = coef_now(st, load_splits(), td)
    seg = st["segs"][-1]
    head = {"base": st["base"]["asof"], "seg": seg["from"], "td": td,
            "n225": round(float(n.iloc[-1]), 2), "chg": round(float(n.iloc[-1] - n.iloc[-2]), 2)}
    if seg["from"] > td:
        return head | {"ok": False, "why": "係数の基準日より前の相場"}
    by = {i["t"]: i for i in items}
    miss = [c for c in by if c not in coef]
    if miss:
        return head | {"ok": False, "why": f"係数の無い銘柄（{'・'.join(miss[:5])}）"}
    ct, lv = {}, {}
    for c in by:
        try:
            s = df[c + ".T"]["Close"].dropna()
        except Exception:
            return head | {"ok": False, "why": f"{c} の株価が無い"}
        if len(s) < 2 or s.index[-1].strftime("%Y-%m-%d") != td:
            return head | {"ok": False, "why": f"{c} の株価の日付が日経平均とずれている"}
        a, b = float(s.iloc[-1]), float(s.iloc[-2])
        ct[c] = (a - b) * coef[c] / D
        lv[c] = a * coef[c]
    tot = sum(ct.values())
    diff = tot - head["chg"]
    head |= {"sum": round(tot, 2), "diff": round(diff, 2)}
    if abs(diff) > TOL:
        return head | {"ok": False, "why": f"検算が合わない（Σ寄与度と日経平均の前日比の差 {diff:+.2f}円）"}
    L = sum(lv.values())
    for c, i in by.items():
        i["ct"] = round(ct[c], 2)
        i["wt"] = round(lv[c] / L * 100, 3)
    return head | {"ok": True}


if __name__ == "__main__":
    sys.exit(refresh(force="--force" in sys.argv))
