# -*- coding: utf-8 -*-
"""ストップ高・ストップ安の判定 → docs/stophigh.json ＋ docs/stophigh_hist/YYYY-MM.jsonl（追記のみ）

起動文: kaburadar\\起動文_実務_ストップ高と値幅制限_2026-10-24.md

■ 判定（東証の制限値幅・呼値の単位。2026-10-24 に JPX の公式ページで確認）
  基準値段 = 前営業日の終値（yfinance の Close。auto_adjust=False＝配当は調整しない・分割だけ調整済み）。
  上限 = 基準値段＋制限値幅。呼値の単位に合わないときは1つ上の呼値の値段（実際の約定で確認・stop_prices の注記）。
  下限 = 基準値段−制限値幅。
  呼値の単位は TOPIX500 構成銘柄（JPX 上場銘柄一覧の規模区分 Core30/Large70/Mid400）とその他で違う。
    close_high … 終値が上限（終値ストップ高）   touch_high … 高値だけ上限に届いた（一時ストップ高）
    close_low  … 終値が下限（終値ストップ安）   touch_low  … 安値だけ下限に届いた（一時ストップ安）

■ 判定しない日（推測で埋めない）
  - 株式分割の権利落ち日（docs/splits.json の by_code / upcoming / recent の ex）。基準値段が調整されるため
  - 前営業日または当日の出来高が0（基準値段が最終気配のことがある・当日は気配だけで約定なし）
  - 前の足が「前営業日」でない（売買停止明け・新規上場の初日）
  - 高値が上限を超える／安値が下限を下回る（制限値幅の拡大・株式併合や無償割当の調整・データの誤り。
    どれか見分けられないので判定しない。件数は skipped に出す）

■ 翌日の値動き（答え合わせ）
  ストップ高（安）の日の終値から、翌営業日の始値・翌営業日の終値・5営業日後の終値までの騰落（%）。
  その間に分割の権利落ち日があるものは測らない。5営業日がそろった時点で 1行（t=fu）を追記する。

■ 追記のみ（docs/stophigh_hist/YYYY-MM.jsonl・月はストップの日）
  {"t":"ev", ...}  判定（1銘柄1日1行）
  {"t":"fu", ...}  翌日・5日後の値動き
  {"t":"td", ...}  その日の開示（TDnet の表題。後から届いた分も1行として足す・まとめるのは読む側）

■ 株価の取り方
  毎営業日: movers_daily.py が同じジョブの中で書く .cache/ohlc_recent.pkl（直近30本の日足）を読む。
            無ければ自分で yfinance から1か月ぶん取る（手動実行用）。
  初回:     python -X utf8 stophigh.py --backfill   … yfinance の1年ぶん（prices.json と同じ取得）から作る

使い方:
  python -X utf8 stophigh.py
  python -X utf8 stophigh.py --backfill
"""
import csv
import io
import json
import math
import pickle
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
OUT = DOCS / "stophigh.json"
HIST = DOCS / "stophigh_hist"
CACHE = ROOT / ".cache" / "ohlc_recent.pkl"
UNIVERSE = ROOT / "tenbagger_universe.csv"
JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
RECENT_DAYS = 10            # stophigh.json に一覧を持つ直近の営業日数
FU_DAYS = 5                 # 5営業日後
SANKOU = 30                 # 30件未満は参考値

RULE_SRC = {
    "limit": "https://www.jpx.co.jp/equities/trading/domestic/06.html",
    "tick": "https://www.jpx.co.jp/equities/trading/domestic/07.html",
    "checked": "2026-10-24",
}

# ── 制限値幅（JPX「制限値幅」・基準値段 X円未満 → 上下 Y円）。最後の行は 50,000,000円以上
LIMITS = [
    (100, 30), (200, 50), (500, 80), (700, 100), (1000, 150), (1500, 300), (2000, 400),
    (3000, 500), (5000, 700), (7000, 1000), (10000, 1500), (15000, 3000), (20000, 4000),
    (30000, 5000), (50000, 7000), (70000, 10000), (100000, 15000), (150000, 30000),
    (200000, 40000), (300000, 50000), (500000, 70000), (700000, 100000), (1000000, 150000),
    (1500000, 300000), (2000000, 400000), (3000000, 500000), (5000000, 700000),
    (7000000, 1000000), (10000000, 1500000), (15000000, 3000000), (20000000, 4000000),
    (30000000, 5000000), (50000000, 7000000),
]
LIMIT_TOP = 10000000

