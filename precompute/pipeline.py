# -*- coding: utf-8 -*-
"""前計算パイプラインの本体（取得 → 指標計算 → 相場環境）。

precompute_metrics.py（ローカルに CSV/Parquet を出す 1-A のツール）と
build_metrics.py / update_metrics.py（Supabase へ入れる本番バッチ）が
同じ計算を通るように、ここに1本化してある。
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import config
import metrics
import fetching
from fetching import get_prices
from universe import ACTIVE_TICKERS, JAPAN_STOCKS

# 引継ぎ.md §16-4 判断2（2026-09-05 Fable）: ユニバースの正は daily_scanner_v2_8_0.py の
# STOCKS（341銘柄）。238（integrated_backtest 側）はその部分集合。
UNIVERSE_VERSION = "jp340_v2_8_0"


def default_window(years: int) -> tuple[dt.date, dt.date]:
    """取得期間。移動平均200日・52週高値のウォームアップに約1.2年ぶん余分に取る。"""
    end = dt.date.today() + dt.timedelta(days=1)
    start = end - dt.timedelta(days=int(years * 365.25) + 460)
    return start, end


def run_pipeline(tickers=None, years: int = config.DEFAULT_YEARS,
                 auto_adjust: bool = config.DEFAULT_AUTO_ADJUST,
                 refresh: bool = False, log=print):
    """(daily_metrics, market_condition, sectors) を返す。

    daily_metrics はウォームアップぶんを落とし、直近 years 年ぶんだけにする。
    """
    config.ensure_dirs()
    # ★恒常的に取得できない銘柄は明示して外す★（2026-09-22）
    #   「取れなかったから黙って除外」をやめたので、外すものは理由つきで
    #   universe.EXCLUDED_TICKERS に書く。ここが毎回同じであることが再現性の条件。
    tickers = list(ACTIVE_TICKERS) if tickers is None else list(tickers)
    start, end = default_window(years)

    log("ユニバース %d 銘柄 / 取得期間 %s 〜 %s / auto_adjust=%s"
        % (len(tickers), start, end, auto_adjust))

    log("[1/4] グローバル指数を取得（^N225 / ^GSPC / ^VIX）")
    global_data, gfail = get_prices(list(config.GLOBAL_TICKERS.keys()), start, end,
                                    auto_adjust=auto_adjust, kind="global",
                                    refresh=refresh, log=log)
    if gfail:
        # ★ここで止める理由（2026-09-06 に実際に踏んだ・§19-2）★
        # 相場環境(regime)は ^N225 と ^GSPC から決まる。^GSPC が欠けると
        # BULLISH が一度も立たず、BULLISH でしか動かないモメンタム戦略が
        # 丸ごと消える。実際に 1,820件→1,038件・PF1.54→1.78 と、
        # 「精度が落ちる」ではなく**別物の数字**が公開された。
        # まず単独で粘って取り直し、それでもダメなら計算せずに止める。
        log("  ★グローバル指数の取得に失敗: %s → 単独で取り直します" % gfail)
        still = []
        for t in gfail:
            df = fetching.refetch_one(t, start, end, auto_adjust, log=log)
            if df is None:
                still.append(t)
            else:
                fetching.save_cache(t, df, auto_adjust, "global")
                global_data[t] = df
        if still:
            raise SystemExit(
                "グローバル指数 %s を取得できませんでした。\n"
                "  相場環境の判定がこれに依存しているため、欠けたまま計算すると"
                "別物の数字になります。公開しないでここで止めます。\n"
                "  時間をおいて再実行してください（一過性の失敗であることが多い）。"
                % ", ".join(still)
            )
        log("  ★取り直しに成功しました: %s" % ", ".join(gfail))

    log("[2/4] 個別株を取得")
    prices, failed = get_prices(tickers, start, end, auto_adjust=auto_adjust,
                                kind="prices", refresh=refresh, log=log)
    log("  取得できた銘柄: %d / %d" % (len(prices), len(tickers)))

    # ★1銘柄でも欠けたら止める★（2026-09-22）
    #   グローバル指数が既にこの作法なので、個別株もそれに揃える。
    #
    #   ここを「失敗したものは飛ばして先へ」にしていたせいで、公開している
    #   10年の検証結果が**計算し直すたびに違う答え**になっていた。
    #     実測: 336銘柄から1つ外すだけで 最大DD −27.3 → −31.4（4.1ポイント）
    #     同時保有10の枠の取り合いなので、1銘柄の出入りで建玉が総入れ替わりになる。
    #   さらに悪いことに **件数は同じに見えていた**（上限337に対していつも336）。
    #   毎回ちがう1銘柄が落ちていたのに、数が変わらないので誰も気づけなかった。
    #
    #   2026-09-06 の全期間再構築では 5021.T がこれで落ち、正本から
    #   10年ぶんの履歴が丸ごと消えた（直近15日ぶんだけが残っていた）。
    if failed:
        log("  ★個別株の取得に失敗: %s → 単独で取り直します" % ", ".join(failed))
        still = []
        for t in failed:
            df = fetching.refetch_one(t, start, end, auto_adjust, log=log)
            if df is None:
                still.append(t)
            else:
                fetching.save_cache(t, df, auto_adjust, "prices")
                prices[t] = df
        if still:
            raise SystemExit(
                "銘柄 %s を取得できませんでした。\n"
                "  欠けたまま計算すると、同じ10年なのに別物の数字になります"
                "（1銘柄で最大DDが4ポイント動いた実測あり）。\n"
                "  公開しないでここで止めます。時間をおいて再実行してください。\n"
                "  恒常的に取得できないと確かめられたら、理由を書いて"
                "universe.EXCLUDED_TICKERS に足してください"
                "（一過性の失敗を足さないこと）。"
                % ", ".join(still)
            )
        log("  ★取り直しに成功しました: %s" % ", ".join(failed))

    log("[3/4] 前計算列を算出")
    frames, skipped = [], []
    for i, t in enumerate(tickers, 1):
        df = prices.get(t)
        if df is None or len(df) < 200:
            skipped.append(t)
            continue
        frames.append(metrics.compute_stock_metrics(df, t))
        if i % 50 == 0 or i == len(tickers):
            log("  %d/%d 銘柄" % (i, len(tickers)))
    # ★行数が足りない銘柄も、黙って落とさない★（2026-09-22）
    #   取得は成功したのに中身が短い、という形でも母集団は変わる。
    #   入口（取得失敗）だけ塞いでも、ここが開いていれば同じことが起きる。
    if skipped:
        raise SystemExit(
            "データが %d 行未満の銘柄があります: %s\n"
            "  そのまま外すと母集団が変わり、同じ10年でも別物の数字になります。\n"
            "  公開しないでここで止めます。新規上場などで履歴が短い銘柄なら、"
            "理由を書いて universe.EXCLUDED_TICKERS に足してください。"
            % (200, ", ".join("%s(%d)" % (t, len(prices[t]) if prices.get(t) is not None else 0)
                              for t in skipped[:20]))
        )
    if not frames:
        raise RuntimeError("処理できる銘柄がありませんでした")

    panel = pd.concat(frames)
    panel.index.name = "date"
    panel = panel.reset_index()
    panel["date"] = pd.to_datetime(panel["date"])
    cutoff = pd.Timestamp(end) - pd.Timedelta(days=int(years * 365.25))
    panel = panel[panel["date"] >= cutoff]
    panel = metrics.add_rs_rank(panel)
    panel = panel.sort_values(["date", "ticker"]).reset_index(drop=True)
    panel = panel[metrics.ALL_COLUMNS]
    # DB に real で入る列は、ここで同じ精度に丸めておく。
    # 投入側と検証側で丸め方が違うと、境目の比較で判定が食い違う（metrics.REAL_COLUMNS 参照）。
    panel = metrics.round_to_db_precision(panel)
    panel["universe_version"] = UNIVERSE_VERSION
    log("  daily_metrics: %s 行 / %s 列" % (f"{len(panel):,}", len(panel.columns)))

    log("[4/4] market_condition を算出")
    jp_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    market = metrics.compute_market_condition(global_data, jp_dates, panel=panel)
    market = market[[c for c in metrics.MARKET_COLUMNS if c in market.columns]].copy()
    # 件数の列は Postgres 側が integer。float のまま送ると "162.0" で弾かれる
    for c in ("advancing", "declining", "new_high", "new_low"):
        if c in market.columns:
            market[c] = market[c].round().astype("Int64")
    log("  market_condition: %d 行  regime内訳=%s"
        % (len(market), market["regime"].value_counts().to_dict()))

    sectors = pd.DataFrame(
        [{"ticker": t, "name": v[0], "sector": v[1], "market": "プライム",
          "is_active": t in prices} for t, v in JAPAN_STOCKS.items()])

    return panel, market, sectors
