# -*- coding: utf-8 -*-
"""規定値ルールの調整域を夜間に前計算して backtest_cache に置く。

■ なぜ要るか（引継ぎ.md §19 相談④・2026-09-06 Fable決定）
ライブ計算は条件によって 0.2〜28秒かかる。会員がスライダーを動かすたびに
待たせないよう、**規定値3本の調整域（企画書v2 §4-2 の全通り）を先に計算しておく**。
調整域から外れる値と自作ルールはライブ計算（画面は「計算中」を出す）。

■ 調整域（企画書v2 §4-2 / §5-1 の1,260通り）
    乖離率閾値   -10% 〜 -30%（1%刻み）= 21
    乖離の基準MA 25日 / 75日           = 2
    保有日数     1 〜 5営業日           = 5
    地合いフィルタ ON / OFF             = 2
    出来高条件   なし / 1.5倍 / 2.0倍   = 3
                                    計 1,260

■ 速さの実測（2026-09-06）
出来高条件が付くと索引が効いて 0.2〜0.4秒。付かないと 1.4〜28.7秒（閾値が緩いほど遅い）。
**入り口の条件が同じなら保有日数だけ違っても入り口の絞り込みは同じ**なので、
入り口ごとに1回だけ SQL を叩き、保有1〜5日ぶんの出口をまとめて解決する（5分の1になる）。

■ キャッシュキー
cache_key.py が TypeScript（params.ts）と同じハッシュを作る。
**ズレるとエラーにならず「毎回遅い」だけ**なので、tests/verify_cache_key.py で必ず確認すること。

    python precompute/warm_cache.py --env-file ../ruletrade-app/.env.local
    python precompute/warm_cache.py --dry-run --limit 12
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import cache_key  # noqa: E402
import supabase_io  # noqa: E402

UNIVERSE = "jp340_v2_8_0"
DATE_FROM = "2016-01-01"
STOP_PCT = -5.0            # 調整域では損切りは固定（企画書§4-2 に損切りは無い）
COST_PCT = 0.0

THRESHOLDS = list(range(-10, -31, -1))          # -10 〜 -30
MA_COLUMNS = {25: "dev_25", 75: "dev_75"}
HOLD_DAYS = [1, 2, 3, 4, 5]
VOLUME_CONDS = {"none": None, "x1.5": 1.5, "x2.0": 2.0}
# 地合いフィルタ ON = BNF が実運用で稼働する環境（引き継ぎ書v2 §3.1）
REGIME_ON = ["NEUTRAL", "BEARISH", "PANIC"]


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def entry_sets():
    """入り口の条件（保有日数を除く組み合わせ）を列挙する。"""
    for ma, col in MA_COLUMNS.items():
        for th in THRESHOLDS:
            for vname, vmult in VOLUME_CONDS.items():
                for regime_on in (False, True):
                    conds = [{"column": col, "op": "lte", "value": th}]
                    if vmult is not None:
                        conds.append({"column": "vol_ratio_20", "op": "gte", "value": vmult})
                    yield {
                        "ma": ma, "threshold": th, "volume": vname,
                        "regime_on": regime_on,
                        "conditions": conds,
                        "regimes": REGIME_ON if regime_on else None,
                    }


class RpcTimeout(RuntimeError):
    pass


def call_rpc(url, key, conds, regimes, hold_days, date_to, retries=2):
    body = {
        "p_conditions": conds, "p_from": DATE_FROM, "p_to": date_to,
        "p_hold_days": hold_days, "p_stop_pct": STOP_PCT, "p_take_pct": None,
        "p_regimes": regimes, "p_universe": UNIVERSE, "p_tickers": None,
        "p_calendar_days": None, "p_ma_revert": None,
        "p_entry_at": "close", "p_exit_style": "close",
    }
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url + "/rest/v1/rpc/backtest_trades", data=json.dumps(body).encode(),
            method="POST",
            headers={"apikey": key, "Authorization": "Bearer " + key,
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "replace")
            # 60秒（service_role の statement_timeout）に当たった場合。
            # 条件が緩すぎて候補が多すぎる組み合わせなので、諦めて次へ進む。
            if "57014" in detail:
                if attempt >= retries:
                    raise RpcTimeout(detail) from None
            elif attempt >= retries:
                raise
            time.sleep(3 * (attempt + 1))
        except Exception:
            if attempt >= retries:
                raise
            time.sleep(3 * (attempt + 1))


def summarize(trades, cost_pct, date_from, date_to, capital_slots=10):
    """engine.ts の summarize() と同じ定義（金額系は出さない）。"""
    if not trades:
        return dict(trades=0, win_rate=None, pf=None, avg_return=None,
                    median_return=None, max_dd=None, cum_return=None,
                    annual_return=None, take_rate=None)
    rets = sorted(t["gross_pct"] - cost_pct for t in trades)
    n = len(rets)
    wins = [r for r in rets if r > 0]
    loss = [r for r in rets if r <= 0]
    loss_sum = sum(loss)
    mid = n // 2
    median = rets[mid] if n % 2 else (rets[mid - 1] + rets[mid]) / 2
    take = sum(1 for t in trades if t["reason"] == "TP")
    return dict(
        trades=n,
        win_rate=round(100 * len(wins) / n, 1),
        pf=None if loss_sum == 0 else round(sum(wins) / abs(loss_sum), 2),
        avg_return=round(sum(rets) / n, 2),
        median_return=round(median, 2),
        # ★同時保有数に上限が無いので、金額に依存する指標は出さない
        #   （13_公開数字の検証基準.md §4-2 の注記と同じ理由）
        max_dd=None, cum_return=None, annual_return=None,
        take_rate=round(100 * take / n, 1),
    )


def yearly(trades, cost_pct):
    """engine.ts の yearlyBreakdown() と同じ（決済日ベース）。"""
    groups = {}
    for t in trades:
        groups.setdefault(int(t["exit_date"][:4]), []).append(t["gross_pct"] - cost_pct)
    out = []
    for y in sorted(groups):
        g = groups[y]
        out.append(dict(year=y, trades=len(g),
                        win_rate=round(100 * sum(1 for r in g if r > 0) / len(g), 1),
                        avg_return=round(sum(g) / len(g), 2),
                        total_return=round(sum(g), 1)))
    return out


def apply_no_overlap(trades):
    """保有中の同じ銘柄では建てない（engine.ts の applyPortfolioRules・上限なしの場合）。"""
    st = sorted(trades, key=lambda t: (t["entry_date"], t["ticker"]))
    held = {}
    out = []
    for t in st:
        until = held.get(t["ticker"])
        if until is not None and t["entry_date"] < until:
            continue
        out.append(t)
        held[t["ticker"]] = t["exit_date"]
    return out


DISCLAIMER = ("過去の検証結果であり、将来の成績を約束するものではありません。"
              "売買手数料・スリッページ・税金は考慮していません（往復コストを指定した場合を除く）。"
              "規定値は書籍の検証条件であり、推奨ではありません。")


def build_payload(trades, date_from, date_to):
    s = summarize(trades, COST_PCT, date_from, date_to)
    return {
        "kind": "rule_only",
        "label": "ルール単独（ポートフォリオ制約なし）",
        "money_metrics_available": False,
        **s,
        "yearly": yearly(trades, COST_PCT),
        "regime_breakdown": [],
        "period": {"from": date_from, "to": date_to},
        "universe": UNIVERSE,
        "cached": False,
        "computed_ms": 0,
        "disclaimer": DISCLAIMER,
    }


def parse_args():
    p = argparse.ArgumentParser(description="規定値の調整域をキャッシュに温める")
    p.add_argument("--env-file", default="")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0, help="入り口の組み合わせを先頭N件だけ")
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    url, key = supabase_io.credentials()
    t0 = time.time()

    date_to = supabase_io.max_value("daily_metrics", "date")
    sets = list(entry_sets())
    if args.limit:
        sets = sets[:args.limit]
    total = len(sets) * len(HOLD_DAYS)
    log("調整域 %d 通り（入り口 %d × 保有 %d）/ 期間 %s 〜 %s"
        % (total, len(sets), len(HOLD_DAYS), DATE_FROM, date_to))

    rows = []
    saved = 0
    skipped = []
    for i, es in enumerate(sets, 1):
        t1 = time.time()
        # 保有日数の最大でまとめて取り、短い保有は同じ入り口から出口だけ引き直す…
        # ではなく、SQL の出口解決を使うため保有ごとに叩く（入り口が索引で速い組み合わせが
        # 2/3 を占めるので、この単純さで十分。実測は下のログを見る）
        for hold in HOLD_DAYS:
            try:
                trades = call_rpc(url, key, es["conditions"], es["regimes"], hold, date_to)
            except RpcTimeout:
                # 条件が緩すぎて時間内に終わらない組み合わせ。
                # 会員がここを選んだらライブ計算（「計算中」表示）に落ちる。
                skipped.append(dict(ma=es["ma"], threshold=es["threshold"],
                                    volume=es["volume"], regime_on=es["regime_on"],
                                    hold=hold))
                continue
            trades = apply_no_overlap(trades)
            p = cache_key.normalize(
                conditions=es["conditions"], hold_days=hold, stop_pct=STOP_PCT,
                cost_pct=COST_PCT, regimes=es["regimes"],
                date_from=DATE_FROM, date_to=date_to, universe=UNIVERSE)
            rows.append({
                "params_hash": cache_key.params_hash(p),
                "params": p,
                "result": build_payload(trades, DATE_FROM, date_to),
                "computed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            })
        # ★途中で落ちてもやり直しにならないよう、こまめに保存する
        if not args.dry_run and len(rows) >= 100:
            supabase_io.upsert("backtest_cache", rows, "params_hash",
                               log=lambda *_: None)
            saved += len(rows)
            rows = []
        if i % 20 == 0 or i == len(sets):
            log("  入り口 %d/%d（MA%d 閾値%d 出来高%s 地合い%s）%.1f秒/組 / 保存済 %d"
                % (i, len(sets), es["ma"], es["threshold"], es["volume"],
                   "ON" if es["regime_on"] else "OFF", time.time() - t1, saved))

    if not args.dry_run and rows:
        supabase_io.upsert("backtest_cache", rows, "params_hash", log=lambda *_: None)
        saved += len(rows)

    log("保存 %d 件 / 見送り %d 件 / 所要 %.1f 分"
        % (saved, len(skipped), (time.time() - t0) / 60))
    if skipped:
        log("★時間内に終わらず見送った組み合わせ（会員が選ぶとライブ計算になる）:")
        for k in skipped[:20]:
            log("   MA%d 閾値%d 出来高%s 地合い%s 保有%d日"
                % (k["ma"], k["threshold"], k["volume"],
                   "ON" if k["regime_on"] else "OFF", k["hold"]))
        if len(skipped) > 20:
            log("   ほか %d 件" % (len(skipped) - 20))
    return 0


if __name__ == "__main__":
    sys.exit(main())