# ── 呼値の単位（JPX「呼値の単位」・値段 X円以下 → 単位）。最後の値は 50,000,000円超
TICK_T500 = [(1000, 0.1), (3000, 0.5), (5000, 1), (10000, 1), (30000, 5), (50000, 10), (100000, 10),
             (300000, 50), (500000, 100), (1000000, 100), (3000000, 500), (5000000, 1000),
             (10000000, 1000), (30000000, 5000), (50000000, 10000)]
TICK_T500_TOP = 10000
TICK_OTHER = [(1000, 1), (3000, 1), (5000, 5), (10000, 10), (30000, 10), (50000, 50), (100000, 100),
              (300000, 100), (500000, 500), (1000000, 1000), (3000000, 1000), (5000000, 5000),
              (10000000, 10000), (30000000, 10000), (50000000, 50000)]
TICK_OTHER_TOP = 100000
T500_SCALES = {"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"}


def log(*a):
    print(*a, flush=True)


# ─────────────────────────────────────────────
#  規則（0.1円単位の整数で計算して、浮動小数の誤差を持ち込まない）
# ─────────────────────────────────────────────
def to10(x):
    return int(round(float(x) * 10))


def limit_width(base):
    for lim, w in LIMITS:
        if base < lim:
            return w
    return LIMIT_TOP


def tick(price, t500):
    tbl, top = (TICK_T500, TICK_T500_TOP) if t500 else (TICK_OTHER, TICK_OTHER_TOP)
    for lim, t in tbl:
        if price <= lim:
            return t
    return top


