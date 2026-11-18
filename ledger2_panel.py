# -*- coding: utf-8 -*-
"""出来事の台帳（v2）: 全銘柄の日足を1つの表にする窓口（遡及＝段階3 と、日足の履歴を使う検出関数用・2026-11-18）

■ 何をするか
  data/bars_backfill/（過去1年・実行日時点の分割調整済み）・.cache/px5y/（過去5年・同じく調整済み）・
  data/bars/（当日記録・その日の夕方に見えた値）を読み、日付×銘柄の表を作る。
  その表から、当日記録が読んでいるファイルと**同じ形**の値を作る:
    prices_like(d)  … docs/prices.json の items と同じ欄（p・c・c5・c20・hi52・g25・vr・v）。計算は movers_daily.metrics と同じ
    stop_events(d)  … docs/stophigh_hist の ev 行と同じ欄。判定は stophigh.classify（制限値幅・呼値の表）をそのまま呼ぶ
    raw_bar(d)      … data/bars/ の1日分と同じ形（その日に見えていた値＝分割を戻した値）
    hist_stats(d)   … 52週安値・年初来高値／安値の判定に使う値（当日記録の検出関数も同じ関数を通る）
  検出関数（ledger2_detect.py）はこの値を Sources 経由で受け取るだけなので、当日と遡及で同じ関数が動く。

■ 2つの物差し
  調整済み（A）: 最後の日の株数に合わせた値。騰落・出来高の倍率・52週高値・答え合わせはこちら（Yahoo の値と同じ考え方）
  その日の値（R）: 分割を戻した、その日に市場で見えていた値。ストップ高・安（制限値幅は値段で決まる）と窓開けはこちら
  分割の情報: docs/splits.json（by_code・upcoming・recent）＋ .cache/px5y の Stock Splits。

■ 注意（出力のメタ情報にも書く）
  - 遡及の株価は「取った日の時点で Yahoo が見せている値」。上場廃止・統合で今いない銘柄は入らない（生き残りの偏り）。
  - 制限値幅の拡大（JPX のマーケットニュース）は過去分を持っていないので、遡及では「範囲外」として判定から外れる。
"""
import csv
import gzip
import json
from datetime import date as _date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
BARS_BACKFILL_ASOF = "2026-11-18"     # data/bars_backfill を取った日（bars-backfill.yml・その日の夕方）
WIN52 = "366D"                        # 52週＝その日から1年前の同じ日まで（movers_daily は period="1y" で取った全部の高値を見る＝同じ範囲）
MIN_ROWS52 = 240                      # これより履歴が短い銘柄は52週の判定をしない（上場直後の「高値」を数えない）
MIN_YTD = 20                          # 年初来の判定は、その年の取引日が20日以上ある日だけ
NEW_LISTING_GAP = 20                  # 表の最初の日から20営業日より後に初めて足がある銘柄＝表の途中で上場（履歴が全部ある）とみなす
                                      # （売買の少ない銘柄は最初の数日に足が無いことがあるので、1日遅れただけでは上場とみなさない）


def rd(x, n):
    return None if x is None or not np.isfinite(x) else round(float(x), n)


def _read_csv_gz(p, asof):
    df = pd.read_csv(p, dtype={"code": str}, usecols=["date", "code", "open", "high", "low", "close", "adj_close", "volume"])
    df["asof"] = asof if asof != "row" else df["date"]
    return df


def load_splits(docs, extra_dirs=()):
    """{code: [(ex, ratio)]}。splits.json を主に、px5y の Stock Splits で補う（同じ比率で5日以内なら同じ分割）"""
    out = {}
    try:
        s = json.loads((Path(docs) / "splits.json").read_text(encoding="utf-8"))
    except Exception:
        s = {}

    def add(code, ex, ratio):
        try:
            ratio = float(ratio)
        except (TypeError, ValueError):
            return
        if not ex or not ratio or ratio <= 0 or abs(ratio - 1) < 1e-9:
            return
        lst = out.setdefault(code, [])
        for e, r in lst:
            if abs(r - ratio) < 1e-6 and abs((_date.fromisoformat(e) - _date.fromisoformat(ex)).days) <= 5:
                return
        lst.append((ex, ratio))

    for c, evs in (s.get("by_code") or {}).items():
        for e in evs:
            add(c, e.get("ex"), e.get("ratio"))
    for key in ("upcoming", "recent"):
        for e in s.get(key) or []:
            add(str(e.get("code")), e.get("ex"), e.get("ratio"))
    for dd in extra_dirs:
        for f in sorted(Path(dd).glob("chunk_*.splits.json")):
            try:
                for c, evs in json.loads(f.read_text(encoding="utf-8")).items():
                    for e in evs:
                        add(c, e["ex"], e["ratio"])
            except Exception:
                continue
    for c in out:
        out[c].sort()
    return out


