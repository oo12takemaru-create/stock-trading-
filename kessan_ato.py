# -*- coding: utf-8 -*-
"""決算発表のあと、過去はどう動いたか（決算またぎ帳・試作）→ docs/kessan_ato/（株レーダー kaburadar.jp・2026-11-29）

■ 何か
  銘柄ごとに「過去の決算発表のあと、株価は毎回どう動いたか」を、外れも込みで全回並べる（予告ではなく過去の記録）。
  銘柄ページ（stocks/{コード}.html）の節と、決算予定のページ（kessan.html）の1列が読む。
  決算の中身（増益・上方修正など）で分けた内訳は出さない（条件で分けた結果は有料の側に残す決まり）。

■ 物差し（既存の検証と同じ「知り得たあとの最初の始値」）
  発表の時刻で起点 a を決める。
    寄りの前（9:00 より前）の発表 → その日（休日ならその次の営業日）の始値
    場中・引け後の発表              → 翌営業日の始値
  p＝発表の前の最後の終値（引け後の発表ならその日の終値、場中・寄り前なら前の営業日の終値）。
  引けの時刻は 2024-11-05 から 15:30（それより前は 15:00）。
    gap（窓）      = a の始値 ÷ p の終値 − 1      … 発表をまたいで持っていた人が受けた分
    d1（翌営業日） = a の終値 ÷ a の始値 − 1      … 発表を知ってから寄りで入った人の、その日の終わりまで
    d5（5営業日後）= a を1本目として5本目の終値 ÷ a の始値 − 1
    c1             = a の終値 ÷ p の終値 − 1      … 発表の前の終値から翌営業日の終値まで（いちばん大きく動いた回を選ぶ物差し）
    x1・x5・xg     = 同じ区間の TOPIX（1306）との差（ポイント）
  1日で 0.4倍未満・2.5倍超の飛び（分割の取り込み漏れなど）が区間にあれば、その値は出さない（ledger2・similar と同じ線）。
  先読みなし: 起点より後の情報は使わない（d5 は5本目の終値が付いてから）。

■ 材料（新しい取得はしない）
  過去の分（2024-06〜）: data/kessan_ato/seed.csv.gz（kessan_ato_seed.py で手元で1回だけ作った「回ごとの騰落率と日付」）。
     発表の日と時刻は J-Quants の決算サマリー（無料プランの範囲・2024-06-03〜2026-09-03）と、収集側の決算速報。
     株価は yfinance の5年の日足（px_sanity 済み）。生の日足は入れていない。
  それより後の回: docs/kessan_flash_hist/（2026-08-26〜）と docs/tdnet_hist/（2026-10-30〜）の「決算短信」の開示（日付・時刻）。
     株価は data/bars_backfill・data/bars（px_sanity・splits.json で分割を調整）。
  次の発表予定: docs/kessan.json（JPX の決算発表予定日）。

■ 出力（docs/ → GitHub Pages）
  docs/kessan_ato/{コード}.json  回ごとの表とまとめ（1件 数KB）。中身が同じなら書かない（決算のあった銘柄だけ書き換わる）
  docs/kessan_ato/index.json     銘柄ごとのまとめ1行（銘柄ページの生成と決算予定のページが読む）
  docs/kessan_ato/all.json       全銘柄のまとめと回ごとの行の束（銘柄ページの生成が1回で読む）
  docs/kessan_ato/upcoming.json  今後2週間に発表する会社の、過去の反応のまとめ（決算予定のページ用・毎日）
  docs/kessan_ato/meta.json      計算した日・決まり・件数
  生の日足（四本値の並び）は入れない。騰落率と日付だけ。ダウンロード用の CSV は作らない。

使い方:
  python -X utf8 kessan_ato.py                    # 週1回（similar-weekly.yml）: 全銘柄
  python -X utf8 kessan_ato.py --upcoming-only    # 毎日（kessan-daily.yml）: upcoming.json だけ
  python -X utf8 kessan_ato.py --out <dir>        # 試験（本番の場所に書かない）
"""
import argparse
import bisect
import csv
import glob
import gzip
import io
import json
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "kessan_ato"
SEED = ROOT / "data" / "kessan_ato" / "seed.csv.gz"