def stop_prices(base, t500):
    """(上限, 下限)。円。下限が0円以下になるとき（基準値段が制限値幅以下）は None

    基準値段＋制限値幅が呼値の単位に合わないとき（上の帯の呼値が粗い: 2,759円＋500円＝3,259円 → 5円刻み）は
    **1つ上の呼値の値段**（3,260円）を上限にする。JPX のページには丸め方の記載が無いが、実際の約定で
    確かめた（2026-10-24: 過去1年に 3,260円・3,400円・3,025円・5,160円 など119件、切り下げた値段を超えて
    ちょうど切り上げた値段で約定していた。1847 2026-08-07・2121 2026-08-03・3131 2026-08-10 ほか）。
    下限は基準値段が呼値の単位に乗っていれば丸めは起きない（下の帯ほど刻みが細かい）。念のため外側（切り下げ）にそろえる"""
    b10 = to10(base)
    w10 = limit_width(base) * 10
    up10 = b10 + w10
    t10 = to10(tick(up10 / 10, t500))
    up10 = -((-up10) // t10) * t10
    lo10 = b10 - w10
    if lo10 <= 0:
        lo = None
    else:
        t10 = to10(tick(lo10 / 10, t500))
        lo10 = (lo10 // t10) * t10
        lo = lo10 / 10
    return up10 / 10, lo


def classify(base, o, h, l, c, t500):
    """('close_high'|'touch_high'|'close_low'|'touch_low'|None, 上限, 下限) / 範囲外は ('out', …)"""
    up, lo = stop_prices(base, t500)
    h10, l10, c10 = to10(h), to10(l), to10(c)
    if h10 > to10(up) or (lo is not None and l10 < to10(lo)):
        return "out", up, lo
    if c10 == to10(up):
        return "close_high", up, lo
    if lo is not None and c10 == to10(lo):
        return "close_low", up, lo
    if h10 == to10(up):
        return "touch_high", up, lo
    if lo is not None and l10 == to10(lo):
        return "touch_low", up, lo
    return None, up, lo


# ─────────────────────────────────────────────
#  入力
# ─────────────────────────────────────────────
def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def load_t500():
    """TOPIX500 構成銘柄（呼値の単位の表を分ける）。JPX 一覧 → 取れなければ tenbagger_universe.csv の scale"""
    try:
        import pandas as pd
        raw = urllib.request.urlopen(urllib.request.Request(JPX_LIST, headers=UA), timeout=60).read()
        df = pd.read_excel(io.BytesIO(raw), dtype=str)
        s = {str(r["コード"]).strip() for _, r in df.iterrows() if str(r["規模区分"]).strip() in T500_SCALES}
        if 450 <= len(s) <= 520:
            log(f"TOPIX500: {len(s)}銘柄（JPX一覧 {df['日付'].iloc[0]}版）")
            return s, f"JPX上場銘柄一覧 {df['日付'].iloc[0]}版"
        log(f"TOPIX500 の数がおかしい（{len(s)}）。CSV に切り替える")
    except Exception as e:
        log(f"JPX一覧の取得に失敗（CSVの規模区分で続行）: {e}")
    s = set()
    with open(UNIVERSE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if (r.get("scale") or "").strip() in T500_SCALES:
                s.add(r["code"].strip())
    return s, "tenbagger_universe.csv の規模区分"


def load_split_ex():
    """{(code, 'YYYY-MM-DD')}: 分割の権利落ち日（判定しない日）"""
    sp = load_json(DOCS / "splits.json", {}) or {}
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
                    out.add((e["code"], e[k]))
    return out


def load_bars(backfill):
    """({code: DataFrame}, {code: {"n","s"}}, 出所の説明)"""
    if not backfill and CACHE.exists():
        try:
            with open(CACHE, "rb") as f:
                d = pickle.load(f)
            log(f"日足: {CACHE.name}（{d.get('saved')}・{len(d['data'])}銘柄）")
            bars = {t.replace(".T", ""): df for t, df in d["data"].items()}
            meta = {u["c"]: {"n": u["n"], "s": u["s"]} for u in d["uni"]}
            return bars, meta, "movers_daily と同じ取得"
        except Exception as e:
            log(f"キャッシュが読めない（自分で取る）: {e}")
    keep = ROOT / ".cache" / "ohlc_1y.pkl"      # --backfill を同じ日に何度か試すとき用（コミットしない）
    if backfill and keep.exists() and datetime.fromtimestamp(keep.stat().st_mtime).date() == datetime.now().date():
        with open(keep, "rb") as f:
            d = pickle.load(f)
        log(f"日足: {keep.name}（今日取った1年ぶん・{len(d['bars'])}銘柄）")
        return d["bars"], d["meta"], "yfinance 1y"
    import movers_daily as M
    uni = M.load_universe()
    period = "1y" if backfill else "1mo"
    import yfinance as yf
    import pandas as pd

    def chunk(ts):
        df = yf.download(ts, period=period, interval="1d", progress=False,
                         auto_adjust=False, group_by="ticker", threads=True)
        got = {}
        if df is None or df.empty:
            return got
        for t in ts:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 2:
                    got[t] = sub[["Open", "High", "Low", "Close", "Volume"]]
            except Exception:
                continue
        return got

    tickers = [u["t"] for u in uni]
    data = {}
    for i in range(0, len(tickers), M.CHUNK):
        ch = tickers[i:i + M.CHUNK]
        try:
            got = chunk(ch)
        except Exception as e:
            log(f"  失敗: {e}")
            got = {}
        miss = [t for t in ch if t not in got]
        if miss:
            time.sleep(10)
            try:
                got.update(chunk(miss))
            except Exception:
                pass
        data.update(got)
        log(f"  {i // M.CHUNK + 1}: {len(got)}/{len(ch)}")
        time.sleep(M.WAIT)
    bars = {t.replace(".T", ""): df for t, df in data.items()}
    meta = {u["c"]: {"n": u["n"], "s": u["s"]} for u in uni}
    if backfill:
        keep.parent.mkdir(exist_ok=True)
        with open(keep, "wb") as f:
            pickle.dump({"bars": bars, "meta": meta}, f)
    return bars, meta, f"yfinance {period}"


def trade_calendar(bars):
    """取引日 = 半分以上の銘柄に足がある日（銘柄ごとの欠けに引きずられない）"""
    cnt = Counter()
    for df in bars.values():
        for ts in df.index:
            cnt[ts.date().isoformat()] += 1
    if not cnt:
        return []
    top = max(cnt.values())
    return sorted(d for d, n in cnt.items() if n >= top * 0.5)


# ─────────────────────────────────────────────
#  履歴（追記のみ）
# ─────────────────────────────────────────────
def read_hist():
    rows = []
    for p in sorted(HIST.glob("????-??.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    return rows


def append(rows):
    by_m = defaultdict(list)
    for r in rows:
        by_m[r["d"][:7]].append(r)
    HIST.mkdir(parents=True, exist_ok=True)
    for m, rs in by_m.items():
        with open(HIST / f"{m}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in sorted(rs, key=lambda x: (x["d"], x["c"])):
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def merge(rows):
    """ev に fu / td を重ねる。{(d, c): event}"""
    ev = {}
    for r in rows:
        if r.get("t") == "ev":
            ev.setdefault((r["d"], r["c"]), dict(r))
    for r in rows:
        k = (r["d"], r["c"])
        if k not in ev:
            continue
        if r.get("t") == "fu":
            ev[k]["fu"] = {x: r.get(x) for x in ("n1o", "n1c", "n5c", "skip") if x in r}
        elif r.get("t") == "td":
            # 後の行ほど新しい。表題は重ねて重複を落とす
            have = ev[k].setdefault("td", [])
            seen = {(x.get("d"), x.get("t")) for x in have}
            for it in r.get("items") or []:
                if (it.get("d"), it.get("t")) not in seen:
                    have.append(it)
                    seen.add((it.get("d"), it.get("t")))
            ev[k]["td_chk"] = True
    return ev


# ─────────────────────────────────────────────
#  開示（movers と同じ TDnet の表題）
# ─────────────────────────────────────────────
def load_tdnet():
    import movers_daily as M
    return M.load_tdnet()


def tdnet_for(td, code, d_prev, d):
    import movers_daily as M
    return [{k: f[k] for k in ("d", "t", "u", "tm") if k in f} for f in M.tdnet_facts(td, code, d_prev, d)]


def tdnet_from_movers(code, d):
    """movers_hist に照合済みで載っていれば、その表題（±5%・売買代金1億円以上の銘柄だけ）"""
    mv = load_json(DOCS / "movers_hist" / f"{d}.json")
    if not mv or not mv.get("tdnet_checked"):
        return None
    for side in ("up", "down"):
        for r in mv.get(side) or []:
            if r.get("c") == code:
                return [{k: f[k] for k in ("d", "t", "u", "tm") if k in f}
                        for f in r.get("facts") or [] if f.get("k") == "tdnet"]
    return None


# ─────────────────────────────────────────────
#  本体
# ─────────────────────────────────────────────
def pct(a, b):
    return round((a / b - 1) * 100, 2) if b else None


def main():
    backfill = "--backfill" in sys.argv
    t0 = time.time()
    bars, meta, src = load_bars(backfill)
    if not bars:
        log("::error::日足が1銘柄も無い")
        sys.exit(1)
    cal = trade_calendar(bars)
    pos = {d: i for i, d in enumerate(cal)}
    t500, t500_src = load_t500()
    split_ex = load_split_ex()
    log(f"取引日 {cal[0]}〜{cal[-1]}（{len(cal)}日）・分割の権利落ち日 {len(split_ex)}件")

    prev_td = (load_json(OUT, {}) or {}).get("trade_date") or ""
    if prev_td and cal[-1] < prev_td:
        log(f"::warning::日足の最終日 {cal[-1]} が前回の stophigh.json（{prev_td}）より古い。何も書かずに終了")
        return

    old = read_hist()
    have_ev = {(r["d"], r["c"]) for r in old if r.get("t") == "ev"}
    have_fu = {(r["d"], r["c"]) for r in old if r.get("t") == "fu"}

    sh_raw = load_json(DOCS / "shinyo_meigara.json", {}) or {}
    sh = {str(i.get("c")): i for i in sh_raw.get("items") or []}
    kr_raw = load_json(DOCS / "karauri.json", {}) or {}
    kr = {str(s.get("c")): s for s in kr_raw.get("stocks") or []}
    latest = cal[-1]

    new_ev, skipped, out_samples = [], Counter(), []
    series = {}       # code -> {date: (o,h,l,c,v)}
    for code, df in bars.items():
        rows = []
        for ts, r in df.iterrows():
            try:
                o, h, l, c, v = (float(r["Open"]), float(r["High"]), float(r["Low"]),
                                 float(r["Close"]), float(r["Volume"] or 0))
            except Exception:
                continue
            if any(math.isnan(x) for x in (o, h, l, c)):
                continue
            rows.append((ts.date().isoformat(), o, h, l, c, 0.0 if math.isnan(v) else v))
        series[code] = {x[0]: x[1:] for x in rows}
        for i in range(1, len(rows)):
            d, o, h, l, c, v = rows[i]
            pd_, _, _, _, pc, pv = rows[i - 1]
            if d not in pos or pos[d] == 0 or cal[pos[d] - 1] != pd_:
                continue                     # 前の足が前営業日でない（停止明け・上場初日）
            if (d, code) in have_ev:
                continue
            if pc <= 0:
                continue
            kind, up, lo = classify(pc, o, h, l, c, code in t500)
            if kind is None:
                continue
            # 件数は「判定に当たったのに出さなかった」ものだけ数える
            if (code, d) in split_ex:
                skipped["split"] += 1
                continue
            if pv <= 0 or v <= 0:
                skipped["novol"] += 1
                continue
            if kind == "out":
                skipped["out"] += 1
                if len(out_samples) < 400:
                    out_samples.append((d, code, pc, h, l, c, up, lo))
                continue
            vols = [x[5] for x in rows[max(0, i - 20):i] if x[5] > 0]
            vr = round(v / (sum(vols) / len(vols)), 1) if len(vols) >= 5 else None
            m = meta.get(code, {})
            e = {"t": "ev", "d": d, "c": code, "n": m.get("n", ""), "s": m.get("s", ""), "k": kind,
                 "base": pc, "up": up, "lo": lo, "o": o, "h": h, "l": l, "cl": c,
                 "chg": pct(c, pc), "vr": vr, "val": round(c * v / 1e6), "t500": code in t500}
            if d == latest:
                # 信用倍率・空売り残高は「その時点の既存の値」。過去の日に今の値は付けない
                i_ = sh.get(code)
                if i_ and i_.get("s"):
                    e["mr"] = round((i_.get("b") or 0) / i_["s"], 2)
                    e["mr_asof"] = sh_raw.get("asof")
                k_ = kr.get(code)
                if k_ and k_.get("total") is not None:
                    e["kr"] = k_["total"]
                    e["kr_asof"] = kr_raw.get("report_date")
            new_ev.append(e)
    if out_samples:
        (ROOT / ".cache").mkdir(exist_ok=True)
        (ROOT / ".cache" / "stophigh_out.json").write_text(json.dumps(out_samples, ensure_ascii=False), encoding="utf-8")
    log(f"新しい判定: {len(new_ev)}件（{dict(Counter(e['k'] for e in new_ev))}）・判定しない {dict(skipped)}")

    # ── 翌日・5日後（5営業日そろったら1行）
    allev = merge(old + new_ev)
    new_fu = []
    partial = {}
    for (d, code), e in allev.items():
        if (d, code) in have_fu or d not in pos:
            continue
        s = series.get(code)
        if not s or d not in s:
            continue
        i = pos[d]
        nxt = cal[i + 1:i + 1 + FU_DAYS]
        if any((code, x) in split_ex for x in nxt):
            if len(nxt) == FU_DAYS:
                new_fu.append({"t": "fu", "d": d, "c": code, "skip": "split"})
            continue
        c0 = s[d][3]
        f = {}
        if nxt and nxt[0] in s:
            f["n1o"] = pct(s[nxt[0]][0], c0)
            f["n1c"] = pct(s[nxt[0]][3], c0)
        if len(nxt) == FU_DAYS and nxt[-1] in s:
            f["n5c"] = pct(s[nxt[-1]][3], c0)
        if len(nxt) == FU_DAYS and "n1o" in f and "n5c" in f:
            new_fu.append({"t": "fu", "d": d, "c": code, **f})
        elif f:
            partial[(d, code)] = f

    # ── 開示（TDnet の表題）。一覧が前日・当日の両方を持っている日だけ「照合済み」にする
    td = load_tdnet()
    have_td = {(r["d"], r["c"]) for r in old if r.get("t") == "td"}
    new_td = []
    for (d, code), e in allev.items():
        if d not in pos or pos[d] == 0:
            continue
        d_prev = cal[pos[d] - 1]
        items = None
        import movers_daily as M
        if M.tdnet_covers(td, d_prev, d):
            items = tdnet_for(td, code, d_prev, d)
        if items is None:
            items = tdnet_from_movers(code, d)
        if items is None:
            continue
        cur = e.get("td") or []
        seen = {(x.get("d"), x.get("t")) for x in cur}
        add = [x for x in items if (x.get("d"), x.get("t")) not in seen]
        if add or (d, code) not in have_td:
            new_td.append({"t": "td", "d": d, "c": code, "items": add})

    append(new_ev + new_fu + new_td)
    log(f"追記: ev {len(new_ev)} / fu {len(new_fu)} / td {len(new_td)}")

    # ── 公開用のまとめ
    rows = read_hist()
    ev = merge(rows)
    for k, f in partial.items():
        if k in ev and "fu" not in ev[k]:
            ev[k]["fu"] = f
    days = sorted({d for d, _ in ev}, reverse=True)
    recent_days = [d for d in cal[::-1][:RECENT_DAYS]]

    def slim(e):
        return {k: v for k, v in e.items() if k != "t"}

    recent = {d: sorted([slim(e) for (dd, _), e in ev.items() if dd == d],
                        key=lambda x: (-(x.get("chg") or 0) if x["k"].endswith("high") else (x.get("chg") or 0)))
              for d in recent_days}
    by_year = defaultdict(Counter)
    by_day = defaultdict(Counter)
    for (d, _), e in ev.items():
        by_year[d[:4]][e["k"]] += 1
        by_day[d][e["k"]] += 1

    def summ(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"n": 0}
        return {"n": len(vals), "mean": round(statistics.fmean(vals), 2),
                "median": round(statistics.median(vals), 2),
                "pos": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
                "sankou": len(vals) < SANKOU}

    stats = {}
    for kind in ("close_high", "touch_high", "close_low", "touch_low"):
        es = [e for e in ev.values() if e["k"] == kind and isinstance(e.get("fu"), dict) and not e["fu"].get("skip")]
        stats[kind] = {m: summ([e["fu"].get(m) for e in es]) for m in ("n1o", "n1c", "n5c")}
        ds = [e["d"] for e in es]
        stats[kind]["from"] = min(ds) if ds else None
        stats[kind]["to"] = max(ds) if ds else None
    # 銘柄ページの1行用（直近1年・新しい順に最大5件と回数）
    cut = (datetime.fromisoformat(latest) - timedelta(days=365)).date().isoformat()
    by_code = defaultdict(list)
    for (d, code), e in sorted(ev.items(), reverse=True):
        if d >= cut:
            by_code[code].append([d, e["k"]])
    by_code = {c: {"n": dict(Counter(k for _, k in v)), "last": v[:5]} for c, v in by_code.items()}
    now = datetime.now(JST).isoformat(timespec="seconds")
    out = {
        "updated": now, "trade_date": latest, "prev_date": cal[-2] if len(cal) > 1 else None,
        "source": "東証の日足（yfinance・movers と同じ取得）から機械的に判定。開示は TDnet の表題",
        "rule": RULE_SRC, "t500_src": t500_src,
        "note": ("基準値段＝前営業日の終値。株式分割の権利落ち日、前営業日か当日の出来高が0の日、"
                 "高値・安値が通常の制限値段の外に出た日（制限値幅の拡大・株式併合などの調整・データの誤りを見分けられない）は判定していない。"
                 "TOPIX500 構成銘柄は現在の一覧で呼値の単位を決めている（過去の入れ替えは反映していない）。"),
        "since": min(days) if days else None,
        "hist_from": cal[0] if backfill else None,
        "skipped": dict(skipped),
        "days": {d: dict(by_day[d]) for d in sorted(by_day)},
        "by_year": {y: dict(c) for y, c in sorted(by_year.items())},
        "recent": recent,
        "stats": stats,
        "by_code": by_code,
        "months": sorted(p.stem for p in HIST.glob("????-??.jsonl")),
    }
    prev = load_json(OUT, {}) or {}
    if prev.get("hist_from") and not out["hist_from"]:
        out["hist_from"] = prev["hist_from"]
    # 過去分を作ったとき（--backfill）の「判定しない」の件数は残す。skipped はその回の分だけ
    out["skipped_backfill"] = dict(skipped) if backfill else prev.get("skipped_backfill")
    write_json(OUT, out)
    c = Counter(e["k"] for (d, _), e in ev.items() if d == latest)
    log(f"stophigh.json: {latest} 終値ストップ高 {c['close_high']}・一時 {c['touch_high']}"
        f"／終値ストップ安 {c['close_low']}・一時 {c['touch_low']}（累計 {len(ev)}件・{time.time() - t0:.0f}秒）")


if __name__ == "__main__":
    main()