class Panel:
    """sources: [(ファイルの並び, asof)]。asof="row" はその日の値（data/bars）。後ろのものほど優先"""

    def __init__(self, sources, splits, t500=(), until=None, since=None):
        frames = []
        for files, asof in sources:
            for p in files:
                frames.append(_read_csv_gz(p, asof))
        if not frames:
            raise ValueError("日足が1つも無い")
        for i, f in enumerate(frames):
            f["prio"] = i
        df = pd.concat(frames, ignore_index=True)
        if until:
            df = df[df["date"] <= until]
        if since:
            df = df[df["date"] >= since]
        df = df.dropna(subset=["open", "close"])
        df = df[(df["open"] > 0) & (df["close"] > 0)]
        df = df.sort_values(["code", "date", "prio"]).drop_duplicates(["code", "date"], keep="last")
        df = df.reset_index(drop=True)
        # ── 分割: A=最後の日の株数へ（asof より後の権利落ちの分だけ割る）・R=その日の値へ（date<ex<=asof の分を掛け戻す）
        fa = np.ones(len(df))
        fr = np.ones(len(df))
        codes = df["code"].values
        dates = df["date"].values
        asofs = df["asof"].values
        last = df["date"].max()
        starts = np.flatnonzero(np.r_[True, codes[1:] != codes[:-1]])
        ends = np.r_[starts[1:], len(df)]
        for s0, e0 in zip(starts, ends):
            sp = splits.get(codes[s0])
            if not sp:
                continue
            dd, aa = dates[s0:e0], asofs[s0:e0]
            for ex, r in sp:
                if ex > last:
                    continue                         # まだ来ていない権利落ち（どの値も調整されていない）
                m_a = (dd < ex) & (aa < ex)          # まだ調整されていない（その日の値のまま）→ 最後の株数へ割る
                m_r = (dd < ex) & (aa >= ex)         # 取った日に調整済み → その日の値へ戻す
                fa[s0:e0][m_a] /= r
                fr[s0:e0][m_r] *= r
        # A = 元の値 × fa、R = 元の値 × fr。R と A の比 k = fr / fa（R = A × k）
        df["fa"], df["k"] = fa, fr / fa
        self.dates = sorted(df["date"].unique().tolist())
        self.pos = {d: i for i, d in enumerate(self.dates)}
        self.dti = pd.to_datetime(pd.Series(self.dates)).values
        self.codes = sorted(df["code"].unique().tolist())
        self.cpos = {c: j for j, c in enumerate(self.codes)}
        T, N = len(self.dates), len(self.codes)
        ri = df["date"].map(self.pos).values
        ci = df["code"].map(self.cpos).values

        def wide(vals):
            a = np.full((T, N), np.nan)
            a[ri, ci] = vals
            return a
        self.O = wide(df["open"].values * fa)
        self.H = wide(df["high"].fillna(df["close"]).values * fa)
        self.L = wide(df["low"].fillna(df["close"]).values * fa)
        self.C = wide(df["close"].values * fa)
        self.V = wide(df["volume"].fillna(0).values / fa)
        self.K = wide(df["k"].values)
        self.AR = wide((df["adj_close"] / df["close"]).values)      # 配当の調整係数（変われば配当落ちの日）
        self.dropped = self._drop_spikes(("1306", "^N225"))
        self.splits = splits
        self.split_set = {(c, ex) for c, lst in splits.items() for ex, _ in lst}
        self.t500 = set(t500)
        self.coverage = np.isfinite(self.C).sum(axis=1)
        self._m = None
        self._h = None

    def _drop_spikes(self, codes, far=0.4, near=0.1, max_len=3):
        """配信の桁とび（例: 1306.T の 2026-03-30・31 が 1/10 の 37円台）を捨てる（相場ボード #889 の drop_spikes と同じ考え方）。
        前後の値がそろっている（near 以内）のに、間の1〜3日だけ far 以上離れた点。物差し（TOPIX・日経平均）だけに当てる
        （個別銘柄は本当に動くことがあるので、答え合わせの「値の飛び」で invalid にする）"""
        out = []
        for code in codes:
            j = self.cpos.get(code)
            if j is None:
                continue
            idx = np.flatnonzero(np.isfinite(self.C[:, j]))
            c = self.C[idx, j]
            i = 1
            while i < len(c) - 1:
                if abs(c[i] / c[i - 1] - 1) >= far:
                    for k in range(1, max_len + 1):
                        if i + k < len(c) and abs(c[i + k] / c[i - 1] - 1) <= near and \
                                all(abs(c[i + m] / c[i - 1] - 1) >= far for m in range(k)):
                            for m in range(k):
                                r = idx[i + m]
                                out.append((code, self.dates[r]))
                                for A in (self.O, self.H, self.L, self.C, self.V):
                                    A[r, j] = np.nan
                            i += k
                            break
                i += 1
        return out

    # ─────────────────────────── その日の表
    def has_day(self, d, min_ratio=0.5):
        i = self.pos.get(d)
        return i is not None and self.coverage[i] >= min_ratio * max(1, np.nanmedian(self.coverage))

    def series(self, code):
        """答え合わせ用（調整済み A）: {date: (open, high, low, close)}"""
        j = self.cpos.get(code)
        if j is None:
            return {}
        out = {}
        for i in np.flatnonzero(np.isfinite(self.C[:, j])):
            out[self.dates[i]] = (self.O[i, j], self.H[i, j], self.L[i, j], self.C[i, j])
        return out

    def raw_bar(self, d):
        """data/bars の1日分と同じ形（その日に見えていた値 R）"""
        i = self.pos.get(d)
        if i is None:
            return None
        out = {}
        for j in np.flatnonzero(np.isfinite(self.C[i])):
            k = self.K[i, j]
            out[self.codes[j]] = {"open": self.O[i, j] * k, "high": self.H[i, j] * k, "low": self.L[i, j] * k,
                                  "close": self.C[i, j] * k, "adj_close": None, "volume": self.V[i, j] / k}
        return out

    # ─────────────────────────── prices.json と同じ欄（movers_daily.metrics と同じ計算）
    def _metrics(self):
        if self._m is not None:
            return self._m
        T, N = self.C.shape
        names = ("p", "c", "c5", "c20", "hi52", "g25", "vr", "v", "n")
        M = {k: np.full((T, N), np.nan, dtype=np.float64) for k in names}
        for j in range(N):
            idx = np.flatnonzero(np.isfinite(self.C[:, j]))
            if len(idx) < 2:
                continue
            c = pd.Series(self.C[idx, j])
            h = pd.Series(self.H[idx, j])
            v = pd.Series(self.V[idx, j]).fillna(0)
            M["p"][idx, j] = c.values
            for k, back in (("c", 1), ("c5", 5), ("c20", 20)):
                M[k][idx, j] = (c / c.shift(back) - 1).values * 100
            # 出来高の倍率: 前20本（当日を含まない）の出来高 >0 の平均。5本以上あるときだけ
            pv = v.where(v > 0).shift(1)
            s = pv.rolling(20, min_periods=1).sum()
            n = pv.rolling(20, min_periods=1).count()
            mean = s / n
            M["vr"][idx, j] = np.where((n >= 5) & (mean > 0), v / mean, np.nan)
            ti = pd.DatetimeIndex(self.dti[idx])
            hi = pd.Series(h.values, index=ti).rolling(WIN52, min_periods=1).max().values
            M["hi52"][idx, j] = np.where(hi > 0, (c / hi - 1) * 100, np.nan)
            M["g25"][idx, j] = (c / c.rolling(25, min_periods=25).mean() - 1).values * 100
            M["v"][idx, j] = (c * v / 1e6).values
            # 履歴の本数。表の最初の日から足がある銘柄は、本当の履歴がもっと長いかもしれないので本数で数える。
            # 表の途中で上場した銘柄は履歴が全部あるので、短くても判定する（movers の1年分の取得と同じ扱い）
            M["n"][idx, j] = np.arange(1, len(idx) + 1) + (10 ** 6 if idx[0] >= NEW_LISTING_GAP else 0)
        self._m = M
        return M

    def prices_like(self, d):
        """docs/prices.json と同じ形 {"trade_date": d, "items": {code: {...}}}。
        52週（hi52）は履歴が MIN_ROWS52 本以上ある銘柄か、表の途中で上場した銘柄だけ
        （表の最初の日から足がある銘柄は、それより前の高値が見えていないので、短い履歴の「高値」を出さない）"""
        i = self.pos.get(d)
        if i is None or not self.has_day(d):
            return None
        M = self._metrics()
        items = {}
        for j in np.flatnonzero(np.isfinite(M["p"][i])):
            n = M["n"][i, j]
            items[self.codes[j]] = {
                "p": rd(M["p"][i, j], 1), "c": rd(M["c"][i, j], 2), "c5": rd(M["c5"][i, j], 1),
                "c20": rd(M["c20"][i, j], 1),
                "hi52": rd(M["hi52"][i, j], 1) if n >= MIN_ROWS52 else None,
                "g25": rd(M["g25"][i, j], 1), "vr": rd(M["vr"][i, j], 1), "v": rd(M["v"][i, j], 0)}
        return {"trade_date": d, "items": items, "synthetic": True}

    # ─────────────────────────── 52週安値・年初来（当日記録と遡及の両方が通る）
    def _hist(self):
        if self._h is not None:
            return self._h
        T, N = self.C.shape
        years = np.array([d[:4] for d in self.dates])
        # 表の最初の日が、その年の最初の営業日の近く（1月10日まで）でなければ、その年の年初来は数えない
        first_year_partial = self.dates[0][:4] if self.dates[0][5:] > "01-10" else None
        H = {k: np.full((T, N), np.nan) for k in ("lo52", "ytd_hi", "ytd_lo", "n", "ytd_n")}
        for j in range(N):
            idx = np.flatnonzero(np.isfinite(self.C[:, j]))
            if len(idx) < 2:
                continue
            c = pd.Series(self.C[idx, j])
            h = pd.Series(self.H[idx, j])
            l = pd.Series(self.L[idx, j])
            ti = pd.DatetimeIndex(self.dti[idx])
            lo = pd.Series(l.values, index=ti).rolling(WIN52, min_periods=1).min().values
            H["lo52"][idx, j] = np.where(lo > 0, (c / lo - 1) * 100, np.nan)
            H["n"][idx, j] = np.arange(1, len(idx) + 1) + (10 ** 6 if idx[0] >= NEW_LISTING_GAP else 0)   # 表の途中で上場＝履歴は全部ある
            y = pd.Series(years[idx])
            yh = h.groupby(y.values).cummax()
            yl = l.groupby(y.values).cummin()
            H["ytd_hi"][idx, j] = (c / yh - 1).values * 100
            H["ytd_lo"][idx, j] = (c / yl - 1).values * 100
            yn = y.groupby(y.values).cumcount().values + 1
            # 表の最初の年は、年の初めから足がそろっていない（表が年の途中から始まる）ので数えない。
            # ただし表の途中で上場した銘柄は、上場した年も履歴が全部ある
            if idx[0] < NEW_LISTING_GAP:
                yn = np.where(y.values == first_year_partial, 0, yn) if first_year_partial else yn
            H["ytd_n"][idx, j] = yn
        self._h = H
        return H

    def hist_stats(self, d):
        """{code: {"lo52": %, "ytd_hi": %, "ytd_lo": %, "n": 履歴の本数, "ytd_n": その年の本数}}（小数1桁で丸めた値）"""
        i = self.pos.get(d)
        if i is None or not self.has_day(d):
            return None
        H = self._hist()
        out = {}
        for j in np.flatnonzero(np.isfinite(H["n"][i])):
            out[self.codes[j]] = {"lo52": rd(H["lo52"][i, j], 1), "ytd_hi": rd(H["ytd_hi"][i, j], 1),
                                  "ytd_lo": rd(H["ytd_lo"][i, j], 1), "n": int(H["n"][i, j]) % 10 ** 6,
                                  "full": bool(H["n"][i, j] >= 10 ** 6 or H["n"][i, j] >= MIN_ROWS52), "ytd_n": int(H["ytd_n"][i, j])}
        return out

    # ─────────────────────────── stophigh_hist の ev 行と同じ形（stophigh.py の判定をそのまま呼ぶ）
    def stop_events(self, d):
        import stophigh as S
        i = self.pos.get(d)
        if i is None or i == 0 or not self.has_day(d):
            return []
        pd_ = self.dates[i - 1]
        out = []
        # その日の値 R で比べる。制限値幅は基準値段の14%以上なので、高値・安値が前日終値から12%未満の銘柄は判定しない（速さのため）
        k0, k1 = self.K[i - 1], self.K[i]
        pc = self.C[i - 1] * k0
        h, l = self.H[i] * k1, self.L[i] * k1
        with np.errstate(invalid="ignore", divide="ignore"):
            cand = np.flatnonzero(np.isfinite(pc) & np.isfinite(h) & ((h / pc - 1 >= 0.12) | (l / pc - 1 <= -0.12)))
        for j in cand:
            code = self.codes[j]
            o, c = self.O[i, j] * k1[j], self.C[i, j] * k1[j]
            v, pv = self.V[i, j] / k1[j], self.V[i - 1, j] / k0[j]
            a0, a1 = self.AR[i - 1, j], self.AR[i, j]
            if np.isfinite(a0) and np.isfinite(a1) and a1 > 0 and abs(a0 / a1 - 1) > 1e-5:
                continue                          # 配当落ちの日（stophigh.py と同じく判定しない）
            # 丸め: その日の値は元データ（小数2桁）に戻す
            o, hh, ll, c, base = (round(float(x), 2) for x in (o, h[j], l[j], c, pc[j]))
            kind, up, lo = S.classify(base, o, hh, ll, c, code in self.t500)
            if kind in (None, "out"):
                continue
            if (code, d) in self.split_set or not (v > 0) or not (pv > 0):
                continue
            vols = self.V[max(0, i - 20):i, j]
            vols = vols[np.isfinite(vols) & (vols > 0)]
            vr = round(float(self.V[i, j] / vols.mean()), 1) if len(vols) >= 5 else None
            out.append({"t": "ev", "d": d, "c": code, "k": kind, "base": base, "up": up, "lo": lo,
                        "o": o, "h": hh, "l": ll, "cl": c, "chg": S.pct(c, base), "vr": vr,
                        "val": round(c * v / 1e6), "t500": code in self.t500, "synthetic": True})
        return out