CLOSE_CHANGE = "2024-11-05"     # 東証の取引時間の延長（引け 15:00 → 15:30）
JUMP_LO, JUMP_HI = 0.4, 2.5
TOPIX = "1306"
H = 5                           # 5営業日後
UPCOMING_DAYS = 14              # 今後2週間（暦日）
SEED_COLS = ["code", "date", "time", "q", "fy", "src", "anchor", "gap", "d1", "d5", "c1", "xg", "x1", "x5"]
ROW_COLS = ["発表日", "時刻", "時間帯", "四半期", "決算期", "窓", "翌営業日", "5営業日後", "前の終値→翌営業日の終値",
            "窓の対TOPIX", "翌営業日の対TOPIX", "5営業日後の対TOPIX"]


def log(*a):
    print(*a, flush=True)


# ──────────────────────────────────────── 発表の時刻・四半期
def close_time(d):
    return "15:00" if d < CLOSE_CHANGE else "15:30"


def timing_of(d, t):
    """'pre'（寄りの前）/ 'in'（場中）/ 'after'（引け後）。時刻が無ければ None"""
    if not t:
        return None
    t = t[:5]
    if t < "09:00":
        return "pre"
    if t < close_time(d):
        return "in"
    return "after"


TIMING_JA = {"pre": "寄り前", "in": "場中", "after": "引け後"}

_ZEN = str.maketrans("０１２３４５６７８９", "0123456789")


def q_from_title(title):
    """TDnet の表題 → '1Q'/'2Q'/'3Q'/'本決算'。決算短信でなければ None"""
    if "決算短信" not in title or "訂正" in title:
        return None
    s = title.translate(_ZEN)
    if re.search(r"第\s*1\s*四半期", s):
        return "1Q"
    if re.search(r"第\s*2\s*四半期", s) or "中間" in s:
        return "2Q"
    if re.search(r"第\s*3\s*四半期", s):
        return "3Q"
    if "四半期" in s:
        return None
    return "本決算"


def fy_from_title(title):
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月期", title.translate(_ZEN))
    return f"{m.group(1)}-{int(m.group(2)):02d}" if m else None


def norm_q(q):
    q = (q or "").strip()
    if q in ("1Q", "2Q", "3Q"):
        return q
    if q in ("FY", "本決算", "通期"):
        return "本決算"
    return None


def code4(c):
    c = str(c)
    return c[:4] if len(c) == 5 and c.endswith("0") else c


# ──────────────────────────────────────── 出来事（発表の日・時刻）
def events_from_flash(root):
    out = []
    for p in sorted(glob.glob(str(Path(root) / "docs" / "kessan_flash_hist" / "*.json"))):
        try:
            j = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        for dd in j.get("days") or []:
            for it in dd.get("items") or []:
                q = norm_q(it.get("q"))
                if not q or not it.get("c"):
                    continue
                fy = str(it.get("fy") or "").replace("/", "-")[:7] or None
                out.append({"code": it["c"], "date": dd["d"], "time": (it.get("t") or "")[:5] or None,
                            "q": q, "fy": fy, "src": "flash"})
    return out


def events_from_tdnet(root):
    out = []
    for p in sorted(glob.glob(str(Path(root) / "docs" / "tdnet_hist" / "20*.json"))):
        try:
            j = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        d = j.get("d") or Path(p).stem
        for r in j.get("rows") or []:
            try:
                c, t, title = r[0], r[2], r[3]
            except (IndexError, TypeError):
                continue
            q = q_from_title(title or "")
            if not q:
                continue
            out.append({"code": code4(c), "date": d, "time": (t or "")[:5] or None, "q": q,
                        "fy": fy_from_title(title), "src": "tdnet"})
    return out


def merge_events(*lists):
    """同じ銘柄・同じ日は1回（先に渡したリストを優先。時刻が無ければ後のもので埋める）"""
    ev = {}
    for lst in lists:
        for e in lst:
            k = (e["code"], e["date"])
            if k not in ev:
                ev[k] = dict(e)
            elif not ev[k].get("time") and e.get("time"):
                ev[k]["time"] = e["time"]
    return ev


