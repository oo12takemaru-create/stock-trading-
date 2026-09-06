# -*- coding: utf-8 -*-
"""ルールバックテストの共通エンジン（Python 版＝定義の正本）。

このファイルの約定・集計の定義が「正解」で、TypeScript 版
（ruletrade-app/src/lib/backtest/engine.ts）はこれと同じ結果を返す義務がある。

■ 定義の出典
  D:\\マイドキュメント\\Claude\\Projects\\株式投資開発\\BNF2検証\\bnf2_verify.py
  の run_backtest() / metrics() をそのまま移植した。受け入れ試験①
  （exp2_threshold_era.csv との一致）は、この移植が正しいことが前提になる。

■ 約定ルール（bnf2_verify.py L140-200 と1対1で対応）
  1. 判定は「その日の終値」で行い、約定は「翌営業日の始値」（未来情報を使わない）
  2. カレンダーは市場カレンダー（^N225 の営業日）。銘柄にその日の足が無ければ何もしない
  3. 決済判定は建玉の保有日数を1つ進めてから行い、同じ日に建てた玉は当日決済しない
  4. 同日に損切り価格と利確価格の両方に触れた場合は、保守的に損切り扱い
  5. 期間の最後まで決済されなかった建玉は集計に含めない
  6. 同時保有数の上限に達している間は新規建てをしない。上限が None なら無制限
  7. 同じ銘柄を保有中は、同じ銘柄で新規建てをしない

■ 起動文（Phase 2-①）との関係
  起動文の API 仕様は exit = {hold_days, stop_pct} だけだが、それだけでは
  BNF2検証（利確+8% / 損切-4% / 同時10銘柄 / 往復コスト0.2%）を再現できない。
  そこで tp_pct / max_positions / cost_pct を任意パラメータとして足してある。
  既定値は起動文どおり（利確なし・上限なし・コストなし）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

# 建玉の決済理由
REASON_STOP = "SL"    # 損切り
REASON_TAKE = "TP"    # 利確
REASON_TIME = "TIME"  # 時間切れ（保有日数の上限）


@dataclass
class ExitRule:
    """出口設定。パーセントは「%」の数値（-8 は -8% の意味）。"""

    hold_days: int = 5           # 時間切れまでの保有営業日数
    stop_pct: float | None = -8.0  # 損切り（負の数）。None で損切りなし
    take_pct: float | None = None  # 利確（正の数）。None で利確なし
    cost_pct: float = 0.0        # 往復コスト（%）。損益から差し引く

    def stop_ratio(self) -> float | None:
        return None if self.stop_pct is None else 1.0 + self.stop_pct / 100.0

    def take_ratio(self) -> float | None:
        return None if self.take_pct is None else 1.0 + self.take_pct / 100.0


@dataclass
class PriceBook:
    """日付 × 銘柄 の密行列。値が NaN の升目はその日に足が無いことを表す。"""

    dates: pd.DatetimeIndex
    tickers: list[str]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    @classmethod
    def from_panel(cls, panel: pd.DataFrame, tickers: Sequence[str] | None = None,
                   calendar: pd.DatetimeIndex | None = None) -> "PriceBook":
        """long 形式（date, ticker, open, high, low, close）から作る。

        tickers の順序が新規建ての優先順位になる（同時保有数の上限があるとき
        だけ結果に効く）。省略時は panel の出現順。
        """
        panel = panel.copy()
        panel["date"] = pd.to_datetime(panel["date"])
        if tickers is None:
            tickers = list(dict.fromkeys(panel["ticker"]))
        else:
            tickers = [t for t in tickers if t in set(panel["ticker"])]
        if calendar is None:
            calendar = pd.DatetimeIndex(sorted(panel["date"].unique()))
        else:
            calendar = pd.DatetimeIndex(sorted(pd.to_datetime(calendar).unique()))

        t_pos = {t: i for i, t in enumerate(tickers)}
        d_pos = pd.Series(np.arange(len(calendar)), index=calendar)

        shape = (len(calendar), len(tickers))
        arrays = {k: np.full(shape, np.nan) for k in ("open", "high", "low", "close")}

        sub = panel[panel["ticker"].isin(t_pos)]
        rows = d_pos.reindex(sub["date"]).to_numpy()
        cols = sub["ticker"].map(t_pos).to_numpy()
        keep = ~np.isnan(rows)
        rows = rows[keep].astype(np.int64)
        cols = cols[keep].astype(np.int64)
        for k in arrays:
            arrays[k][rows, cols] = sub[k].to_numpy(dtype="float64")[keep]

        return cls(dates=calendar, tickers=list(tickers), **arrays)


@dataclass
class Trade:
    ticker: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    days: int
    ret: float      # % 単位。コスト差し引き後
    reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame(columns=["ticker", "entry_date", "exit_date",
                                         "days", "ret", "reason"])
        return pd.DataFrame([t.__dict__ for t in self.trades])


def run_backtest(book: PriceBook, signals: np.ndarray, exit_rule: ExitRule,
                 max_positions: int | None = None,
                 allow_entry: np.ndarray | None = None,
                 start: pd.Timestamp | None = None,
                 end: pd.Timestamp | None = None) -> BacktestResult:
    """建玉シミュレーション。

    book      : 価格の密行列
    signals   : book と同じ形の bool 行列。True = その日の終値で条件を満たした
    exit_rule : 出口設定
    max_positions : 同時保有数の上限。None で無制限
    allow_entry   : 長さ len(book.dates) の bool。判定日がこの日に True の
                    ときだけ新規建てを許す（相場環境フィルタ）。None で常に許可
    start / end   : シミュレーションする期間（判定日ではなく約定日で切る）
    """
    dates = book.dates
    lo, hi = 0, len(dates)
    if start is not None:
        lo = int(np.searchsorted(dates, pd.Timestamp(start), side="left"))
    if end is not None:
        hi = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    if hi - lo < 2:
        return BacktestResult()

    stop_ratio = exit_rule.stop_ratio()
    take_ratio = exit_rule.take_ratio()
    cost = exit_rule.cost_pct
    hold_days = exit_rule.hold_days
    unlimited = max_positions is None or max_positions <= 0

    # 建玉: ticker index -> [entry_px, entry_date_index, held_days]
    pos_px: dict[int, float] = {}
    pos_entry: dict[int, int] = {}
    pos_days: dict[int, int] = {}
    trades: list[Trade] = []

    o, h, l, c = book.open, book.high, book.low, book.close

    for i in range(lo + 1, hi):
        prev_i = i - 1

        # ── 決済（保有中の建玉）──
        for ti in list(pos_px.keys()):
            hi_px = h[i, ti]
            if np.isnan(hi_px):      # その日は足が無い＝保有日数も進めない
                continue
            pos_days[ti] += 1
            entry_px = pos_px[ti]
            exit_px = None
            reason = ""
            if stop_ratio is not None and l[i, ti] <= entry_px * stop_ratio:
                exit_px, reason = entry_px * stop_ratio, REASON_STOP
            elif take_ratio is not None and hi_px >= entry_px * take_ratio:
                exit_px, reason = entry_px * take_ratio, REASON_TAKE
            elif pos_days[ti] >= hold_days:
                exit_px, reason = c[i, ti], REASON_TIME
            if exit_px is not None:
                trades.append(Trade(
                    ticker=book.tickers[ti],
                    entry_date=dates[pos_entry[ti]],
                    exit_date=dates[i],
                    days=pos_days[ti],
                    ret=(exit_px / entry_px - 1.0) * 100.0 - cost,
                    reason=reason,
                ))
                del pos_px[ti], pos_entry[ti], pos_days[ti]

        # ── 新規建て（前日終値のシグナル → 当日始値）──
        if allow_entry is not None and not allow_entry[prev_i]:
            continue
        if not unlimited and len(pos_px) >= max_positions:
            continue
        for ti in np.flatnonzero(signals[prev_i]):
            if not unlimited and len(pos_px) >= max_positions:
                break
            ti = int(ti)
            if ti in pos_px:
                continue
            entry_px = o[i, ti]
            if np.isnan(entry_px) or entry_px <= 0:
                continue
            pos_px[ti] = float(entry_px)
            pos_entry[ti] = i
            pos_days[ti] = 0

    return BacktestResult(trades=trades)


def summarize(result: BacktestResult, calendar: pd.DatetimeIndex,
              capital_slots: int = 10) -> dict:
    """統計値。bnf2_verify.metrics() と同じ定義。

    capital_slots: 資産曲線を作るときに資金を何分割したと仮定するか。
        BNF2検証は同時保有10銘柄なので 10。最大DD と年率だけがこの値に依存する。
    """
    tdf = result.to_frame()
    if len(tdf) == 0:
        return dict(trades=0, win_rate=None, pf=None, avg_return=None,
                    median_return=None, max_dd=None, annual_return=None,
                    take_rate=None, cum_return=None)

    ret = tdf["ret"]
    wins = ret[ret > 0]
    loss = ret[ret <= 0]
    pf = float(wins.sum() / abs(loss.sum())) if len(loss) and loss.sum() != 0 else float("inf")

    eq = tdf.groupby("exit_date")["ret"].sum().sort_index().cumsum() / capital_slots
    max_dd = float((eq - eq.cummax()).min())
    years = max((calendar[-1] - calendar[0]).days / 365.25, 0.1)

    return dict(
        trades=int(len(tdf)),
        win_rate=round(100.0 * len(wins) / len(tdf), 1),
        pf=round(pf, 2),
        avg_return=round(float(ret.mean()), 2),
        median_return=round(float(ret.median()), 2),
        max_dd=round(max_dd, 1),
        annual_return=round(float(ret.sum() / capital_slots) / years, 1),
        take_rate=round(100.0 * float((tdf["reason"] == REASON_TAKE).mean()), 1),
        cum_return=round(float(ret.sum() / capital_slots), 1),
    )


def yearly_breakdown(result: BacktestResult) -> list[dict]:
    """年別の内訳（決済日ベース）。"""
    tdf = result.to_frame()
    if len(tdf) == 0:
        return []
    tdf = tdf.assign(year=pd.to_datetime(tdf["exit_date"]).dt.year)
    out = []
    for year, grp in tdf.groupby("year"):
        wins = grp["ret"] > 0
        out.append(dict(
            year=int(year),
            trades=int(len(grp)),
            win_rate=round(100.0 * float(wins.mean()), 1),
            avg_return=round(float(grp["ret"].mean()), 2),
            total_return=round(float(grp["ret"].sum()), 1),
        ))
    return out


# ──────────────────────────────────────────────────────────────
#  条件 → シグナル行列
# ──────────────────────────────────────────────────────────────

OPS = {
    "lte": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
    "gte": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
}


def build_signals(panel: pd.DataFrame, book: PriceBook,
                  conditions: Iterable[dict]) -> np.ndarray:
    """条件配列から bool 行列を作る。

    conditions: [{"column": "dev_25", "op": "lte", "value": -20}, ...]
    すべての条件の AND。値が NaN の升目は False（判定できない＝該当しない）。
    """
    t_pos = {t: i for i, t in enumerate(book.tickers)}
    d_pos = pd.Series(np.arange(len(book.dates)), index=book.dates)

    sub = panel[panel["ticker"].isin(t_pos)]
    rows = d_pos.reindex(pd.to_datetime(sub["date"])).to_numpy()
    keep = ~pd.isna(rows)
    rows = rows[keep].astype(np.int64)
    cols = sub["ticker"].map(t_pos).to_numpy()[keep].astype(np.int64)

    out = np.ones((len(book.dates), len(book.tickers)), dtype=bool)
    hit = np.zeros_like(out)
    hit[rows, cols] = True

    for cond in conditions:
        col = cond["column"]
        op = OPS[cond["op"]]
        vals = sub[col].to_numpy(dtype="float64")[keep]
        ok = op(vals, float(cond["value"])) & ~np.isnan(vals)
        m = np.zeros_like(out)
        m[rows, cols] = ok
        out &= m

    return out & hit


# ──────────────────────────────────────────────────────────────
#  SQL 関数 backtest_trades() と同じ処理（Python 側の照合用）
# ──────────────────────────────────────────────────────────────

def resolve_candidates(book: PriceBook, signals: np.ndarray, exit_rule: ExitRule,
                       allow_entry: np.ndarray | None = None,
                       start: pd.Timestamp | None = None,
                       end: pd.Timestamp | None = None) -> list[dict]:
    """1シグナルを1件の約定に解決する（同時保有数と重複建ての制約は当てない）。

    ruletrade-app/supabase/schema.sql の backtest_trades() と同じ結果になる
    必要がある。TypeScript 側の applyPortfolioRules() にこの出力を渡すと、
    run_backtest() と同じトレード集合になる。
    """
    dates = book.dates
    lo, hi = 0, len(dates)
    if start is not None:
        lo = int(np.searchsorted(dates, pd.Timestamp(start), side="left"))
    if end is not None:
        hi = int(np.searchsorted(dates, pd.Timestamp(end), side="right"))
    if hi - lo < 2:
        return []

    stop_ratio = exit_rule.stop_ratio()
    take_ratio = exit_rule.take_ratio()
    hold = exit_rule.hold_days
    o, h, l, c = book.open, book.high, book.low, book.close

    # 銘柄ごとに「足がある日」の位置を並べておく（SQL の lateral 相当）
    bars = [np.flatnonzero(~np.isnan(h[lo:hi, ti])) + lo
            for ti in range(len(book.tickers))]
    pos_in_bars = []
    for ti in range(len(book.tickers)):
        m = np.full(len(dates), -1, dtype=np.int64)
        m[bars[ti]] = np.arange(len(bars[ti]))
        pos_in_bars.append(m)

    out = []
    for prev_i in range(lo, hi - 1):
        if allow_entry is not None and not allow_entry[prev_i]:
            continue
        entry_i = prev_i + 1
        for ti in np.flatnonzero(signals[prev_i]):
            ti = int(ti)
            entry_px = o[entry_i, ti]
            if np.isnan(entry_px) or entry_px <= 0:
                continue
            k0 = pos_in_bars[ti][entry_i]
            if k0 < 0:
                continue
            window = bars[ti][k0 + 1:k0 + 1 + hold]
            reason, exit_px, exit_i, days = None, None, None, None
            for k, j in enumerate(window, start=1):
                if stop_ratio is not None and l[j, ti] <= entry_px * stop_ratio:
                    reason, exit_px = REASON_STOP, entry_px * stop_ratio
                elif take_ratio is not None and h[j, ti] >= entry_px * take_ratio:
                    reason, exit_px = REASON_TAKE, entry_px * take_ratio
                elif k >= hold:
                    reason, exit_px = REASON_TIME, c[j, ti]
                if reason is not None:
                    exit_i, days = j, k
                    break
            if reason is None:
                continue        # 期間内に決済できなかった＝集計に入れない
            out.append(dict(
                ticker=book.tickers[ti],
                signal_date=str(dates[prev_i].date()),
                entry_date=str(dates[entry_i].date()),
                exit_date=str(dates[exit_i].date()),
                days=int(days),
                gross_pct=float((exit_px / entry_px - 1.0) * 100.0),
                reason=reason,
            ))
    out.sort(key=lambda r: (r["entry_date"], r["ticker"]))
    return out
