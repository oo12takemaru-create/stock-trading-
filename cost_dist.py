# -*- coding: utf-8 -*-
"""含み損益の推定（しこりの壁）: 全銘柄の「今も持たれている株の買値の分布」を日足と出来高から推定する（2026-11-27）

指示書: kaburadar\\_報告_含み損益の地図_段階0と1.md（段階0・1）と統括の「決まった形」（段階2）
サイトに出すのは2つだけ: ①ヒートマップの色の1つ（推定の含み益の人が多い／少ない） ②銘柄ページの「しこりの壁」1枚。
ランキングは作らない（段階1の検証で、先行きの手掛かりにならなかった）。

■ 推定の式（中国の株アプリの「筹码分布」と同じ形・方法として新しいものではない）
  価格を対数で 0.5% 刻みの帯に分け、銘柄ごとに
      D_t = D_{t-1}·(1 − a_t) + a_t·K_t ,   a_t = α·min(出来高_t ÷ 分母, 上限 RCAP)
  K_t は その日の安値〜高値の三角形（頂点 = (高値+安値+終値)/3。売買代金は使わない＝段階0で誤差1ポイント未満）。
  - 回転率の上限は 0.25/日（指示書の1から下げた。デイトレードの往復は持ち主の入れ替わりではないため）。
  - 開始の分布: 5年の窓の初日から日足がある銘柄は「最初の250営業日の価格帯別出来高」で始め、251日目から式を回す
    （親子上場・持ち合いの銘柄で「最初の日に均等」が作る、にせの壁を消すため）。窓の途中で上場した銘柄は上場日の K で始める。
  - 数字は幅で出す: α ∈ {0.3, 0.5, 1.0} × 分母 ∈ {発行済−自己株, 推定の浮動株} の6通りの最小〜最大。真ん中の値は α=0.5・発行済−自己株。
  - 確かさ: 開始の分布が今も推定に残っている割合（resid）と幅で「高い／ふつう／低い」。上場から60営業日未満は「データ不足」。

■ 分母
  発行済−自己株 = 有報の 親会社株主に帰属する当期純利益 ÷ 1株当たり当期純利益（docs/yuho/latest.json・期中平均の株数）。
  純資産 ÷ 1株当たり純資産 と15%以上違えば yfinance の発行済（tenbagger_shares.csv）に近い方。有報より後の分割は splits.json で掛ける。
  推定の浮動株 = 発行済−自己株 × (1 − 5%以上の大量保有のうち運用会社・証券・信託銀行を除いた保有割合の合計)（docs/holders.json）。
  大量保有は5%以上しか見えないので、浮動株は多めに出る（＝推定の幅は狭めに出る）。

■ 分割
  帯は対数の等間隔なので、分割は「帯の値札を比率で割る」だけで、5年をさかのぼって計算し直したのと同じになる
  （出来高と分母が同じ比率で変わるので回転率は変わらない）。毎日は splits.json の権利落ち日にその銘柄の値札をずらす。
  分割の記録が無いのに1日で2.5倍・0.4倍に飛んだ銘柄は、その日を使わない（px_sanity と同じ物差し）。

■ 毎日の動き（movers-daily.yml の Daily bars のあと）
  data/cost/state.npz（控え・全銘柄×6通りの分布）を読み、それより後の data/bars/ の日を1日ずつ進める。
  控え（data/cost/state.npz）は「月の最初の取引日」、銘柄ごとの公開ファイル（docs/cost/{コード}.json）は「週の最初の取引日」
  だけ書き換える（git の履歴を膨らませない）。毎日は控えから最大1か月ぶんの data/bars を進め直す（1日1秒弱）。
  毎日書くのは docs/cost_map.json（全銘柄の今日の数字・1ファイル）と data/cost/days/YYYY/YYYY-MM-DD.csv.gz（その日の指標）。

■ 出力
  data/cost/state.npz                 控え（月1回・約3.4MB）。分布は対数で255段（1段 約6.5%）に丸め、帯の端の0は切って詰める
  data/cost/hist/YYYY-MM.csv.gz       初回にまとめて作った過去の指標（銘柄×週の最後の取引日・銘柄順）。
                                      日ごとの過去が要るときは --init を手元で流し直せば同じものができる（約16分）
  data/cost/days/YYYY/YYYY-MM-DD.csv.gz  それ以後の日の指標（1日1ファイル・書いたら書き換えない）
  docs/cost_map.json                  公開・毎日。ヒートマップの色と銘柄ページの今日の数字
  docs/cost/{コード}.json ・ index.json   公開・週1回。しこりの壁（1%帯の割合）・指標・幅・確かさ・平均買値の週次1年
  生の日足（四本値・出来高の並び）と売買代金は公開ファイルに入れない。

使い方:
  python -X utf8 cost_dist.py --init --px5y <yochou_px5y.py の出力>   # 初回（全銘柄×5年・手元で）
  python -X utf8 cost_dist.py                                          # 毎日（ジョブの中）
  python -X utf8 cost_dist.py --root <dir>                             # 試験用
"""
import argparse
import csv
import gzip
import io
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent

STEP = math.log(1.005)          # 帯の幅（対数）
NB = 1024                       # 帯の数（1.005^1024 ≒ 166倍の値幅）
MARGIN = 16                     # 値札の端の余白（帯）
ALPHAS = (0.3, 0.5, 1.0)
DENOMS = ("S", "F")             # S = 発行済−自己株、F = 推定の浮動株
CFGS = [(a, d) for d in DENOMS for a in ALPHAS]
BASE = CFGS.index((0.5, "S"))
RCAP = 0.25                     # 1日の回転率の上限
WARM = 250                      # 開始の分布に使う日数（最初の1年）
MIN_DAYS = 60                   # これ未満は「データ不足」
WALL_W = 5                      # 最大のしこりの窓（5帯 ≒ 2.5%）
WALL_RANGE = 0.30               # 最大のしこりは終値から +30% まで
OVH_RANGE = 0.10                # 上値しこり量は終値〜+10%
JUMP = 2.5                      # 分割の記録が無い1日の飛び（px_sanity と同じ）
JUMP_MIN_PRICE = 50.0
CONF_LOW = (0.30, 0.30)         # resid ≥ 0.30 か 幅 > 30ポイント → 低い
CONF_MID = (0.10, 0.15)         # resid ≥ 0.10 か 幅 > 15ポイント → ふつう
CONF_NAMES = {"H": "高い", "M": "ふつう", "L": "低い", "N": "データ不足"}
LABEL = "推定"
NOTE = ("日足と出来高から計算した推定です。実際の保有者の買値ではありません。"
        "数字の幅は、計算の決め事（入れ替わりの速さ・浮動株の見積もり）を変えたときの最小〜最大です。")
HIST_COLS = ("date", "code", "c", "pr", "pr_lo", "pr_hi", "avg", "avg_lo", "avg_hi", "dev", "ovh",
             "wall_lo", "wall_hi", "wall_s", "far_p", "far_s", "q05", "q95", "resid", "conf")
INSTITUTION_WORDS = ("アセット", "投信", "投資信託", "投資顧問", "証券", "信託", "キャピタル", "インベストメント",
                     "マネジメント", "ファンド", "パートナーズ", "アドバイザ", "ブラックロック", "バンガード", "フィデリティ",
                     "モルガン", "ゴールドマン", "シュローダー", "ウエリントン", "ノムラ", "capital", "investment",
                     "management", "fund", "partners", "advisor", "securities", "blackrock", "vanguard", "fidelity",
                     "morgan", "goldman", "nomura", "schroder", "wellington", "asset")
R_CENTER = np.exp((np.arange(NB) + 0.5) * STEP).astype(np.float64)   # 帯の中心 = exp(g0) × R_CENTER


def log(*a):
    print(*a, flush=True)


def sig(x, n=4):
    """有効数字 n 桁の数（JSON・CSV を短く）"""
    if x is None or not np.isfinite(x) or x <= 0:
        return None
    d = n - 1 - int(math.floor(math.log10(abs(x))))
    v = round(float(x), d)
    return int(v) if v == int(v) else v


def week_key(d):
    y, w, _ = date.fromisoformat(d).isocalendar()
    return (y, w)


# ──────────────────────────────────────── 分母（発行済−自己株・推定の浮動株）
def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def split_table(root):
    """{code: [(ex, ratio)]}（splits.json の by_code・比率>0 のもの）"""
    out = {}
    for c, lst in (load_json(root / "docs" / "splits.json").get("by_code") or {}).items():
        for s in lst or []:
            try:
                ex, r = s.get("ex"), float(s.get("ratio") or 0)
            except (TypeError, ValueError):
                continue
            if ex and r > 0:
                out.setdefault(c, []).append((ex, r))
    return out