# ──────────────────────────────────────── 株価の表
class Px:
    """cal: 営業日の並び、O・C: 日付×銘柄（NaN＝足なし）"""

    def __init__(self, cal, codes, O, C):
        self.cal = list(cal)
        self.col = {c: j for j, c in enumerate(codes)}
        self.O, self.C = O, C
        self.T = len(self.cal)

    def idx(self, d, t, timing):
        """(p, a) の行番号。表の外なら None"""
        cal = self.cal
        if timing == "pre":
            a = bisect.bisect_left(cal, d)            # d 以降の最初の営業日
            p = a - 1
        else:
            a = bisect.bisect_right(cal, d)           # d より後の最初の営業日
            if timing == "after" and a - 1 >= 0 and cal[a - 1] == d:
                p = a - 1                             # 引け後: その日の終値
            else:
                p = bisect.bisect_left(cal, d) - 1    # 場中など: d より前の最後の営業日
        if p < 0 or a >= self.T:
            return None
        return p, a


def _ret(x, y):
    if x is None or y is None or not np.isfinite(x) or not np.isfinite(y) or x <= 0 or y <= 0:
        return None
    r = y / x
    if r < JUMP_LO or r > JUMP_HI:
        return None
    return (r - 1) * 100


def _path_ok(C, j, i0, i1):
    """i0..i1 の終値の並びに飛びが無いか（足の無い日は飛ばして隣どうしで見る）"""
    seq = [C[i, j] for i in range(i0, i1 + 1) if np.isfinite(C[i, j]) and C[i, j] > 0]
    for x, y in zip(seq, seq[1:]):
        if not (JUMP_LO <= y / x <= JUMP_HI):
            return False
    return True


def r2(x):
    return None if x is None else round(float(x), 2)


def measure(px, e):
    """1回分の騰落率（%）。起点の足が無い・未来なら None"""
    j = px.col.get(e["code"])
    jt = px.col.get(TOPIX)
    if j is None:
        return None
    tm = timing_of(e["date"], e.get("time"))
    if tm is None:
        tm = "after"            # 時刻が無いときは遅い側に倒す（知り得たあとを必ず守る）
    ix = px.idx(e["date"], e.get("time"), tm)
    if ix is None:
        return None
    p, a = ix
    if a - p > 6:               # 表の穴（長い休場・足の欠け）は測らない
        return None
    O, C = px.O, px.C
    o_a, c_a, c_p = O[a, j], C[a, j], C[p, j]
    d1 = _ret(o_a, c_a)
    if d1 is None:
        return None
    gap = _ret(c_p, o_a)
    c1 = _ret(c_p, c_a)
    d5 = None
    if a + H - 1 < px.T and _path_ok(C, j, a, a + H - 1):
        d5 = _ret(o_a, C[a + H - 1, j])
    if not _path_ok(C, j, p, a):
        gap = c1 = None

    def tx(i0, x0, i1, x1):
        if jt is None:
            return None
        return _ret(x0[i0, jt], x1[i1, jt])

    t_gap = tx(p, C, a, O)
    t_d1 = tx(a, O, a, C)
    t_d5 = tx(a, O, a + H - 1, C) if (d5 is not None) else None
    sub = lambda x, y: None if (x is None or y is None) else x - y  # noqa: E731
    return {"anchor": px.cal[a], "timing": tm, "gap": r2(gap), "d1": r2(d1), "d5": r2(d5), "c1": r2(c1),
            "xg": r2(sub(gap, t_gap)), "x1": r2(sub(d1, t_d1)), "x5": r2(sub(d5, t_d5))}