def t500_from_universe(root=HERE):
    s = set()
    try:
        with open(Path(root) / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if (r.get("scale") or "").strip() in ("TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"):
                    s.add(r["code"].strip())
    except FileNotFoundError:
        pass
    return s


def default_sources(root=HERE, px5y=None, with_daily=True):
    """[(files, asof)]: bars_backfill → px5y（あれば・同じ日は px5y を使う＝5年を1つの取り口に）→ data/bars（当日記録・最優先）"""
    root = Path(root)
    src = []
    bb = sorted((root / "data" / "bars_backfill").glob("*.csv.gz"))
    if bb:
        src.append((bb, BARS_BACKFILL_ASOF))
    if px5y and Path(px5y).exists():
        meta = {}
        try:
            meta = json.loads((Path(px5y) / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        # 束ごとに取った日（分割調整の基準日）が違うことがある（途中で止まって別の日に続きを取ったとき）
        chunks = meta.get("chunks") or {}
        for f in sorted(Path(px5y).glob("chunk_*.csv.gz")):
            src.append(([f], chunks.get(f.name.split(".")[0]) or meta.get("adjusted_asof") or BARS_BACKFILL_ASOF))
    if with_daily:
        src.append((sorted((root / "data" / "bars").glob("*/*.csv.gz")), "row"))
    return src


def build(root=HERE, px5y=None, with_daily=True, until=None, since=None):
    root = Path(root)
    return Panel(default_sources(root, px5y, with_daily), load_splits(root / "docs", [px5y] if px5y else []),
                 t500_from_universe(root), until=until, since=since)