def load_denoms(root, codes, asof, splits):
    """S（発行済−自己株）と F（浮動株の割合）を codes の並びで返す。src は出どころの件数"""
    yuho = (load_json(root / "docs" / "yuho" / "latest.json").get("codes") or {})
    yf = {}
    try:
        with open(root / "tenbagger_shares.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    v = float(r.get("shares") or "nan")
                except ValueError:
                    continue
                if v > 0:
                    yf[(r.get("ticker") or "").replace(".T", "")] = v
    except OSError:
        pass
    hold = (load_json(root / "docs" / "holders.json").get("by_issuer") or {})
    S = np.full(len(codes), np.nan)
    F = np.ones(len(codes))
    src = {"有報 利益÷EPS": 0, "有報 純資産÷BPS": 0, "yfinance 発行済": 0, "なし": 0}
    for j, c in enumerate(codes):
        y = yuho.get(c) or {}
        fy = (y.get("fy") or [{}])[-1]
        c1 = c2 = None
        try:
            if fy.get("eps") and abs(fy["eps"]) >= 0.01 and fy.get("ni"):
                c1 = fy["ni"] / fy["eps"]
                c1 = c1 if c1 > 0 else None
            if fy.get("bps") and fy["bps"] > 0 and fy.get("na") and fy["na"] > 0:
                c2 = fy["na"] / fy["bps"]
        except (TypeError, ZeroDivisionError):
            pass
        y0 = yf.get(c)
        s, kind = None, None
        if c1 and c2:
            if abs(c1 / c2 - 1) < 0.15 or not y0:
                s, kind = c1, "有報 利益÷EPS"
            else:
                s, kind = (c1, "有報 利益÷EPS") if abs(math.log(c1 / y0)) <= abs(math.log(c2 / y0)) else (c2, "有報 純資産÷BPS")
        elif c1 or c2:
            s, kind = (c1, "有報 利益÷EPS") if c1 else (c2, "有報 純資産÷BPS")
        if s:
            sub = y.get("sub") or "0000"
            for ex, r in splits.get(c, []):
                if sub < ex <= asof:
                    s *= r
        elif y0:
            s, kind = y0, "yfinance 発行済"
        src[kind or "なし"] += 1
        if s:
            S[j] = s
        fixed = 0.0
        for h in hold.get(c) or []:
            nm = (h.get("name") or "").lower()
            if any(w in nm for w in INSTITUTION_WORDS):
                continue
            try:
                fixed += float(h.get("ratio_own") or 0) / 100.0
            except (TypeError, ValueError):
                pass
        F[j] = min(1.0, max(0.2, 1.0 - fixed))
    return S, F, src


QMIN = 7.0    # 1e-7 より小さい帯は0


def quantize(x):
    q = np.round((np.log10(np.maximum(x, 1e-12)) + QMIN) / QMIN * 254) + 1
    return np.where(x >= 10 ** -QMIN, np.clip(q, 1, 255), 0).astype(np.uint8)


def dequantize(q):
    return np.where(q > 0, 10.0 ** ((q.astype(np.float64) - 1) / 254 * QMIN - QMIN), 0.0).astype(np.float32)


# ──────────────────────────────────────── エンジン
class Engine:
    """全銘柄の分布を持つ。D: (6通り, 銘柄, 帯) float32"""

    def __init__(self, codes):
        self.codes = list(codes)
        n = len(self.codes)
        self.idx = {c: i for i, c in enumerate(self.codes)}
        self.g0 = np.full(n, np.nan)
        self.D = np.zeros((len(CFGS), n, NB), dtype=np.float32)
        self.acc = None                          # 開始の分布を作っている途中（初回だけ）
        self.mode = np.zeros(n, dtype=np.int8)   # 0=まだ 1=開始の分布を作り中 2=回っている
        self.nvalid = np.zeros(n, dtype=np.int32)
        self.resid = np.ones((len(CFGS), n))
        self.last = np.full(n, np.nan)           # 最後の終値（その日の値札）
        self.date = ""
        self.skipped = {}                        # {code: 理由}（その回に使わなかった日）

    # 銘柄を足す（新規上場）
    def add(self, code):
        j = len(self.codes)
        self.codes.append(code)
        self.idx[code] = j
        self.g0 = np.append(self.g0, np.nan)
        self.D = np.concatenate([self.D, np.zeros((len(CFGS), 1, NB), dtype=np.float32)], axis=1)
        if self.acc is not None:
            self.acc = np.concatenate([self.acc, np.zeros((1, NB), dtype=np.float32)])
        self.mode = np.append(self.mode, np.int8(0))
        self.nvalid = np.append(self.nvalid, np.int32(0))
        self.resid = np.concatenate([self.resid, np.ones((len(CFGS), 1))], axis=1)
        self.last = np.append(self.last, np.nan)
        return j

    def split(self, code, ratio):
        """分割（ratio=2 で1株が2株）: 値札を ratio で割るだけ"""
        j = self.idx.get(code)
        if j is None or not np.isfinite(self.g0[j]):
            return
        self.g0[j] -= math.log(ratio)
        self.last[j] /= ratio

    def _shift(self, j, k):
        """値札を k 帯ずらす（k>0 で上へ）。はみ出た分は端の帯に寄せる"""
        if k == 0:
            return
        for arr in [self.D[:, j, :]] + ([self.acc[j:j + 1, :]] if self.acc is not None else []):
            if k > 0:
                lost = arr[:, :k + 1].sum(axis=1)
                arr[:, :NB - k] = arr[:, k:].copy()
                arr[:, NB - k:] = 0
                arr[:, 0] = lost if k < NB else 0
            else:
                m = -k
                lost = arr[:, NB - m - 1:].sum(axis=1)
                arr[:, m:] = arr[:, :NB - m].copy()
                arr[:, :m] = 0
                arr[:, NB - 1] = lost
        self.g0[j] += k * STEP

    def _fit_grid(self, J, lo, hi):
        """その日の安値〜高値が値札に収まるように（新しい銘柄は真ん中に置く）"""
        for n, j in enumerate(J):
            if not np.isfinite(self.g0[j]):
                self.g0[j] = math.log(math.sqrt(lo[n] * hi[n])) - NB / 2 * STEP
                continue
            li = (math.log(lo[n]) - self.g0[j]) / STEP
            hi_ = (math.log(hi[n]) - self.g0[j]) / STEP
            if hi_ > NB - 2:
                self._shift(j, int(math.ceil(hi_ - (NB - 1 - MARGIN))))
            elif li < 1:
                k = int(math.floor(li - MARGIN))
                # 上の端の重い帯を落とさない範囲で下げる
                self._shift(j, max(k, -(NB - 1)))

    def kernel(self, J, lo, hi, c):
        """その日の売買の分布 K（J の銘柄だけ・(len(J), NB)）"""
        g0 = self.g0[J]
        tp = np.clip((hi + lo + c) / 3.0, lo, hi)
        lo_i = np.floor((np.log(lo) - g0) / STEP).astype(np.int64)
        hi_i = np.floor((np.log(hi) - g0) / STEP).astype(np.int64)
        lo_i = np.clip(lo_i, 0, NB - 1)
        hi_i = np.clip(hi_i, 0, NB - 1)
        P = np.exp(g0)[:, None] * R_CENTER[None, :]
        pc = np.clip(P, lo[:, None], hi[:, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            left = np.where((tp > lo)[:, None], (pc - lo[:, None]) / (tp - lo)[:, None], 1.0)
            right = np.where((hi > tp)[:, None], (hi[:, None] - pc) / (hi - tp)[:, None], 1.0)
        v = np.where(pc <= tp[:, None], left, right)
        v = np.maximum(v, 1e-3)
        B = np.arange(NB)[None, :]
        v = v * ((B >= lo_i[:, None]) & (B <= hi_i[:, None]))
        s = v.sum(axis=1, keepdims=True)
        return (v / np.where(s > 0, s, 1.0)).astype(np.float32)

    def step(self, day, bars, S, F, splits_today=None, init_full=None):
        """1日進める。bars: {code: (open, high, low, close, volume)}。S・F は codes の並び（足りない分は NaN/1）
        splits_today: {code: ratio}（その日に権利落ち）。init_full: 初回だけ「窓の初日から日足がある」銘柄の集合"""
        for c, r in (splits_today or {}).items():
            self.split(c, r)
        J, lo, hi, cl, vo = [], [], [], [], []
        for c, (o, h, l, cc, v) in bars.items():
            if not (np.isfinite(h) and np.isfinite(l) and np.isfinite(cc) and l > 0 and np.isfinite(v) and v >= 0):
                continue
            j = self.idx.get(c)
            if j is None:
                j = self.add(c)
            h2, l2 = max(h, cc, l), min(l, cc, h)
            p = self.last[j]
            if np.isfinite(p) and p > 0:
                r = cc / p
                if (min(cc, p) >= JUMP_MIN_PRICE and (r >= JUMP or r <= 1 / JUMP)):
                    self.skipped[c] = f"{day} 前日比 {r:.2f}倍（分割の記録なし）"
                    continue
            J.append(j); lo.append(l2); hi.append(h2); cl.append(cc); vo.append(v)
        if not J:
            self.date = day
            return np.array([], dtype=np.int64)
        J = np.array(J); lo = np.array(lo, float); hi = np.array(hi, float); cl = np.array(cl, float); vo = np.array(vo, float)
        self._fit_grid(J, lo, hi)
        K = self.kernel(J, lo, hi, cl)
        mode = self.mode[J]
        # 1) まだ始まっていない銘柄
        new = mode == 0
        if new.any():
            full = np.array([init_full is not None and self.codes[j] in init_full for j in J])
            warm = new & full
            if warm.any():
                if self.acc is None:
                    self.acc = np.zeros((len(self.codes), NB), dtype=np.float32)
                self.mode[J[warm]] = 1
            ipo = new & ~full
            if ipo.any():
                for k in range(len(CFGS)):
                    self.D[k, J[ipo], :] = K[ipo]
                self.resid[:, J[ipo]] = 1.0
                self.mode[J[ipo]] = 2
        mode = self.mode[J]
        # 2) 開始の分布を作り中（初回だけ）: 出来高で重みをつけて足す
        w = mode == 1
        if w.any():
            self.acc[J[w]] += K[w] * vo[w][:, None].astype(np.float32)
            done = w & (self.nvalid[J] + 1 >= WARM)
            if done.any():
                self._finish_warm(J[done])
        # 3) 回っている銘柄（今日始まった銘柄は除く）
        run = (mode == 2) & ~new
        if run.any():
            Jr = J[run]
            for k, (a, d) in enumerate(CFGS):
                den = S[Jr] * (F[Jr] if d == "F" else 1.0)
                with np.errstate(divide="ignore", invalid="ignore"):
                    r = np.where(np.isfinite(den) & (den > 0), vo[run] / den, 0.0)
                aa = (a * np.minimum(r, RCAP)).astype(np.float32)
                self.D[k, Jr, :] *= (1.0 - aa)[:, None]
                self.D[k, Jr, :] += aa[:, None] * K[run]
                self.resid[k, Jr] *= (1.0 - aa)
        self.nvalid[J] += 1
        self.last[J] = cl
        self.date = day
        return J

    def _finish_warm(self, Jd):
        s = self.acc[Jd].sum(axis=1, keepdims=True)
        A = self.acc[Jd] / np.where(s > 0, s, 1.0)
        for k in range(len(CFGS)):
            self.D[k, Jd, :] = A
        self.resid[:, Jd] = 1.0
        self.mode[Jd] = 2
        self.acc[Jd] = 0

    def finish_init(self):
        """初回の終わり: 250日に満たないまま終わった銘柄も、そこまでの出来高で始めたことにする"""
        if self.acc is not None:
            Jd = np.flatnonzero(self.mode == 1)
            if len(Jd):
                self._finish_warm(Jd)
            self.acc = None

    # ── 指標
    def metrics(self, J):
        """J の銘柄（回っている・値がある）の指標。戻り {名前: 配列}"""
        J = np.asarray([j for j in J if self.mode[j] == 2 and np.isfinite(self.last[j])], dtype=np.int64)
        if not len(J):
            return J, {}
        c = self.last[J]
        P = np.exp(self.g0[J])[:, None] * R_CENTER[None, :]
        D = self.D[:, J, :]
        tot = D.sum(axis=2, dtype=np.float64)
        tot = np.where(tot > 0, tot, 1.0)
        below = (P < c[:, None]).astype(np.float32)
        pr = np.einsum("knb,nb->kn", D, below, dtype=np.float64) / tot          # (6, n)
        avg = np.einsum("knb,nb->kn", D, P.astype(np.float32), dtype=np.float64) / tot
        D0 = D[BASE].astype(np.float64) / tot[BASE][:, None]
        ovh = (D0 * ((P >= c[:, None]) & (P <= c[:, None] * (1 + OVH_RANGE)))).sum(axis=1)
        cs = np.cumsum(D0, axis=1)
        csp = np.concatenate([np.zeros((len(J), 1)), cs], axis=1)
        win = csp[:, WALL_W:] - csp[:, :-WALL_W]                # 帯 b..b+4 の合計（b=0..NB-5）
        mid = P[:, WALL_W // 2: WALL_W // 2 + win.shape[1]]
        inr = (mid > c[:, None]) & (mid <= c[:, None] * (1 + WALL_RANGE))
        w1 = np.where(inr, win, -1.0)
        b1 = w1.argmax(axis=1)
        s1 = w1[np.arange(len(J)), b1]
        far = np.where(mid > c[:, None] * (1 + WALL_RANGE), win, -1.0)
        b2 = far.argmax(axis=1)
        s2 = far[np.arange(len(J)), b2]
        e0 = np.exp(self.g0[J])
        wall_lo = np.where(s1 > 0, e0 * np.exp(b1 * STEP), np.nan)
        wall_hi = np.where(s1 > 0, e0 * np.exp((b1 + WALL_W) * STEP), np.nan)
        far_p = np.where(s2 > 0, mid[np.arange(len(J)), b2], np.nan)
        q05 = P[np.arange(len(J)), np.clip((cs < 0.05).sum(axis=1), 0, NB - 1)]
        q95 = P[np.arange(len(J)), np.clip((cs < 0.95).sum(axis=1), 0, NB - 1)]
        resid = self.resid[BASE, J]
        width = pr.max(axis=0) - pr.min(axis=0)
        conf = np.where((resid >= CONF_LOW[0]) | (width > CONF_LOW[1]), "L",
                        np.where((resid >= CONF_MID[0]) | (width > CONF_MID[1]), "M", "H"))
        conf = np.where(self.nvalid[J] < MIN_DAYS, "N", conf)
        return J, {"c": c, "pr": pr[BASE], "pr_lo": pr.min(axis=0), "pr_hi": pr.max(axis=0), "pr_all": pr,
                   "avg": avg[BASE], "avg_lo": avg.min(axis=0), "avg_hi": avg.max(axis=0),
                   "dev": c / avg[BASE] - 1.0, "ovh": ovh,
                   "wall_lo": wall_lo, "wall_hi": wall_hi, "wall_s": np.where(s1 > 0, s1, 0.0),
                   "far_p": far_p, "far_s": np.where(s2 > 0, s2, 0.0), "q05": q05, "q95": q95,
                   "resid": resid, "conf": conf}

    # ── 控え
    def save(self, path):
        """分布は対数で255段に丸める（1e-7〜1・1段 約6.5%。読み直すと銘柄ごとに合計1に戻す。含み益の割合の誤差は0.3ポイント未満）。
        帯は全6通りのどれかで 1e-7 を超える範囲だけ"""
        n = len(self.codes)
        start = np.zeros(n, dtype=np.int32)
        length = np.zeros(n, dtype=np.int32)
        parts = []
        for j in range(n):
            m = self.D[:, j, :].max(axis=0) > 1e-7
            if not m.any():
                continue
            b = np.flatnonzero(m)
            start[j], length[j] = b[0], b[-1] - b[0] + 1
            parts.append(self.D[:, j, b[0]:b[-1] + 1])
        flat = np.concatenate([p.reshape(len(CFGS), -1) for p in parts], axis=1) if parts else np.zeros((len(CFGS), 0), np.float32)
        flat = quantize(flat)
        path.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        np.savez_compressed(buf, codes=np.array(self.codes), g0=self.g0, mode=self.mode, nvalid=self.nvalid,
                            resid=self.resid.astype(np.float32), last=self.last, start=start, length=length, flat=flat,
                            date=np.array(self.date), cfgs=np.array([f"{a}/{d}" for a, d in CFGS]), rcap=np.array(RCAP))
        path.write_bytes(buf.getvalue())

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        cf = [str(x) for x in z["cfgs"]]
        if cf != [f"{a}/{d}" for a, d in CFGS]:
            raise SystemExit(f"控えの設定が違う: {cf}")
        e = cls([str(c) for c in z["codes"]])
        e.g0 = z["g0"].astype(float); e.mode = z["mode"].astype(np.int8); e.nvalid = z["nvalid"].astype(np.int32)
        e.resid = z["resid"].astype(float); e.last = z["last"].astype(float); e.date = str(z["date"])
        flat, start, length = z["flat"], z["start"], z["length"]
        o = 0
        for j in range(len(e.codes)):
            L = int(length[j])
            if L:
                e.D[:, j, start[j]:start[j] + L] = dequantize(flat[:, o:o + L])
                o += L
        tot = e.D.sum(axis=2, keepdims=True)
        e.D /= np.where(tot > 0, tot, 1.0)
        return e


# ──────────────────────────────────────── 日足の読み込み
def universe(root):
    out = {}
    try:
        with open(root / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                c = (r.get("code") or "").strip()
                if len(c) == 4:
                    out[c] = (r.get("name") or "").strip()
    except OSError:
        pass
    return out


def fnum(v):
    try:
        return float(v) if v not in (None, "") else float("nan")
    except ValueError:
        return float("nan")


def read_day(p, uni):
    """data/bars の1日 → (日付, {code: (o,h,l,c,v)})"""
    out, d = {}, None
    with gzip.open(p, "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = r.get("code")
            if uni and c not in uni:
                continue
            d = r["date"]
            out[c] = (fnum(r.get("open")), fnum(r.get("high")), fnum(r.get("low")), fnum(r.get("close")), fnum(r.get("volume")))
    return d or p.name[:10], out


def bar_days(root, after=""):
    ps = sorted((root / "data" / "bars").glob("*/*.csv.gz"))
    return [p for p in ps if p.name[:10] > after]


def load_px5y(px5y, uni):
    """yochou_px5y.py の出力 → (dates, codes, O, H, L, C, V, adjusted_asof)"""
    import pandas as pd
    px5y = Path(px5y)
    meta = load_json(px5y / "meta.json")
    frames = []
    for p in sorted(px5y.glob("chunk_*.csv.gz")):
        df = pd.read_csv(p, dtype={"code": str})
        frames.append(df[df["code"].isin(uni)] if uni else df)
    df = pd.concat(frames, ignore_index=True)
    dates = sorted(df["date"].unique())
    codes = sorted(df["code"].unique())
    out = []
    for col in ("open", "high", "low", "close", "volume"):
        m = df.pivot_table(index="date", columns="code", values=col, aggfunc="last").reindex(index=dates, columns=codes)
        out.append(m.to_numpy(dtype=float))
    return dates, codes, *out, (meta.get("adjusted_asof") or dates[-1])


def build_init_panel(root, px5y, uni):
    """初回用の行列: px5y（取った日の分割調整済み）＋それより後の data/bars。最後の日の値札にそろえ、px_sanity を当てる"""
    dates, codes, O, H, L, C, V, adj_asof = load_px5y(px5y, uni)
    log(f"  px5y: {len(codes)}銘柄 {dates[0]}〜{dates[-1]}（分割調整は {adj_asof} 時点）")
    extra = bar_days(root, dates[-1])
    if extra:
        ci = {c: i for i, c in enumerate(codes)}
        add = []
        for p in extra:
            d, rows = read_day(p, uni)
            add.append((d, rows))
            for c in rows:
                if c not in ci:
                    ci[c] = len(codes); codes.append(c)
        n0 = len(dates)
        def grow(M):
            M2 = np.full((n0 + len(add), len(codes)), np.nan)
            M2[:n0, :M.shape[1]] = M
            return M2
        O, H, L, C, V = (grow(M) for M in (O, H, L, C, V))
        for k, (d, rows) in enumerate(add):
            dates.append(d)
            for c, (o, h, l, cc, v) in rows.items():
                j = ci[c]
                O[n0 + k, j], H[n0 + k, j], L[n0 + k, j], C[n0 + k, j], V[n0 + k, j] = o, h, l, cc, v
        log(f"  data/bars から {len(add)}日を足した（{add[0][0]}〜{add[-1][0]}）")
    # px5y の調整より後に権利落ちした分割: その前の値を比率で割る（出来高は掛ける）
    sp = split_table(root)
    nsp = 0
    da = np.array(dates)
    for j, c in enumerate(codes):
        for ex, r in sp.get(c, []):
            if adj_asof < ex <= dates[-1]:
                m = da < ex
                for M in (O, H, L, C):
                    M[m, j] /= r
                V[m, j] *= r
                nsp += 1
    if nsp:
        log(f"  px5y より後の分割 {nsp}件を前の値に当てた")
    # 終値の並びの検査（similar_weekly と同じ px_sanity.clean_columns）
    try:
        sys.path.insert(0, str(ROOT))
        import px_sanity as PS
        spd = {c: {ex: r for ex, r in lst} for c, lst in sp.items()}
        res = PS.clean_columns(dates, C, codes, spd)
        cnt = {}
        for j, (st, fac, _) in res.items():
            cnt[st] = cnt.get(st, 0) + 1
            drop = ~np.isfinite(fac)
            for M in (O, H, L, C, V):
                M[drop, j] = np.nan
            ok = np.isfinite(fac) & (fac != 1.0)
            for M in (O, H, L, C):
                M[ok, j] *= fac[ok]
            V[ok, j] /= fac[ok]
        log(f"  px_sanity: {len(res)}銘柄 {cnt}")
    except Exception as e:   # 検査が使えなくても止めない（飛びの日は step が使わない）
        log(f"  px_sanity を当てられなかった: {e}")
    return dates, codes, O, H, L, C, V


# ──────────────────────────────────────── 書き出し
def hist_rows(day, eng, J, m):
    rows = []
    pm = lambda x: int(round(float(x) * 1000))
    for n, j in enumerate(J):
        rows.append([day, eng.codes[j], sig(m["c"][n], 5), pm(m["pr"][n]), pm(m["pr_lo"][n]), pm(m["pr_hi"][n]),
                     sig(m["avg"][n]), sig(m["avg_lo"][n]), sig(m["avg_hi"][n]), pm(m["dev"][n]), pm(m["ovh"][n]),
                     sig(m["wall_lo"][n]), sig(m["wall_hi"][n]), pm(m["wall_s"][n]), sig(m["far_p"][n]), pm(m["far_s"][n]),
                     sig(m["q05"][n]), sig(m["q95"][n]), pm(m["resid"][n]), m["conf"][n]])
    return rows


def write_csv_gz(path, rows, header=HIST_COLS):
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    with open(path, "wb") as f:
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9) as g:
            g.write(buf.getvalue().encode("utf-8"))


def read_hist(root, since):
    """data/cost/hist と days から {code: [(date, c, avg)]}（since より後）"""
    out = {}
    ps = sorted((root / "data" / "cost" / "hist").glob("*.csv.gz")) + sorted((root / "data" / "cost" / "days").glob("*/*.csv.gz"))
    for p in ps:
        if p.parent.name == "hist" and p.name[:7] < since[:7]:
            continue
        if p.parent.name != "hist" and p.name[:10] <= since:
            continue
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["date"] > since and r.get("avg"):
                    out.setdefault(r["code"], {})[r["date"]] = (fnum(r["c"]), fnum(r["avg"]))
    return out


def pct(x, nd=1):
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, nd)


def cost_map(eng, J, m, day):
    rows = []
    for n, j in enumerate(J):
        q = m["conf"][n]
        if q == "N":
            rows.append([eng.codes[j], None, None, None, "N", None, None])
            continue
        rows.append([eng.codes[j], pct(m["pr"][n], 0), pct(m["pr_lo"][n], 0), pct(m["pr_hi"][n], 0), q,
                     sig(m["avg"][n], 3), pct(m["dev"][n], 0)])
    rows.sort(key=lambda r: r[0])
    return {"asof": day, "updated": datetime.now(JST).isoformat(timespec="seconds"), "label": LABEL,
            "what": "推定の含み益の人の割合（％）と、その幅・確かさ・推定の平均買値（毎日）。しこりの壁の形は docs/cost/{コード}.json（週1回）",
            "cols": ["t", "p", "lo", "hi", "q", "a", "d"],
            "col_names": {"t": "コード", "p": "推定 含み益の割合（中心・％）", "lo": "幅の下（％）", "hi": "幅の上（％）",
                          "q": "推定の確かさ", "a": "推定の平均買値（円）", "d": "終値の推定平均買値からの乖離（％）"},
            "q_names": CONF_NAMES, "note": NOTE, "params": params(), "rows": rows}


def params():
    return {"alpha": list(ALPHAS), "denoms": ["発行済−自己株", "推定の浮動株"], "rcap": RCAP, "step": "0.5%",
            "init": "最初の250営業日の価格帯別出来高", "peak": "(高値+安値+終値)/3", "min_days": MIN_DAYS,
            "conf": {"低い": f"開始の分布の残り≧{CONF_LOW[0]} か 幅>{int(CONF_LOW[1]*100)}ポイント",
                     "ふつう": f"残り≧{CONF_MID[0]} か 幅>{int(CONF_MID[1]*100)}ポイント"}}


def stock_json(eng, j, n, m, day, weekly):
    """銘柄ごとの公開ファイル（1%帯にまとめた千分率・端の0.5%ずつは切る）"""
    D = eng.D[BASE, j, :].astype(np.float64)
    D = D / D.sum()
    D2 = D[: NB // 2 * 2].reshape(-1, 2).sum(axis=1)
    cs = np.cumsum(D2)
    a = int((cs < 0.005).sum())
    b = int(min(len(D2) - 1, (cs < 0.995).sum()))
    v = [int(round(x * 1000)) for x in D2[a:b + 1]]
    step = 2
    while len(v) > 160:          # 値幅の広い銘柄は帯を太くして約2KBに収める
        step *= 2
        v = [sum(v[i:i + 2]) for i in range(0, len(v), 2)]
    lo_edge = math.exp(eng.g0[j] + a * 2 * STEP)
    q = m["conf"][n]
    out = {"code": eng.codes[j], "asof": day, "label": LABEL,
           "c": sig(m["c"][n], 5),
           "bins": {"lo": sig(lo_edge, 5), "f": round(math.exp(step * STEP), 6), "v": v,
                    "what": "下の値段から1帯ごとに値段が f 倍。v は帯ごとの推定の割合（千分率）"},
           "q": q, "q_name": CONF_NAMES[q], "resid": round(float(m["resid"][n]), 2),
           "pr": [pct(m["pr"][n], 0), pct(m["pr_lo"][n], 0), pct(m["pr_hi"][n], 0)],
           "avg": [sig(m["avg"][n]), sig(m["avg_lo"][n]), sig(m["avg_hi"][n])],
           "dev": pct(m["dev"][n], 0), "ovh": pct(m["ovh"][n], 1),
           "wall": ({"lo": sig(m["wall_lo"][n]), "hi": sig(m["wall_hi"][n]), "s": pct(m["wall_s"][n], 1),
                     "d": pct(math.sqrt(m["wall_lo"][n] * m["wall_hi"][n]) / m["c"][n] - 1, 0)} if m["wall_s"][n] > 0 else None),
           "far": ({"p": sig(m["far_p"][n]), "s": pct(m["far_s"][n], 1), "d": pct(m["far_p"][n] / m["c"][n] - 1, 0)}
                   if m["far_s"][n] > 0 else None),
           "r90": [sig(m["q05"][n]), sig(m["q95"][n])],
           "w": weekly}
    if q == "N":
        out["pr"] = None
    return out


def weekly_series(hist, code, upto):
    """直近1年の週ごとの最後の日 [日付, 終値, 推定の平均買値]"""
    h = hist.get(code) or {}
    last = {}
    for d in sorted(h):
        if d <= upto:
            last[week_key(d)] = d
    keys = sorted(last)[-53:]
    return [[last[k][5:], sig(h[last[k]][0], 4), sig(h[last[k]][1], 4)] for k in keys]


def write_public(root, eng, J, m, day, hist, names):
    out = root / "docs" / "cost"
    out.mkdir(parents=True, exist_ok=True)
    keep = set()
    idx = {}
    total = 0
    for n, j in enumerate(J):
        code = eng.codes[j]
        js = stock_json(eng, j, n, m, day, weekly_series(hist, code, day))
        s = json.dumps(js, ensure_ascii=False, separators=(",", ":"))
        p = out / f"{code}.json"
        if not p.exists() or p.read_text(encoding="utf-8") != s:
            p.write_text(s, encoding="utf-8")
        keep.add(p.name)
        total += len(s.encode("utf-8"))
        idx[code] = [m["conf"][n]]
    for p in out.glob("*.json"):
        if p.name not in keep and p.name != "index.json":
            p.unlink()
    ix = {"asof": day, "label": LABEL, "note": NOTE, "params": params(), "q_names": CONF_NAMES,
          "cols": ["推定の確かさ"], "items": dict(sorted(idx.items()))}
    (out / "index.json").write_text(json.dumps(ix, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return len(keep), total


# ──────────────────────────────────────── 実行
def run_init(root, px5y, hist_from_warm=True):
    uni = universe(root)
    t0 = time.time()
    dates, codes, O, H, L, C, V = build_init_panel(root, px5y, uni)
    log(f"  読み込み {time.time() - t0:.0f}秒 {len(codes)}銘柄×{len(dates)}日")
    sp = split_table(root)
    S, F, src = load_denoms(root, codes, dates[-1], sp)
    log(f"  分母の出どころ: {src}")
    eng = Engine(codes)
    first = np.array([np.flatnonzero(np.isfinite(C[:, j]))[0] if np.isfinite(C[:, j]).any() else -1 for j in range(len(codes))])
    init_full = {codes[j] for j in range(len(codes)) if first[j] == 0}
    # 初回は値札を5年の値幅の真ん中に置く（毎日の値札ずらしを減らす）
    with np.errstate(all="ignore"):
        lo_all = np.nanmin(np.where(L > 0, L, np.nan), axis=0)
        hi_all = np.nanmax(H, axis=0)
    for j in range(len(codes)):
        if np.isfinite(lo_all[j]) and np.isfinite(hi_all[j]) and lo_all[j] > 0:
            span = (math.log(hi_all[j]) - math.log(lo_all[j])) / STEP
            if span < NB - 2 * MARGIN:
                eng.g0[j] = (math.log(lo_all[j]) + math.log(hi_all[j])) / 2 - NB / 2 * STEP
            else:
                eng.g0[j] = math.log(hi_all[j]) - (NB - MARGIN) * STEP
    hdir = root / "data" / "cost" / "hist"
    month, rows = None, []
    t1 = time.time()
    for t, d in enumerate(dates):
        if month and d[:7] != month and rows:      # 月が変わったら書いて手放す（メモリを抑える）
            write_csv_gz(hdir / f"{month}.csv.gz", sorted(rows, key=lambda r: (r[1], r[0])))
            rows = []
        month = d[:7]
        bars = {codes[j]: (O[t, j], H[t, j], L[t, j], C[t, j], V[t, j]) for j in np.flatnonzero(np.isfinite(C[t]))}
        Jd = eng.step(d, bars, S, F, None, init_full)
        Jm, m = eng.metrics(Jd)
        wk_end = t == len(dates) - 1 or week_key(dates[t + 1]) != week_key(d)
        if len(Jm) and wk_end:                     # 過去は週の最後の取引日だけ残す（git の容量のため）
            rows.extend(hist_rows(d, eng, Jm, m))
        if t % 100 == 0:
            log(f"    {d} {t + 1}/{len(dates)} {time.time() - t1:.0f}秒")
    if rows:
        write_csv_gz(hdir / f"{month}.csv.gz", sorted(rows, key=lambda r: (r[1], r[0])))
    eng.finish_init()
    log(f"  計算 {time.time() - t1:.0f}秒")
    eng.save(root / "data" / "cost" / "state.npz")
    return eng, S, F, dates[-1]


def finish_outputs(root, eng, day, weekly, names):
    J = np.flatnonzero(eng.mode == 2)
    Jm, m = eng.metrics(J)
    cm = cost_map(eng, Jm, m, day)
    s = json.dumps(cm, ensure_ascii=False, separators=(",", ":"))
    (root / "docs").mkdir(exist_ok=True)
    (root / "docs" / "cost_map.json").write_text(s, encoding="utf-8")
    log(f"  docs/cost_map.json {len(cm['rows'])}銘柄 {len(s.encode('utf-8')) / 1024:.0f}KB")
    if weekly:
        since = (date.fromisoformat(day) - timedelta(days=380)).isoformat()
        hist = read_hist(root, since)
        n, tot = write_public(root, eng, Jm, m, day, hist, names)
        log(f"  docs/cost/ {n}ファイル 合計 {tot / 1024 / 1024:.1f}MB（1件平均 {tot / max(n, 1) / 1024:.1f}KB）")
    qs = {CONF_NAMES[k]: int((m["conf"] == k).sum()) for k in CONF_NAMES} if len(Jm) else {}
    log(f"  確かさ: {qs}")
    return Jm, m


def run_daily(root, force_weekly=False):
    st = root / "data" / "cost" / "state.npz"
    if not st.exists():
        raise SystemExit("data/cost/state.npz が無い（初回は --init --px5y で作る）")
    eng = Engine.load(st)
    d_ck = eng.date
    days = bar_days(root, d_ck)
    if not days:
        log(f"  控え {d_ck} より後の日足が無い（何もしない）")
        return
    uni = universe(root)
    sp = split_table(root)
    for p in days:
        d, bars = read_day(p, uni)
        allc = eng.codes + [c for c in bars if c not in eng.idx]
        S, F, _ = load_denoms(root, allc, d, sp)
        Sx = dict(zip(allc, S))
        Fx = dict(zip(allc, F))
        spt = {c: r for c, lst in sp.items() for ex, r in lst if ex == d}
        J = _step_with(eng, d, bars, Sx, Fx, spt)
        Jm, m = eng.metrics(J)
        out = root / "data" / "cost" / "days" / d[:4] / f"{d}.csv.gz"
        if not out.exists():
            write_csv_gz(out, hist_rows(d, eng, Jm, m))
        log(f"  {d}: {len(Jm)}銘柄を進めた（分割 {len(spt)}件・使わなかった日 {len(eng.skipped)}件）")
    pub = load_json(root / "docs" / "cost" / "index.json").get("asof") or d_ck
    weekly = force_weekly or week_key(eng.date) != week_key(pub)
    finish_outputs(root, eng, eng.date, weekly, uni)
    if force_weekly or eng.date[:7] != d_ck[:7]:
        eng.save(st)
        log(f"  控えを {eng.date} に書き換えた（月の最初の取引日）")
    if eng.skipped:
        log(f"  使わなかった日: {list(eng.skipped.items())[:10]}")


def _step_with(eng, d, bars, Sx, Fx, spt):
    for c in bars:
        if c not in eng.idx:
            eng.add(c)
    S = np.array([Sx.get(c, np.nan) for c in eng.codes])
    F = np.array([Fx.get(c, 1.0) for c in eng.codes])
    return eng.step(d, bars, S, F, spt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--px5y")
    ap.add_argument("--weekly", action="store_true", help="控えと銘柄ごとのファイルを今日書き換える（週・月の切り替わりを待たない）")
    a = ap.parse_args()
    root = Path(a.root)
    t0 = time.time()
    if a.init:
        if not a.px5y:
            raise SystemExit("--init には --px5y が要る")
        eng, S, F, day = run_init(root, a.px5y)
        finish_outputs(root, eng, day, True, universe(root))
    else:
        run_daily(root, a.weekly)
    log(f"終わり {time.time() - t0:.0f}秒")


if __name__ == "__main__":
    main()
