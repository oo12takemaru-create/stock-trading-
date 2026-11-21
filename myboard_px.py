# -*- coding: utf-8 -*-
"""マイボード用: 銘柄ごとの約1年の終値の並び（2026-11-18・相場ボード・マイボード 設計書 v1 段階3）

■ 何のため
  kaburadar.jp/hold.html（マイボード）が、利用者の入れた最大30銘柄の小さな日足チャートを
  GitHub Pages の静的ファイルだけで速く描くため（Vercel の関数は使わない）。

■ 取得は増やさない
  movers_daily.py が同じジョブ内で書く .cache/close_1y.pkl（全銘柄×1年の終値）を読むだけ。

■ 出力（docs/ の下 → GitHub Pages）
  docs/px1y/{コード}.json   1銘柄1ファイル（本体）
      {"code","n" 銘柄名,"d0" 先頭の日付,"x" 日数の差（先頭0）,"c" 終値（分割調整済み）,
       "d" 最後の日付,"p" 終値,"chg" 前日比,"pct" 騰落率%,"h3","l3" 3か月の終値の高値・安値,"h1","l1" 1年の高値・安値}
      x/c の形は chartcard.js（KRCard）と同じ。
  docs/px1y/recent.json     全銘柄共通の「本体より後の終値」1ファイル（毎日書き換える）
      {"updated","trade_date","full" 本体を全部書き換えた取引日,"dates":[本体より後の取引日],
       "items":{コード:[dates に並ぶ終値（無い日は null）]}}
      画面は 本体＋recent を日付でつないで、終値・前日比・高安を計算し直す。

■ git の履歴が膨らまない工夫
  本体（約3,700ファイル）を毎日書き換えると、1日あたり数MB ずつ履歴が増える。そこで
  - 本体は「週の最初の取引日」にだけ全部書き換える（ふつうは月曜）。
  - 平日の新しい足は recent.json（1ファイル）に重ねる。週の中で recent は最大4〜5日分。
  - 例外として、その週の途中でも本体を書き換える銘柄: 本体が無い（新規上場）・
    過去の終値が変わった（株式分割の調整など。本体の終値と今日取った終値が 0.5% 超ずれた日がある）。

■ 守ること
  - 取引日は最終行の日付の最頻値（movers_daily.py と同じ決め方）。前回より古い取引日では書かない。
  - 大引け前（15:45 JST より前）に走ったら書かない（途中の足を終値として出さない）。
  - 当日の足がある銘柄が半分未満なら障害とみなして書かない。
  - 失敗しても prices / movers の公開は止めない（ワークフローで || echo）。
  - 桁がおかしい並び（株式併合を分割として取り込んだ・再上場前の値が残った等）は px_sanity.py で検査し、
    作り直せるものは作り直し、無理なものは書かない（前に書いた本体も消す）。画面は「チャートなし」に落ちる。
    作り直しに要る分割の記録は docs/splits.json（TDnet・JPX）と、検査で引っかかった銘柄だけ yfinance から取る
    （ふつうは0〜数銘柄）。

使い方:
  python -X utf8 myboard_px.py                                   # ジョブの中（movers_daily.py の後）
  python -X utf8 myboard_px.py --cache <pkl> --out <dir>         # 試験用（本番の場所に書かない）
"""
import argparse
import json
import math
import pickle
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

import px_sanity

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "px1y"
CACHE = ROOT / ".cache" / "close_1y.pkl"
PRICES = ROOT / "docs" / "prices.json"
SPLITS = ROOT / "docs" / "splits.json"
MAX_SPLIT_FETCH = 30     # 分割の記録を取りに行く銘柄の上限（それより多いのは障害とみなし取らない）
CLOSE_JST = dtime(15, 45)
KEEP_DAYS = 366          # 本体に入れる期間（暦日）
H3_DAYS = 92             # 3か月（chartcard.js の slice と同じ）
SPLIT_TOL = 0.005        # 本体と今日の終値のずれ（0.5% 超で本体を書き直す）
CODE_OK = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def log(*a):
    print(*a, flush=True)


def rnd(v):
    """終値を短く（3391.0 → 3391 / 401.9 → 401.9）。株価は小数1桁で足りる（呼値の最小は0.1円）"""
    v = round(float(v), 1)
    return int(v) if v == int(v) else v


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def series_of(s, trade_date):
    """pandas の Series → [(date, close)]（NaN・0以下を除く・取引日より後を除く・1年分）"""
    out = []
    for ix, v in s.items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if not (f > 0) or math.isinf(f):
            continue
        d = ix.date() if hasattr(ix, "date") else ix
        if d > trade_date:
            continue
        out.append((d, rnd(f)))
    if not out:
        return out
    cut = out[-1][0] - timedelta(days=KEEP_DAYS)
    return [x for x in out if x[0] > cut]


