# -*- coding: utf-8 -*-
"""出来事の台帳（v2）の遡及（検証台帳 設計書 v1 段階3・2026-11-18）
→ data/ledger2/backfill/events/YYYY-MM.jsonl.gz ＋ scores/YYYY-MM.jsonl.gz（origin="backfill"）

■ 同じ検出関数
  当日記録（ledger2.py）と同じ ledger2_detect の検出関数を、日付を変えて呼ぶ。違うのは Sources（データの読み口）だけ:
    prices(d)    … 日足の表（ledger2_panel）から movers_daily.metrics と同じ計算で作った prices.json と同じ形
    stophigh(d)  … 日足の表から stophigh.classify（制限値幅・呼値）で作った stophigh_hist と同じ形
    bars(d)      … 日足の表の、その日に見えていた値（分割を戻した値）
    radar_items  … 温度計の段階は thermo100_hist（後から同じ式で計算し直した値。当時公開した値ではない）
    gauge        … docs/gauge.json の history（2026-10-03〜 の分だけ）
    tdnet(d)     … docs/tdnet_hist（保存してある 2026-10-30〜 の分だけ）
  信用残・空売り残高は過去の「その日に公表された値」を持っていないので遡及しない（空を返す）。
  既存のシグナル台帳（docs/ledger）の取り込みは遡及に入れない（当時の記録なので、ledger2_import_v1 として別に扱う）。
  温度計の段階変化（thermo_stage_change）は温度計の履歴がある 2016-11〜 の全期間を、株価のある期間と別に作る。

■ 答え合わせ（ledger2_score.py と同じ定義）
  起点 = base_date の始値 / 終点 = 起点から h 営業日後（jp_bizday）の終値 / h = 1・5・20・60
  raw_ret = 終点÷起点−1、ret = raw_ret − 1306.T の同じ期間の raw（市場全体 MKT は ^N225 で測る）、max_dd_20 = 20営業日の安値の最小÷起点−1。
  値は日足の表の「調整済み」（最後の日の株数に合わせた値＝分割調整済み）。1日で0.4倍未満／2.5倍超の飛びは invalid。
  表の最後の日より先の窓は書かない（あとで --late が足す）。

■ 置き場と再開
  月ごとの gzip（中身は当日記録と同じ形の JSONL）。月ファイルがもうあればその月は飛ばす（途中で止まっても続きから）。
  作り直すときは --force。meta.json に期間・件数・注意書き（上場廃止を含まない・実行日時点の調整済み）を書く。

使い方:
  python -X utf8 ledger2_backfill.py                       # 手元の bars_backfill（1年）＋ data/bars で
  python -X utf8 ledger2_backfill.py --px5y .cache/px5y    # 5年（yochou_px5y.py の出力）で
  python -X utf8 ledger2_backfill.py --late                # 毎営業日: 遡及分のまだ埋まっていない窓を足す（追記のみ）
  python -X utf8 ledger2_backfill.py --compare 2026-11-18  # 当日記録と同じ日を遡及の読み口で検出して突き合わせる（何も書かない）
"""
import argparse
import gzip
import io
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ledger2_detect as L
import ledger2_panel as P

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "ledger2" / "backfill"
IMPORTED = HERE / "data" / "ledger2" / "imported"
JST = timezone(timedelta(hours=9))
H = (1, 5, 20, 60)
JUMP_LO, JUMP_HI = 0.4, 2.5
TOPIX, N225 = "1306", "^N225"
THERMO_ONLY = {"thermo_stage_change"}          # 株価の表の期間より前も作る（温度計の履歴は10年）
NOTE = ("遡及分（origin=backfill）は、当日記録と同じ検出関数を過去の株価に当てて作ったもの。株価は取得日の時点で Yahoo が見せている値"
        "（分割は遡って調整済み・配当は未調整）で、上場廃止・統合で今いない銘柄は含まない（生き残りの偏り）。"
        "温度計の段階は thermo100_hist（後から同じ式で計算し直した値）。信用残・空売り残高は過去の公表時点の値が無いので遡及していない。"
        "制限値幅の拡大の日は過去分の情報が無く、ストップ高・安の判定から外れている。")


def log(*a):
    print(*a, flush=True)