# ──────────────────────────────────────── 収集側の日足（直近1年）
def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def load_bars_px(root):
    root = Path(root)
    live, bf = {}, {}

    def rd(p, rows):
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[(r["date"], r["code"])] = (_f(r.get("open")), _f(r.get("close")))

    for p in sorted((root / "data" / "bars").glob("*/*.csv.gz")):
        rd(p, live)
    for p in sorted((root / "data" / "bars_backfill").glob("*.csv.gz")):
        rd(p, bf)
    bf_end = max((d for d, _ in bf), default="")
    rows = {k: v for k, v in live.items() if k[0] > bf_end}
    rows.update(bf)
    dates = sorted({d for d, _ in rows})
    codes = sorted({c for _, c in rows})
    di = {d: i for i, d in enumerate(dates)}
    ci = {c: i for i, c in enumerate(codes)}
    O = np.full((len(dates), len(codes)), np.nan)
    C = np.full((len(dates), len(codes)), np.nan)
    for (d, c), (o, cl) in rows.items():
        if o and o > 0:
            O[di[d], ci[c]] = o
        if cl and cl > 0:
            C[di[d], ci[c]] = cl
    # backfill より後に権利落ちした分割: その前の値を比率で割る
    try:
        sj = json.loads((root / "docs" / "splits.json").read_text(encoding="utf-8"))
    except Exception:
        sj = {}
    sp = {}
    for c, lst in (sj.get("by_code") or {}).items():
        for s in lst or []:
            ex, r = s.get("ex"), s.get("ratio")
            if ex and r and float(r) > 0:
                sp.setdefault(c, {})[ex] = float(r)
                if ex > bf_end and c in ci:
                    m = np.array([d < ex for d in dates])
                    O[m, ci[c]] /= float(r)
                    C[m, ci[c]] /= float(r)
    # 終値の並びの検査（px_sanity）。直した係数は始値にも同じく掛ける
    try:
        import px_sanity as S
        res = S.clean_columns(dates, C, codes, sp)
        for j, (st, fac, _) in res.items():
            drop = ~np.isfinite(fac)
            C[drop, j] = np.nan
            O[drop, j] = np.nan
            ok = np.isfinite(fac) & (fac != 1.0)
            C[ok, j] *= fac[ok]
            O[ok, j] *= fac[ok]
        if res:
            log(f"  株価の検査（px_sanity）: {len(res)}銘柄")
    except Exception as ex:  # 検査が動かなくても飛びの線（0.4〜2.5倍）は効いている
        log(f"::warning::px_sanity を使えなかった: {ex}")
    return Px(dates, codes, O, C), bf_end


# ──────────────────────────────────────── seed（過去の分）
def read_seed(path=SEED):
    rows = {}
    if not Path(path).exists():
        return rows
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            e = {k: (r[k] or None) for k in ("code", "date", "time", "q", "fy", "src", "anchor")}
            for k in ("gap", "d1", "d5", "c1", "xg", "x1", "x5"):
                e[k] = float(r[k]) if r[k] not in ("", None) else None
            e["timing"] = timing_of(e["date"], e["time"]) or "after"
            rows[(e["code"], e["date"])] = e
    return rows


def write_seed(rows, path=SEED):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SEED_COLS)
    for e in sorted(rows, key=lambda e: (e["code"], e["date"])):
        w.writerow(["" if e.get(k) is None else e[k] for k in SEED_COLS])
    with gzip.GzipFile(path, "wb", mtime=0) as g:
        g.write(buf.getvalue().encode("utf-8"))


# ──────────────────────────────────────── まとめ
def summarize(evs):
    """evs: 古い順の回のリスト（measure 済み）"""
    d1 = [e for e in evs if e.get("x1") is not None]
    n = len(evs)
    up = sum(1 for e in d1 if e["x1"] > 0)
    med_d1 = statistics.median([abs(e["d1"]) for e in evs if e.get("d1") is not None]) if evs else None
    gaps = [abs(e["gap"]) for e in evs if e.get("gap") is not None]
    med_gap = statistics.median(gaps) if gaps else None
    times = [e["time"] for e in evs if e.get("time")]
    tc = Counter(times).most_common(1)
    usual_t, usual_n = (tc[0] if tc else (None, 0))
    tmc = Counter(e.get("timing") for e in evs if e.get("time"))
    big = max((e for e in evs if e.get("c1") is not None), key=lambda e: abs(e["c1"]), default=None)
    return {"n": n, "n_x1": len(d1), "up_x1": up, "med_abs_d1": r2(med_d1), "med_abs_gap": r2(med_gap),
            "n_gap": len(gaps), "usual_time": usual_t, "usual_time_n": usual_n, "n_time": len(times),
            "timing": {TIMING_JA[k]: v for k, v in tmc.items() if k in TIMING_JA},
            "max": ({"date": big["date"], "c1": big["c1"], "q": big.get("q")} if big else None),
            "from": evs[0]["date"] if evs else None, "last": evs[-1]["date"] if evs else None}