def stats(pts):
    """[(date, close)] → 終値・前日比・騰落率・3か月/1年の高安"""
    d_last, p = pts[-1]
    prev = pts[-2][1] if len(pts) >= 2 else None
    cut3 = d_last - timedelta(days=H3_DAYS)
    c3 = [c for d, c in pts if d >= cut3]
    c1 = [c for _, c in pts]
    o = {"d": d_last.isoformat(), "p": p,
         "chg": rnd(p - prev) if prev else None,
         "pct": round((p / prev - 1) * 100, 2) if prev else None,
         "h3": max(c3), "l3": min(c3), "h1": max(c1), "l1": min(c1)}
    return o


def body_of(code, name, pts):
    x, last = [], None
    for d, _ in pts:
        x.append(0 if last is None else (d - last).days)
        last = d
    o = {"code": code, "n": name, "d0": pts[0][0].isoformat(), "x": x, "c": [c for _, c in pts]}
    o.update(stats(pts))
    return o


def body_changed(old, pts):
    """本体（old）の終値と今日取った終値が、同じ日付で 0.5% 超ずれていれば True（分割の調整など）"""
    try:
        d = date.fromisoformat(old["d0"])
        mine = {}
        for dx, c in zip(old["x"], old["c"]):
            d = d + timedelta(days=dx)
            mine[d] = c
    except Exception:
        return True
    hit = 0
    for d, c in pts:
        o = mine.get(d)
        if o is None:
            continue
        hit += 1
        if o <= 0 or abs(c / o - 1) > SPLIT_TOL:
            return True
    return hit == 0


def fetch_splits(ticker):
    """yfinance の分割の記録 {date: 比率}。取れなければ None"""
    try:
        import yfinance as yf
        s = yf.Ticker(ticker).splits
        return {(ix.date() if hasattr(ix, "date") else ix): float(v) for ix, v in s.items() if float(v) > 0}
    except Exception as e:
        log(f"  {ticker} の分割の記録が取れない: {e}")
        return None


def local_splits(path):
    """docs/splits.json の by_code → {コード: {権利落ち日: 比率}}（実施済みのものだけ）"""
    o = load_json(path) or {}
    out = {}
    for code, lst in (o.get("by_code") or {}).items():
        for x in lst or []:
            if x.get("ex") and x.get("ratio") and x.get("status") != "予定":
                out.setdefault(str(code).upper(), {})[x["ex"]] = x["ratio"]
    return out


def ref_prices(path, trade_date):
    """prices.json の同じ取引日の終値（古い値＝stale は使わない）"""
    o = load_json(path) or {}
    if o.get("trade_date") != trade_date.isoformat():
        return {}
    return {c: v.get("p") for c, v in (o.get("items") or {}).items()
            if isinstance(v, dict) and v.get("p") and not v.get("stale")}


def sanitize(rows, refs, get_splits, known=None):
    """rows [(code, name, ticker, pts)] → (使う rows [(code, name, pts)], 使わないコードの一覧, 報告)
    get_splits(ticker) → {date: 比率} or None、known: {コード: {日付: 比率}}（splits.json）"""
    flagged = []
    for i, (code, name, t, pts) in enumerate(rows):
        ref = refs.get(code)
        ref = ref if (ref and pts[-1][0] == refs.get("_td")) else None
        if px_sanity.problems(pts, ref):
            flagged.append(i)
    fetch = len(flagged) <= MAX_SPLIT_FETCH
    if not fetch:
        log(f"::warning::検査で引っかかった銘柄が {len(flagged)} と多い。分割の記録は取りに行かない")
    out, bad, notes = [], [], []
    fl = set(flagged)
    for i, (code, name, t, pts) in enumerate(rows):
        if i not in fl:
            out.append((code, name, pts))
            continue
        ref = refs.get(code) if pts[-1][0] == refs.get("_td") else None
        sp = dict((known or {}).get(code) or {})
        got = get_splits(t) if fetch else None
        for d, r in (got or {}).items():
            sp[d.isoformat() if hasattr(d, "isoformat") else str(d)] = r
        st, new, pr = px_sanity.check(pts, sp, ref)
        notes.append(f"{code} {st}: {pr[0][0]} {pr[0][1]} {pr[0][2]}（ほか {len(pr) - 1}）")
        if st == "bad":
            bad.append(code)
            continue
        new = [(d, rnd(c)) for d, c in new]
        out.append((code, name, new))
    return out, bad, notes