class BackfillSources(L.Sources):
    """遡及の読み口。検出関数から見える形は当日記録と同じ"""

    def __init__(self, panel, root=None):
        super().__init__(root=root)
        self.panel = panel
        self._day = {}
        sp = {}
        for c, lst in panel.splits.items():
            sp[c] = {ex: r for ex, r in lst}
        self._sp = sp

    def _per_day(self, key, d, fn):
        k = (key, d)
        if k not in self._day:
            if len(self._day) > 64:
                self._day.clear()
            self._day[k] = fn()
        return self._day[k]

    def prices(self, d):
        return self._per_day("prices", d, lambda: self.panel.prices_like(d))

    def stophigh(self, d):
        return self._per_day("stop", d, lambda: self.panel.stop_events(d))

    def bars(self, d):
        return self._per_day("bars", d, lambda: self.panel.raw_bar(d) if self.panel.has_day(d) else None)

    def hist_stats(self, d):
        return self._per_day("hist", d, lambda: self.panel.hist_stats(d))

    def split_ex(self, code):
        return self._sp.get(L.code4(code), {})

    def stock_codes(self, d):
        return set(self.panel.codes) | super().stock_codes(d)

    # 遡及しないもの（過去の公表時点の値が無い）
    def karauri(self, d):
        return None

    def karauri_events(self):
        return []

    def shinyo_days(self):
        return {}, {}

    def listed_shares(self):
        return {}                                 # 今の上場株式数で過去の時価総額を作らない

    def thermo_now(self):
        return None

    def ledger_rows(self, d):
        return []

    def radar_items(self):
        """温度計の4段階: thermo100_hist（後から計算し直した値）"""
        return self._memo("radar_bf", lambda: [{"d": d, "regime": x.get("r"), "rule": "thermo100_hist"}
                                               for d, x in sorted(self.thermo_hist().items())])


# ──────────────────────────────────────── 答え合わせ（ledger2_score.py と同じ定義）
_days_cache = {}


def bizdays_from(d, n=max(H)):
    if d not in _days_cache:
        out = [d]
        while len(out) < n + 1:
            out.append(L.next_bizday(out[-1]))
        _days_cache[d] = out
    return _days_cache[d]


class Scorer:
    def __init__(self, panel):
        self.panel = panel
        self.ser = {}
        self.last = panel.dates[-1]

    def series(self, code):
        if code not in self.ser:
            self.ser[code] = self.panel.series(code)
        return self.ser[code]

    def score(self, e, done=frozenset(), nowiso=None):
        """出来事1件 → 答えの行（まだ窓が来ていないものは書かない）。nowiso=None なら scored_at を持たない"""
        return [{k: v for k, v in r.items() if not (k == "scored_at" and v is None)} for r in self._score(e, done, nowiso)]

    def _score(self, e, done, nowiso):
        eid, b = e["event_id"], e["base_date"]
        code = N225 if e["code"] == "MKT" else e["code"]
        days = bizdays_from(b)
        hs = [h for h in H if (eid, h) not in done and days[h] <= self.last]
        if not hs:
            return []
        ser, tser = self.series(code), self.series(TOPIX)
        span = days[:max(hs) + 1]
        prev, jd = None, None
        for d in span:
            if d in ser:
                c = ser[d][3]
                if prev and not (JUMP_LO <= c / prev <= JUMP_HI):
                    jd = d
                    break
                prev = c
        base = {"event_id": eid, "date": e["date"], "src": "backfill_px", "scored_at": nowiso}
        if jd:
            return [{**base, "status": "invalid", "reason": f"説明できない値の飛び（{jd}）"}]
        if b not in ser or b not in tser:
            return [{**base, "status": "invalid", "reason": "起点の日の株価が無い（売買停止・上場前など）"}]
        o, to = ser[b][0], tser[b][0]
        rows, miss = [], []
        for h in hs:
            end = days[h]
            if end not in ser or end not in tser:
                miss.append(h)
                continue
            raw = (ser[end][3] / o - 1) * 100
            traw = (tser[end][3] / to - 1) * 100
            row = {"event_id": eid, "date": e["date"], "h": h, "base_date": b, "base_open": round(o, 4),
                   "end_date": end, "end_close": round(ser[end][3], 4), "raw_ret": round(raw, 3),
                   "topix_ret": round(traw, 3), "ret": round(raw - traw, 3),
                   "status": "scored" if h == max(H) else "open", "src": "backfill_px", "scored_at": nowiso}
            if code == N225:
                row["bench"] = "^N225"
            if h == 20:
                lows = [ser[d][2] for d in days[:21] if d in ser]
                row["max_dd_20"] = round((min(lows) / o - 1) * 100, 3) if lows else None
            rows.append(row)
        if miss:
            # 終点の日がもう過ぎているのに株価が無い窓（売買停止・物差しの桁とびで捨てた日など）は、もう埋まらない。
            # invalid の行で閉じる（出ている窓の答えはそのまま使う。集計は h のある行だけを読む）
            rows.append({**base, "status": "invalid", "reason": "終点の日の株価が無い", "missing_h": miss})
        return rows