def file_obj(code, name, evs, nxt, asof):
    rows = []
    for e in reversed(evs):          # 新しい順
        rows.append([e["date"], e.get("time"), TIMING_JA.get(e.get("timing")) if e.get("time") else None,
                     e.get("q"), e.get("fy"), e.get("gap"), e.get("d1"), e.get("d5"), e.get("c1"),
                     e.get("xg"), e.get("x1"), e.get("x5")])
    return {"code": code, "name": name, "asof": asof, "next": nxt, "sum": summarize(evs), "cols": ROW_COLS, "rows": rows}


def load_universe(root):
    out = {}
    with open(Path(root) / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            c = (r.get("code") or "").strip()
            if len(c) == 4:
                out[c] = unicodedata.normalize("NFKC", r.get("name") or "").strip()
    return out


def load_next(root, today):
    """docs/kessan.json → {code: 'YYYY-MM-DD'}（今日以降の最初の予定）と、今後2週間の日ごとの一覧"""
    try:
        k = json.loads((Path(root) / "docs" / "kessan.json").read_text(encoding="utf-8"))
    except Exception:
        return {}, []
    nxt, days = {}, []
    lim = (date.fromisoformat(today) + timedelta(days=UPCOMING_DAYS)).isoformat()
    for d in k.get("days") or []:
        dd = d.get("d")
        if not dd or dd < today:
            continue
        for it in d.get("items") or []:
            c = it.get("c")
            if c and c not in nxt:
                nxt[c] = dd
        if dd <= lim:
            days.append({"d": dd, "w": d.get("w"), "codes": [it.get("c") for it in d.get("items") or [] if it.get("c")]})
    return nxt, days


def write_text(p, txt):
    if p.exists() and p.read_text(encoding="utf-8") == txt:
        return False
    p.write_text(txt, encoding="utf-8", newline="\n")
    return True


def dumps(o):
    return json.dumps(o, ensure_ascii=False, separators=(",", ":"))


def index_row(s):
    return [s["n"], s["n_x1"], s["up_x1"], s["med_abs_d1"], s["med_abs_gap"], s["usual_time"], s["usual_time_n"],
            s["n_time"], s["last"]]


INDEX_COLS = ["回数", "対TOPIXが出た回数", "翌営業日に対TOPIXで上回った回数", "翌営業日の動きの大きさの中央値",
              "窓の大きさの中央値", "ふだんの発表時刻", "その時刻の回数", "時刻が分かる回数", "最後の発表日"]


def write_upcoming(out, index, names, days, today):
    items = []
    for d in days:
        lst = []
        for c in d["codes"]:
            if c in index:
                lst.append([c, names.get(c, c)] + index[c])
        items.append({"d": d["d"], "w": d.get("w"), "n_all": len(d["codes"]), "items": lst})
    obj = {"asof": today, "days_ahead": UPCOMING_DAYS, "cols": ["コード", "銘柄名"] + INDEX_COLS, "days": items,
           "note": "過去の決算発表のあとの値動きの記録です。この先も同じとは限りません。"}
    return write_text(Path(out) / "upcoming.json", dumps(obj))


def build(root=ROOT, out=OUT, upcoming_only=False):
    t0 = time.time()
    root, out = Path(root), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    today = datetime.now(JST).date().isoformat()
    names = load_universe(root)
    nxt, days = load_next(root, today)
    if upcoming_only:
        try:
            index = json.loads((out / "index.json").read_text(encoding="utf-8"))["items"]
        except Exception:
            log("index.json がまだ無い。upcoming は書かない")
            return 0
        w = write_upcoming(out, index, names, days, today)
        log(f"upcoming.json {'書いた' if w else '同じ'}（{len(days)}日）")
        return 0

    seed = read_seed()
    log(f"seed {len(seed)}回")
    new = merge_events(events_from_flash(root), events_from_tdnet(root))
    px, bf_end = load_bars_px(root)
    log(f"日足 {px.cal[0] if px.cal else '-'}〜{px.cal[-1] if px.cal else '-'}（{px.T}日）")
    allev = dict(seed)
    added = skipped = 0
    for k, e in new.items():
        if k in seed or e["code"] not in names:
            continue
        m = measure(px, e)
        if m is None:
            skipped += 1
            continue
        e.update(m)
        allev[k] = e
        added += 1
    log(f"新しい回 {added}（測れなかった {skipped}）")
    by = {}
    for (c, d), e in allev.items():
        if c in names:
            by.setdefault(c, []).append(e)
    index, wrote, same, allrows = {}, 0, 0, {}
    keep = {"meta.json", "index.json", "upcoming.json", "all.json"}
    for c, evs in by.items():
        evs.sort(key=lambda e: e["date"])
        obj = file_obj(c, names[c], evs, nxt.get(c), px.cal[-1] if px.cal else today)
        index[c] = index_row(obj["sum"])
        mx = obj["sum"].get("max") or {}
        allrows[c] = [index[c] + [mx.get("date"), mx.get("c1")],
                      [[r[0], r[1], r[3], r[5], r[6], r[7], r[8], r[10]] for r in obj["rows"]]]
        keep.add(f"{c}.json")
        obj_txt = dumps(obj)
        if write_text(out / f"{c}.json", obj_txt):
            wrote += 1
        else:
            same += 1
    removed = 0
    for p in out.glob("*.json"):
        if p.name not in keep:
            p.unlink()
            removed += 1
    write_text(out / "index.json", dumps({"asof": px.cal[-1] if px.cal else today, "cols": INDEX_COLS, "items": index}))
    # 銘柄ページの生成（サイトの gen_stocks.py）が1回で読む束（全銘柄の回ごとの行）。
    # 銘柄ごとに1,800回取りに行くと GitHub Pages の一時的な失敗に当たりやすいため（2026-11-29 対象を全銘柄の半分に広げた）
    write_text(out / "all.json", dumps({"asof": px.cal[-1] if px.cal else today,
                                         "cols_sum": INDEX_COLS + ["いちばん大きく動いた回", "その回の前の終値→翌営業日の終値"],
                                         "cols_row": ["発表日", "時刻", "四半期", "窓", "翌営業日", "5営業日後",
                                                      "前の終値→翌営業日の終値", "翌営業日の対TOPIX"],
                                         "items": allrows}))
    write_upcoming(out, index, names, days, today)
    cnt = Counter(min(len(v), 12) for v in by.values())
    meta = {"updated": datetime.now(JST).isoformat(timespec="seconds"), "asof": px.cal[-1] if px.cal else None,
            "n_codes": len(by), "n_events": sum(len(v) for v in by.values()),
            "n_codes_by_count": {str(k): v for k, v in sorted(cnt.items())},
            "rule": {"anchor": "寄り前の発表はその日、場中・引け後の発表は翌営業日の始値（知り得たあとの最初の始値）",
                     "gap": "発表の前の最後の終値 → 起点の始値", "d1": "起点の始値 → 同じ日の終値",
                     "d5": "起点の始値 → 起点を1本目とした5本目の終値", "c1": "発表の前の最後の終値 → 起点の日の終値",
                     "topix": "同じ区間の 1306（TOPIX 連動 ETF）との差", "jump": [JUMP_LO, JUMP_HI],
                     "close_time": "引けは 2024-11-05 から 15:30・それより前は 15:00"},
            "source": {"dates": "決算短信の開示の日と時刻（2024-06〜2026-09 は J-Quants の決算サマリー、それ以後は TDnet の開示一覧）",
                       "prices": "日足（株式分割は調整済み・配当は未調整）から株レーダーが計算"},
            "seconds": round(time.time() - t0, 1)}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    log(f"書いた {wrote} / 同じ {same} / 消した {removed} / 銘柄 {len(by)} / {meta['seconds']}秒")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--upcoming-only", action="store_true")
    a = ap.parse_args()
    return build(ROOT, Path(a.out), a.upcoming_only)


if __name__ == "__main__":
    sys.exit(main())