def week_key(d):
    y, w, _ = d.isocalendar()
    return (y, w)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(CACHE))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--full", action="store_true", help="本体を全部書き直す（試験・手動）")
    ap.add_argument("--prices", default=str(PRICES), help="参考の終値（prices.json）")
    ap.add_argument("--splits", default=str(SPLITS), help="分割の記録（splits.json）")
    ap.add_argument("--no-fetch", action="store_true", help="分割の記録を取りに行かない（試験用）")
    a = ap.parse_args()
    out = Path(a.out)

    try:
        with open(a.cache, "rb") as f:
            cache = pickle.load(f)
    except Exception as e:
        log(f"::warning::1年分の終値のキャッシュが読めない（今日は更新しない）: {e}")
        return 0
    uni, close = cache.get("uni") or [], cache.get("close") or {}
    if not close:
        log("::warning::キャッシュに終値が無い。書かずに終了")
        return 0

    last = Counter()
    for s in close.values():
        if len(s):
            ix = s.index[-1]
            last[ix.date() if hasattr(ix, "date") else ix] += 1
    trade_date = last.most_common(1)[0][0]
    now = datetime.now(JST)
    log(f"取引日: {trade_date}（キャッシュ {cache.get('saved', '?')}）")
    if trade_date > now.date():
        log(f"::warning::取引日 {trade_date} が今日より先。書かずに終了")
        return 0
    if trade_date == now.date() and now.time() < CLOSE_JST:
        log(f"大引け前（{now:%H:%M} JST）なので当日の足は途中。書かずに終了")
        return 0

    rp = out / "recent.json"
    old_recent = load_json(rp) or {}
    old_td = old_recent.get("trade_date") or ""
    if old_td and trade_date.isoformat() < old_td:
        log(f"::warning::取引日 {trade_date} が前回（{old_td}）より古い。書かずに終了")
        return 0
    full_prev = old_recent.get("full") or ""
    full = a.full or not full_prev or week_key(date.fromisoformat(full_prev)) != week_key(trade_date) \
        or not out.exists()

    # 銘柄ごとの並び（ユニバースの順・同じコードは最初の1つ）
    rows, seen, fresh = [], set(), 0
    for u in uni:
        t, code = u.get("t"), str(u.get("c") or "").upper()
        if not t or not code or code in seen or len(code) != 4 or not set(code) <= CODE_OK:
            continue
        seen.add(code)
        s = close.get(t)
        if s is None:
            continue
        pts = series_of(s, trade_date)
        if len(pts) < 2:
            continue
        if pts[-1][0] == trade_date:
            fresh += 1
        name = unicodedata.normalize("NFKC", str(u.get("n") or "")).strip()
        rows.append((code, name, t, pts))
    if fresh < len(seen) * 0.5:
        log(f"::warning::当日の足がある銘柄が半分に届かない（{fresh}/{len(seen)}）。取得の障害とみなし書かない")
        return 0

    # 桁の検査（作り直す・書かない）
    refs = ref_prices(a.prices, trade_date)
    refs["_td"] = trade_date
    rows, bad, notes = sanitize(rows, refs, (lambda t: None) if a.no_fetch else fetch_splits,
                                local_splits(a.splits))
    for x in notes:
        log("  検査: " + x)
    out.mkdir(parents=True, exist_ok=True)
    for code in bad:
        bp = out / f"{code}.json"
        if bp.exists():
            bp.unlink()

    # 本体を書く（全部 or 必要な銘柄だけ）／recent を作る
    n_new = n_split = n_full = 0
    base_end = {}                     # コード → 本体の最後の日付
    for code, name, pts in rows:
        bp = out / f"{code}.json"
        old = None if full else load_json(bp)
        why = None
        if full:
            why = "full"
        elif old is None:
            why = "new"
        elif body_changed(old, pts):
            why = "split"
        if why:
            write_json(bp, body_of(code, name, pts))
            base_end[code] = pts[-1][0]
            n_full += why == "full"
            n_new += why == "new"
            n_split += why == "split"
        else:
            try:
                base_end[code] = date.fromisoformat(old["d"])
            except Exception:
                write_json(bp, body_of(code, name, pts))
                base_end[code] = pts[-1][0]
                n_new += 1

    # recent: 本体より後の終値（全銘柄共通の日付の並びに揃える）
    dates = sorted({d for code, _, pts in rows for d, _ in pts if d > base_end[code]})
    di = {d: k for k, d in enumerate(dates)}
    items = {}
    for code, _, pts in rows:
        tail = [(d, c) for d, c in pts if d > base_end[code]]
        if not tail:
            continue
        arr = [None] * len(dates)
        for d, c in tail:
            arr[di[d]] = c
        items[code] = arr
    rec = {"updated": now.isoformat(timespec="seconds"), "trade_date": trade_date.isoformat(),
           "full": trade_date.isoformat() if full else full_prev,
           "count": len(rows), "dates": [d.isoformat() for d in dates], "items": items}
    write_json(rp, rec)

    # 上場廃止など: 全部書き直した日に、今日の一覧に無く最後の日付が30日より古い本体を消す
    gone = 0
    if full:
        have = {code for code, _, _ in rows}
        for p in out.glob("*.json"):
            if p.stem == "recent" or p.stem in have:
                continue
            o = load_json(p) or {}
            try:
                if date.fromisoformat(o.get("d") or "1970-01-01") < trade_date - timedelta(days=30):
                    p.unlink()
                    gone += 1
            except Exception:
                pass

    sz = rp.stat().st_size
    log(f"マイボードの終値: 銘柄 {len(rows)}（当日の足 {fresh}）"
        f"  本体の書き換え 全部 {n_full}・新規 {n_new}・分割など {n_split}・削除 {gone}"
        f"  recent {len(dates)}日分・{len(items)}銘柄・{sz / 1024:.0f}KB"
        f"  桁の検査 引っかかり {len(notes)}・書かない {len(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