# ──────────────────────────────────────── 読み書き
def write_gz(p, rows):
    p.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    for r in rows:
        buf.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(buf.getvalue().encode("utf-8"))
    tmp.replace(p)


def read_jsonl_any(p):
    p = Path(p)
    if not p.exists():
        return []
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    return L.jsonl(p)


def load_backfill(out=OUT):
    """(events, scores)。scores は月の gzip ＋ あとから足した late-*.jsonl"""
    out = Path(out)
    ev, sc = [], []
    for f in sorted((out / "events").glob("*.jsonl.gz")):
        ev += read_jsonl_any(f)
    for f in sorted((out / "scores").glob("*.jsonl.gz")) + sorted((out / "scores").glob("late-*.jsonl")):
        sc += read_jsonl_any(f)
    return ev, sc


def slim(row):
    """遡及の行は数が多いので、空の欄を落とす（形は当日記録と同じ・欄が無い＝null）"""
    r = dict(row)
    r.pop("recorded_at", None)
    ctx = r.get("context") or {}
    ch = {k: v for k, v in (ctx.get("chart") or {}).items() if v is not None}
    r["context"] = {k: v for k, v in ctx.items() if v is not None and k != "chart"}
    if ch:
        r["context"]["chart"] = ch
    for k in ("time", "snapshot"):
        if not r.get(k):
            r.pop(k, None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--px5y", help="yochou_px5y.py の出力（.cache/px5y）。無ければ data/bars_backfill（1年）")
    ap.add_argument("--force", action="store_true", help="月ファイルがあっても作り直す")
    ap.add_argument("--until", default=None, help="この日まで（既定: 当日記録を始めた日の前日）")
    ap.add_argument("--late", action="store_true", help="遡及分のまだ埋まっていない窓だけ足す（毎営業日）")
    ap.add_argument("--compare", help="この日を遡及の読み口で検出し、当日記録の events と突き合わせる（書かない）")
    ap.add_argument("--import-v1", action="store_true",
                    help="既存のシグナル台帳（docs/ledger・2026-05〜当日記録の前日）を当時の記録（origin=live）として取り込む")
    a = ap.parse_args()
    out = Path(a.out)
    t0 = time.time()
    nowiso = datetime.now(JST).isoformat(timespec="seconds")

    if a.late:
        r = late(out, nowiso)
        if IMPORTED.exists() and Path(a.out) == OUT:
            late(IMPORTED, nowiso)
        return r
    if a.import_v1:
        return import_v1(IMPORTED if Path(a.out) == OUT else Path(a.out) / "imported", nowiso, a.force)

    pn = P.build(HERE, px5y=a.px5y)
    log(f"日足の表: {pn.dates[0]}〜{pn.dates[-1]}（{len(pn.dates)}日・{len(pn.codes)}銘柄）{time.time() - t0:.0f}秒")
    data = BackfillSources(pn)
    import ledger2 as LG

    if a.compare:
        return compare(a.compare, data)

    until = a.until or (date.fromisoformat(L.LIVE_START) - timedelta(days=1)).isoformat()
    days = [d for d in pn.dates if d <= until and pn.has_day(d)]
    # 温度計の段階変化は温度計の履歴の全期間（株価の表の期間に関係なく）
    tdays = [d for d in sorted(data.thermo_hist()) if d <= until]
    months = sorted({d[:7] for d in days} | {d[:7] for d in tdays})
    scorer = Scorer(pn)
    # 温度計だけの月の答え合わせ（日経平均）: 表に無い古い期間は yfinance で取る
    nk_old = None
    first_px = pn.dates[0]
    tot = Counter()
    written = []
    for ym in months:
        pe, ps = out / "events" / f"{ym}.jsonl.gz", out / "scores" / f"{ym}.jsonl.gz"
        if pe.exists() and ps.exists() and not a.force:
            continue
        rows = []
        for d in [x for x in days if x[:7] == ym]:
            for r in LG.detect_all(d, data, "backfill"):
                if r["event_type"] in THERMO_ONLY:
                    continue
                rows.append(slim(r))
        for d in [x for x in tdays if x[:7] == ym]:
            for e in L.detect_thermo_stage(d, data):
                r = {"event_id": f"{d.replace('-', '')}_MKT_{e['event_type']}", "date": d, "code": "MKT",
                     "event_type": e["event_type"], "event_params": e["event_params"],
                     "context": {k: v for k, v in L.context(d, "MKT", data).items() if v is not None and k != "chart"},
                     "snapshot": e["snapshot"], "known": e["known"], "base_date": L.base_date(d, e["known"]),
                     "origin": "backfill", "detector_version": L.DETECTOR_VERSION, "status": "open", "src": "thermo100_hist"}
                rows.append(r)
        rows.sort(key=lambda r: (r["date"], r["event_type"], r["code"]))
        scores = []
        for r in rows:
            if r["code"] == "MKT" and r["base_date"] < first_px:
                if nk_old is None:
                    nk_old = old_n225(tdays[0])
                scores += score_old_mkt(r, nk_old, None)
            else:
                scores += scorer.score(r)              # 答えの時刻は meta.json の updated（行ごとには持たない＝軽く）
        write_gz(pe, rows)
        write_gz(ps, scores)
        c = Counter(r["event_type"] for r in rows)
        tot.update(c)
        written.append(ym)
        log(f"  {ym}: 出来事 {len(rows)}件・答え {len(scores)}行  {time.time() - t0:.0f}秒")

    # meta（全月を読み直して数える）
    ev, sc = load_backfill(out)
    cnt = Counter(e["event_type"] for e in ev)
    per = {}
    for e in ev:
        t = e["event_type"]
        lo, hi = per.get(t, (e["date"], e["date"]))
        per[t] = (min(lo, e["date"]), max(hi, e["date"]))
    size = sum(f.stat().st_size for f in out.rglob("*.gz"))
    meta = {"updated": nowiso, "origin": "backfill", "detector_version": L.DETECTOR_VERSION,
            "price_source": ("yfinance 5年（yochou_px5y.py）＋ data/bars_backfill" if a.px5y else "data/bars_backfill（1年）")
                            + " ＋ data/bars（当日記録の日足）",
            "price_period": [pn.dates[0], pn.dates[-1]], "until": until,
            "adjusted_asof": (json.loads((Path(a.px5y) / "meta.json").read_text(encoding="utf-8")).get("adjusted_asof")
                              if a.px5y and (Path(a.px5y) / "meta.json").exists() else P.BARS_BACKFILL_ASOF),
            "survivorship": "上場廃止・統合で今いない銘柄は含まない",
            "thermo": "温度計の段階は thermo100_hist（後から同じ式で計算し直した値・当時公開した値ではない）",
            "not_backfilled": ["margin_buy_surge", "short_new_inst", "short_surge", "signal_*（既存のシグナル台帳）"],
            "partial": {"disclosure": "docs/tdnet_hist にある 2026-10-30〜 だけ", "gauge_stage_up": "docs/gauge.json の 2026-10-03〜 だけ",
                        "thermo_stage_change": "thermo100_hist の 2016-11〜（株価の期間より長い）"},
            "dropped_spikes": [list(x) for x in pn.dropped],   # 物差し（1306・^N225）の配信の桁とびとして捨てた日
            "note": NOTE, "counts": dict(sorted(cnt.items())), "periods": {k: list(v) for k, v in sorted(per.items())},
            "n_events": len(ev), "n_scores": len(sc), "bytes_gz": size}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    write_baseline(pn, out / "baseline.json.gz")
    log(f"遡及: 書いた月 {len(written)}・出来事 計 {len(ev)}件・答え 計 {len(sc)}行・gzip 計 {size / 1024 / 1024:.1f}MB  {time.time() - t0:.0f}秒")
    for k, v in sorted(cnt.items()):
        log(f"  {k}: {v}件（{per[k][0]}〜{per[k][1]}）")
    return 0


def import_v1(out, nowiso, force=False):
    """既存のシグナル台帳（docs/ledger/*.jsonl）を、当日記録と同じ検出関数（detect_signal_ledger）で出来事にする。
    台帳の r5/r20/r60 は「判定日の翌営業日の始値から・生のリターン」で、v2（知り得たあとの最初の始値・対 TOPIX の超過・
    1/5/20/60）と定義が違うので使わない。株価から v2 の定義で測り直す。行は当時の記録なので origin=live
    （imported_from に元の場所）。当日記録を始めた日（LIVE_START）より前だけ（それ以降は ledger2.py が取り込んでいる）"""
    out = Path(out)
    if (out / "events").exists() and any((out / "events").glob("*.gz")) and not force:
        log(f"{out} にもう取り込んである（作り直すなら --force）")
        return 0
    data = L.Sources()
    days = set()
    for f in sorted((data.docs / "ledger").glob("????-??.jsonl")):
        for r in L.jsonl(f):
            if r.get("date") and r["date"] < L.LIVE_START:
                days.add(r["date"])
    rows = []
    for d in sorted(days):
        for e in L.detect_signal_ledger(d, data):
            rows.append({"event_id": f"{d.replace('-', '')}_{e['code']}_{e['event_type']}", "date": d, "time": e.get("time"),
                         "code": e["code"], "event_type": e["event_type"], "event_params": e["event_params"],
                         "context": {k: v for k, v in L.context(d, e["code"], data).items() if v is not None and k != "chart"},
                         "snapshot": e["snapshot"], "known": e["known"], "base_date": L.base_date(d, e["known"]),
                         "origin": "live", "imported_from": "docs/ledger", "detector_version": L.DETECTOR_VERSION,
                         "status": "open", "src": e.get("src")})
    pn = P.build(HERE)
    scorer = Scorer(pn)
    by_m = defaultdict(list)
    for r in rows:
        by_m[r["date"][:7]].append(r)
    n_sc = 0
    for ym, rs in sorted(by_m.items()):
        sc = [x for r in rs for x in scorer.score(r, nowiso=nowiso)]
        write_gz(out / "events" / f"{ym}.jsonl.gz", rs)
        write_gz(out / "scores" / f"{ym}.jsonl.gz", sc)
        n_sc += len(sc)
    log(f"既存のシグナル台帳を取り込み: {len(rows)}件（{min(days) if days else '-'}〜{max(days) if days else '-'}）・答え {n_sc}行")
    for k, v in sorted(Counter(r["event_type"] for r in rows).items()):
        log(f"  {k}: {v}")
    return 0


def old_n225(start):
    """株価の表より前の温度計の段階変化を測るための日経平均と 1306.T（yfinance・調整なし）"""
    try:
        import yfinance as yf
        out = {}
        for t in (N225, "1306.T"):
            df = yf.download(t, start=(date.fromisoformat(start) - timedelta(days=10)).isoformat(), progress=False,
                             auto_adjust=False, threads=False)
            if hasattr(df.columns, "levels"):
                df.columns = df.columns.get_level_values(0)
            out[t] = {ix.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]))
                      for ix, r in df.dropna(subset=["Open", "Close"]).iterrows() if r["Open"] > 0}
        log(f"  古い期間の日経平均 {len(out[N225])}日・1306 {len(out['1306.T'])}日（yfinance）")
        return out
    except Exception as e:
        log(f"::warning::古い期間の日経平均が取れない: {e}")
        return {}


