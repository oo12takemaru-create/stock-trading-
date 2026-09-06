# -*- coding: utf-8 -*-
"""ポートフォリオ・エンジン（Python 版＝定義の正本）。

公開数字（10年・1,826トレード／勝率52.8%／PF1.55／最大DD−27.3%）を作る計算。
定義の正本は `集客サイト企画/13_公開数字の検証基準.md` §6、
参照実装は `集客サイト企画/_precompute/simulate_exits.py`。

■ backtest_engine.py との違い
  backtest_engine.py … 1ルールだけ。書籍（BNF2検証 exp2）の照合用で、
                       約定は「翌営業日の**始値**」。
  portfolio_engine.py … 規定値3本を1つのポートフォリオとして回す。
                       約定は「翌営業日の**終値**」（＝公開数字の定義）。
  entry_at はこの違いを吸収する内部パラメータで、会員向けAPI・画面には出さない。

■ 約定の定義（13_公開数字の検証基準.md §2・§6-3）
  判定    : その日の確定した日足の終値で条件成立
  約定    : 翌営業日の終値
  損切り  : 安値が損切り水準に触れた日の**終値**で手仕舞い（水準ではなく終値）
  手仕舞い: 各戦略の出口（下表）。決済できないまま期間が終わったら期末強制決済

  | 戦略 | 損切り | 利確 | 時間切れ |
  |---|---|---|---|
  | BNF-LITE  | −5%  | 終値が25日線まで戻ったら | 14暦日 |
  | MOMENTUM  | −5%  | 終値が建値+10%           | 10暦日 |
  | MINERVINI | −9%  | 建値+25%で半分利確 → 損切りを建値へ → 50EMA下抜けで残り | 90暦日 |

■ 1銘柄1ポジション
  決済した翌営業日から次のシグナルを探す（参照実装の `i = exited_at + 1`）。

■ 建玉の大きさ
  1トレードのリスク = 資金の1%。株数 = リスク額 ÷ (建値 − 損切り値)。
  セクター係数（thresholds.json の sector_risk_multiplier）を掛ける。0 のセクターは建てない。
  PANIC 中の BNF は建玉半分。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

INITIAL_CAPITAL = 1_000_000.0
RISK_PCT = 1.0
PANIC_RISK_MULT = 0.5


@dataclass
class PresetExit:
    """規定値ルールの出口。会員が触れるのは stop_pct / take_profit_pct / 日数まで。"""

    stop_pct: float                      # 負の数（-5 = -5%）
    calendar_days: int | None = None     # 暦日での時間切れ
    hold_days: int | None = None         # 営業日での時間切れ（自作ルール用）
    take_profit_pct: float | None = None # 終値が建値+この% で利確
    ma_revert: int | None = None         # 終値がこの期間の移動平均まで戻ったら利確（25 or 75）
    split_take_pct: float | None = None  # ここで半分利確し、損切りを建値へ移す（固定プリセット専用）
    exit_below_ema50: bool = False       # 半分利確後、50EMA下抜けで残りを手仕舞う
    # 時間切れの呼び名。参照実装は BNF/MOMENTUM を「保有期限」、
    # ミネルヴィニの90暦日だけ「タイムストップ」と呼び分けている（§6-2 の内訳と揃える）
    time_exit_label: str = "保有期限"


@dataclass
class PresetRule:
    rule_id: str
    name: str
    regimes: tuple[str, ...]             # 稼働する相場環境
    signal: Callable                     # (cols, i, sector, regime, mkt) -> bool
    exit: PresetExit
    priority: int = 0                    # 同じ日に複数当たったときの優先順位（小さい方が先）


@dataclass
class Trade:
    ticker: str
    sector: str
    strategy: str
    regime: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    entry_price: float
    exit_price: float
    shares: int
    reason: str

    @property
    def pnl(self) -> float:
        return (self.exit_price - self.entry_price) * self.shares

    @property
    def ret_pct(self) -> float:
        return (self.exit_price / self.entry_price - 1.0) * 100.0

    @property
    def hold_days(self) -> int:
        return (self.exit_date - self.entry_date).days


# ──────────────────────────────────────────────────────────────
#  1銘柄ぶんのトレード生成
# ──────────────────────────────────────────────────────────────

def simulate_ticker(ticker: str, sector: str, cols: dict, dates: pd.DatetimeIndex,
                    rules: list[PresetRule], mkt: dict,
                    sector_risk: dict, start=None, end=None) -> list[Trade]:
    """1銘柄について、規定値ルールを優先順に当てながらトレードを作る。

    cols: 列名 -> numpy 配列（daily_metrics の1銘柄ぶん。日付順）
    mkt : {"regime": {date: str}, "sp1": {date: float}, "sp3": {date: float}}
    """
    n = len(dates)
    mult = sector_risk.get(sector, sector_risk["default"])
    if mult == 0.0:                      # v2.7.8 除外セクター
        return []

    start = pd.Timestamp(start) if start is not None else dates[0]
    end = pd.Timestamp(end) if end is not None else dates[-1]

    ordered = sorted(rules, key=lambda r: r.priority)
    trades: list[Trade] = []
    i = 0
    while i < n - 1:                     # 約定は翌営業日なので最終日には出せない
        date = dates[i]
        if date < start or date > end:
            i += 1
            continue
        regime = mkt["regime"].get(date)
        if regime is None:
            i += 1
            continue

        rule = None
        for r in ordered:
            if regime not in r.regimes:
                continue
            if r.signal(cols, i, sector, regime, mkt, date):
                rule = r
                break
        if rule is None:
            i += 1
            continue

        # ── 約定: 翌営業日の終値 ──
        ei = i + 1
        entry_price = cols["close"][ei]
        if not np.isfinite(entry_price) or entry_price <= 0:
            i += 1
            continue
        entry_date = dates[ei]

        stop = entry_price * (1.0 + rule.exit.stop_pct / 100.0)
        risk_per_share = entry_price - stop
        if risk_per_share <= 0:
            i += 1
            continue
        risk_amount = INITIAL_CAPITAL * (RISK_PCT / 100.0) * mult
        if rule.rule_id == "bnf-reversal" and regime == "PANIC":
            risk_amount *= PANIC_RISK_MULT
        shares = int(risk_amount / risk_per_share)
        if shares < 1:
            i += 1
            continue

        exited_at = _run_exit(trades, ticker, sector, rule, regime, dates, cols,
                              ei, entry_date, entry_price, stop, shares)
        i = exited_at + 1                # 1銘柄1ポジション（決済の翌日から探し直す）
    return trades


def _run_exit(trades, ticker, sector, rule, regime, dates, cols,
              ei, entry_date, entry_price, stop, shares) -> int:
    """建玉を1本ぶん走らせて、決済した位置（インデックス）を返す。"""
    ex = rule.exit
    n = len(dates)
    cur_stop = stop
    cur_shares = shares
    half_taken = False

    def add(j, price, reason, sh):
        trades.append(Trade(ticker=ticker, sector=sector, strategy=rule.rule_id,
                            regime=regime, entry_date=entry_date, exit_date=dates[j],
                            entry_price=entry_price, exit_price=price,
                            shares=sh, reason=reason))

    j = ei + 1
    while j < n:
        c = cols["close"][j]
        lo = cols["low"][j]
        dte = dates[j]

        # 損切り: 安値が水準に触れた日の「終値」で手仕舞う（§6-3 の文言どおり）
        if np.isfinite(lo) and lo <= cur_stop:
            add(j, c, "損切り", cur_shares)
            return j

        if ex.ma_revert is not None:
            # 「終値が N日線まで戻った」＝ 乖離率が 0 以上（close/maN*100 == dev_N + 100）
            pos = cols["dev_%d" % ex.ma_revert][j]
            if np.isfinite(pos) and pos >= 0.0:
                add(j, c, "%d日MA戻り" % ex.ma_revert, cur_shares)
                return j

        if ex.take_profit_pct is not None and c >= entry_price * (1 + ex.take_profit_pct / 100.0):
            add(j, c, "+%g%%利確" % ex.take_profit_pct, cur_shares)
            return j

        if ex.split_take_pct is not None and not half_taken \
                and c >= entry_price * (1 + ex.split_take_pct / 100.0):
            hs = cur_shares // 2
            add(j, c, "半分利確", hs)
            cur_shares -= hs
            half_taken = True
            cur_stop = entry_price       # 残りは建値ストップに移す
            j += 1
            continue

        if ex.exit_below_ema50 and half_taken:
            e50 = cols["ema_50_pos"][j]
            if np.isfinite(e50) and e50 < 100.0:
                add(j, c, "50EMA下抜け", cur_shares)
                return j

        if ex.calendar_days is not None and (dte - entry_date).days >= ex.calendar_days:
            add(j, c, ex.time_exit_label, cur_shares)
            return j
        if ex.hold_days is not None and (j - ei) >= ex.hold_days:
            add(j, c, ex.time_exit_label, cur_shares)
            return j
        j += 1

    last = n - 1
    add(last, cols["close"][last], "期末強制決済", cur_shares)
    return last


# ──────────────────────────────────────────────────────────────
#  ポートフォリオ制約
# ──────────────────────────────────────────────────────────────

def apply_circuit_breaker(trades: list[Trade], halt_by_date: dict, panic_days_by_date: dict,
                          halt_losses=5, panic_wait_days=3,
                          bnf_loss_threshold=5, bnf_loss_cooldown=3, panic_bnf_max=10):
    """サーキットブレーカー。simulate_exits.apply_circuit_breaker と同じ。

    ・市場HALT（VIX>35 / 日経1ヶ月−15%）の日は新規建てを止める。ただし BNF は貫通する
    ・直近5連敗で停止（勝ちが出るか30日で解除）
    ・PANIC 突入から3日は BNF を見送る
    ・BNF が5連敗したら3日クールダウン
    ・PANIC 中の BNF は同時10本まで
    """
    st = sorted(trades, key=lambda t: t.entry_date)
    accepted: list[Trade] = []
    recent: list[float] = []
    bnf_recent: list[float] = []
    loss_halt_active = False
    loss_halt_start = None
    bnf_cooldown_until = None
    open_bnf: list[tuple] = []
    TIMEOUT = 30

    for t in st:
        ed = t.entry_date
        is_bnf = t.strategy == "bnf-reversal"
        state_halt = halt_by_date.get(ed, False)

        dynamic_halt = False
        if loss_halt_active:
            if loss_halt_start is not None and (ed - loss_halt_start).days >= TIMEOUT:
                loss_halt_active, loss_halt_start, recent = False, None, []
            if loss_halt_active:
                dynamic_halt = True
        elif len(recent) >= halt_losses and all(r <= 0 for r in recent[-halt_losses:]):
            dynamic_halt = True
            loss_halt_active, loss_halt_start = True, ed

        if state_halt and not is_bnf:
            continue
        if dynamic_halt:
            if t.pnl > 0:
                loss_halt_active, loss_halt_start = False, None
                recent.append(t.pnl)
                recent = recent[-20:]
            continue
        if is_bnf and t.regime == "PANIC":
            ps = panic_days_by_date.get(ed, -1)
            if 0 <= ps < panic_wait_days:
                continue
        if is_bnf:
            if bnf_cooldown_until is not None and ed >= bnf_cooldown_until:
                bnf_cooldown_until, bnf_recent = None, []
            if bnf_cooldown_until is not None and ed < bnf_cooldown_until:
                if t.pnl > 0:
                    bnf_cooldown_until, bnf_recent = None, []
                continue
            if len(bnf_recent) >= bnf_loss_threshold and \
                    all(r <= 0 for r in bnf_recent[-bnf_loss_threshold:]):
                bnf_cooldown_until = ed + pd.Timedelta(days=bnf_loss_cooldown)
                if t.pnl > 0:
                    bnf_cooldown_until, bnf_recent = None, []
                continue
        if is_bnf and t.regime == "PANIC":
            open_bnf = [(xd, x) for (xd, x) in open_bnf if xd > ed]
            if len(open_bnf) >= panic_bnf_max:
                continue

        accepted.append(t)
        recent.append(t.pnl)
        recent = recent[-20:]
        if is_bnf:
            bnf_recent.append(t.pnl)
            bnf_recent = bnf_recent[-20:]
            if t.pnl > 0:
                bnf_cooldown_until = None
            if t.regime == "PANIC":
                open_bnf.append((t.exit_date, t))
        if t.pnl > 0:
            loss_halt_active, loss_halt_start = False, None
    return accepted


def apply_concurrent_limit(trades: list[Trade], max_positions=10, max_per_sector=3):
    """同時保有数と同一セクターの上限。simulate_exits.apply_concurrent_limit と同じ結果。

    参照実装は採用済みトレードを毎回なめ直す O(n^2) だが、
    建玉は決済日を過ぎたら二度と数えないので、決済日の小さい順に取り出せる
    ヒープで持てば同じ結果を線形時間で出せる。
    """
    import heapq

    st = sorted(trades, key=lambda t: t.entry_date)
    accepted: list[Trade] = []
    open_heap: list[tuple] = []          # (exit_date, 連番, sector)
    sector_open: dict[str, int] = {}
    seq = 0
    for t in st:
        e = t.entry_date
        # 「entry_date <= e < exit_date」なので、exit_date <= e の建玉は外れる
        while open_heap and open_heap[0][0] <= e:
            _, _, sec_done = heapq.heappop(open_heap)
            sector_open[sec_done] -= 1
        if len(open_heap) >= max_positions:
            continue
        sec = t.sector or "不明"
        if sector_open.get(sec, 0) >= max_per_sector:
            continue
        accepted.append(t)
        seq += 1
        heapq.heappush(open_heap, (t.exit_date, seq, sec))
        sector_open[sec] = sector_open.get(sec, 0) + 1
    return accepted


# ──────────────────────────────────────────────────────────────
#  統計
# ──────────────────────────────────────────────────────────────

def calc_stats(trades: list[Trade], initial_capital=INITIAL_CAPITAL) -> dict:
    if not trades:
        return {"trades": 0}
    st = sorted(trades, key=lambda t: t.exit_date)
    pnls = [t.pnl for t in st]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    equity = initial_capital
    peak = initial_capital
    max_dd = 0.0
    streak = 0
    worst_streak = 0
    for t in st:
        equity += t.pnl
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak * 100)
        if t.pnl <= 0:
            streak += 1
            worst_streak = max(worst_streak, streak)
        else:
            streak = 0

    rets = [t.ret_pct for t in st]
    years = max((st[-1].exit_date - st[0].entry_date).days / 365.25, 0.1)
    total_return = (equity / initial_capital - 1) * 100
    cagr = ((equity / initial_capital) ** (1 / years) - 1) * 100

    return {
        "trades": len(st),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(100 * len(wins) / len(st), 1),
        "pf": round(gross_win / gross_loss, 2) if gross_loss else None,
        "avg_return": round(float(np.mean(rets)), 2),
        "median_return": round(float(np.median(rets)), 2),
        "avg_win": round(float(np.mean([t.ret_pct for t in st if t.pnl > 0])), 2) if wins else None,
        "avg_loss": round(float(np.mean([t.ret_pct for t in st if t.pnl <= 0])), 2) if losses else None,
        "max_dd": round(max_dd, 1),
        "total_return": round(total_return, 1),
        "cagr": round(cagr, 1),
        "avg_hold_days": round(float(np.mean([t.hold_days for t in st])), 1),
        "max_losing_streak": worst_streak,
    }


def exit_reason_counts(trades: list[Trade]) -> dict:
    out: dict[str, int] = {}
    for t in trades:
        out[t.reason] = out.get(t.reason, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))