def score_old_mkt(e, px, nowiso):
    """score() と同じ定義を、yfinance の日経平均・1306 で（株価の表より前の温度計の段階変化だけ）"""
    ser, tser = px.get(N225) or {}, px.get("1306.T") or {}
    b = e["base_date"]
    days = bizdays_from(b)
    if b not in ser or b not in tser:
        return [{"event_id": e["event_id"], "date": e["date"], "status": "invalid", "reason": "起点の日の日経平均が無い",
                 "src": "yfinance", "scored_at": nowiso}]
    rows = []
    o, to = ser[b][0], tser[b][0]
    for h in H:
        end = days[h]
        if end not in ser or end not in tser:
            continue
        raw = (ser[end][3] / o - 1) * 100
        traw = (tser[end][3] / to - 1) * 100
        row = {"event_id": e["event_id"], "date": e["date"], "h": h, "base_date": b, "base_open": round(o, 4),
               "end_date": end, "end_close": round(ser[end][3], 4), "raw_ret": round(raw, 3), "topix_ret": round(traw, 3),
               "ret": round(raw - traw, 3), "status": "scored" if h == max(H) else "open", "src": "yfinance",
               "bench": "^N225"}
        if nowiso:
            row["scored_at"] = nowiso
        if h == 20:
            lows = [ser[d][2] for d in days[:21] if d in ser]
            row["max_dd_20"] = round((min(lows) / o - 1) * 100, 3) if lows else None
        rows.append(row)
    return rows


def write_baseline(pn, path):
    """参考の物差し: 出来事に関係なく「全銘柄」を同じ起点の日から測った対 TOPIX の超過の平均（等ウエイト）。
    {h: {起点の日: [平均, 銘柄数]}}。小型株が TOPIX に負けた期間は、どの出来事も対 TOPIX でマイナスに出やすいので、
    その分を見分けるために集計（yochou.py）で並べて出す。起点・終点・値の飛びの扱いは Scorer と同じ"""
    import numpy as np
    j = pn.cpos.get(TOPIX)
    if j is None:
        return
    out = {}
    for h in H:
        res = {}
        for i, b in enumerate(pn.dates):
            days = bizdays_from(b)
            e = pn.pos.get(days[h])
            if e is None or not np.isfinite(pn.O[i, j]) or not np.isfinite(pn.C[e, j]):
                continue
            t = (pn.C[e, j] / pn.O[i, j] - 1) * 100
            c = pn.C[i:e + 1]
            import warnings
            with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                r = (pn.C[e] / pn.O[i] - 1) * 100
                jump = np.nanmax(np.abs(np.log(c[1:] / c[:-1])), axis=0) if e > i else np.zeros(len(r))
            ok = np.isfinite(r) & ~(jump > np.log(JUMP_HI))
            if ok.sum() < 500:
                continue
            res[b] = [round(float((r[ok] - t).mean()), 3), int(ok.sum())]
        out[str(h)] = res
    write_gz(path, [{"note": "全銘柄（等ウエイト）の対TOPIX超過の平均。キーは起点の日", "by_h": out}])
    log(f"参考の物差し（全銘柄の平均）: {path.name}")


def late(out, nowiso):
    """遡及分の、まだ埋まっていない窓を足す（scores/late-YYYY-MM.jsonl に追記のみ）。毎営業日"""
    ev, sc = load_backfill(out)
    if not ev:
        log("遡及分がまだ無い")
        return 0
    done = {(r["event_id"], r["h"]) for r in sc if r.get("h") is not None}
    closed = {r["event_id"] for r in sc if r.get("status") == "invalid"}
    todo = [e for e in ev if e["event_id"] not in closed and any((e["event_id"], h) not in done for h in H)]
    if not todo:
        log("遡及分の窓はすべて埋まっている")
        return 0
    since = (date.fromisoformat(min(e["base_date"] for e in todo)) - timedelta(days=10)).isoformat()
    pn = P.build(HERE, since=since)
    scorer = Scorer(pn)
    by_month = defaultdict(list)
    for e in todo:
        for r in scorer.score(e, done, nowiso):
            if r.get("status") == "invalid" and r["reason"].startswith("起点の日の株価"):
                continue                         # 表を短く切ったせいで起点が無いだけ（もとの答えは出ている）
            by_month[e["date"][:7]].append(r)
    n = 0
    for ym, rows in sorted(by_month.items()):
        with open(Path(out) / "scores" / f"late-{ym}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
                n += 1
    log(f"遡及分の窓を追記: {n}行（候補 {len(todo)}件・表 {pn.dates[0]}〜{pn.dates[-1]}）")
    return 0


def compare(d, data):
    """同じ検出関数の証明: d を遡及の読み口で検出し、当日記録（data/ledger2/events）の同じ日と比べる"""
    import ledger2 as LG
    live = [r for r in L.jsonl(HERE / "data" / "ledger2" / "events" / f"{d[:7]}.jsonl") if r["date"] == d]
    bf = LG.detect_all(d, data, "backfill")
    lk = {(r["event_type"], r["code"]) for r in live}
    bk = {(r["event_type"], r["code"]) for r in bf}
    types = sorted({t for t, _ in lk | bk})
    log(f"{d}: 当日記録 {len(lk)}件 / 遡及の読み口 {len(bk)}件")
    res = {}
    for t in types:
        a_, b_ = {c for tt, c in lk if tt == t}, {c for tt, c in bk if tt == t}
        res[t] = {"live": len(a_), "backfill": len(b_), "both": len(a_ & b_),
                  "only_live": sorted(a_ - b_)[:10], "only_backfill": sorted(b_ - a_)[:10]}
        log(f"  {t:24s} 当日 {len(a_):4d} / 遡及 {len(b_):4d} / 一致 {len(a_ & b_):4d}"
            + (f"  当日だけ {sorted(a_ - b_)[:6]}" if a_ - b_ else "") + (f"  遡及だけ {sorted(b_ - a_)[:6]}" if b_ - a_ else ""))
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
