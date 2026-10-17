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
import trading_calendar
from fetching import get_prices
from universe import ACTIVE_TICKERS, JAPAN_STOCKS

# 引継ぎ.md §16-4 判断2（2026-09-05 Fable）: ユニバースの正は daily_scanner_v2_8_0.py の
# STOCKS（341銘柄）。238（integrated_backtest 側）はその部分集合。
UNIVERSE_VERSION = "jp340_v2_8_0"


def default_window(years: int = None) -> tuple[dt.date, dt.date]:
    """取得期間。取り始めは**検証期間の頭から一定の日数だけ前**に固定する。

    ★作った日で決めない★（2026-10-11）
      以前は `今日 − years年 − 460日` だったため、作るたびに取り始めが動き、
      ewm（ema_50 など）の値まで作った日によって変わった。
      years は互換のために受け取るが、取り始めには使わない。
    """
    end = dt.date.today() + dt.timedelta(days=1)
    start = (dt.date.fromisoformat(config.MATERIAL_START)
             - dt.timedelta(days=config.WARMUP_DAYS))
    return start, end


#: 履歴が短い銘柄を飛ばして進めてよい上限（short_ok=True のときだけ使う）。
#: これを超えたら1銘柄の不調ではなく提供元全体の不調とみなし、今までどおり止める
MAX_SHORT_SKIP = 5


def run_pipeline(tickers=None, years: int = config.DEFAULT_YEARS,
                 auto_adjust: bool = config.DEFAULT_AUTO_ADJUST,
                 refresh: bool = False, log=print,
                 short_ok: bool = False, skipped_report: list | None = None):
    """(daily_metrics, market_condition, sectors) を返す。

    daily_metrics はウォームアップぶんを落とし、直近 years 年ぶんだけにする。

    short_ok … ★日次で「新しい日」だけを書く経路（update_metrics.py）専用★（2026-10-17・Fable 案C）
        取得した履歴が短い銘柄を、止めずに panel から外して進める。外した銘柄は
        skipped_report（list）に {ticker, rows, first, last} で入れて返す。
        検証の材料を作る経路（build_metrics.py など）は既定の False のままにすること。
        そちらは「1銘柄でも欠けたら止める」を守る（母集団が変わるため）。
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

    # ★東証の休場日の行を落としてから計算する★（2026-10-17・Fable）
    #   yfinance は 2017〜2018 年の休場日に「前の営業日の終値・出来高0」の行を返していて、
    #   それが材料に入り、エンジンが休場日の行で約定・決済していた。
    #   営業日の表は trading_calendar の1か所（検証側の panel_from_db と同じ表）。
    #   日経平均（東証の指数）も同じ表で落とす。米国の指数は東証の日に合わせて並べ直すので触らない。
    #   個別株は下の [3/4] で、行数の検査を通ったあとに1銘柄ずつ落とす。
    _drop_holidays({k: v for k, v in global_data.items() if k == "^N225"}, log, "日経平均",
                   target=global_data)
    holiday_days, holiday_tickers = set(), 0

    log("[3/4] 前計算列を算出")
    frames, skipped = [], []
    for i, t in enumerate(tickers, 1):
        df = prices.get(t)
        if df is None or len(df) < 200:
            skipped.append(t)
            continue
        # ★分割の取りこぼしを直してから計算する★（2026-10-11・8227.T で見つけた）
        df = fetching.fix_unadjusted_splits(df, t, log=log)
        df, bad = trading_calendar.drop_non_sessions(df, None)   # 東証の休場日の行（上の説明）
        if len(bad):
            holiday_days.update(bad)
            holiday_tickers += 1
        frames.append(metrics.compute_stock_metrics(df, t))
        if i % 50 == 0 or i == len(tickers):
            log("  %d/%d 銘柄" % (i, len(tickers)))
        if i == len(tickers) and holiday_days:
            ds = sorted(holiday_days)
            log("  個別株: 東証の休場日の行を落としました（%d 銘柄・%d 日: %s）"
                % (holiday_tickers, len(ds), "、".join(str(d.date()) for d in ds[:6])
                   + (" ほか" if len(ds) > 6 else "")))
    # ★行数が足りない銘柄も、黙って落とさない★（2026-09-22）
    #   取得は成功したのに中身が短い、という形でも母集団は変わる。
    #   入口（取得失敗）だけ塞いでも、ここが開いていれば同じことが起きる。
    if skipped and short_ok and len(skipped) <= MAX_SHORT_SKIP:
        # ★黙って落とさない★ 何を・なぜ外したかを、呼び出し側に必ず渡してログにも出す。
        #   新しい日の行だけを書く経路なので、検証期間の母集団と材料には触れない。
        #   提供元が戻れば、次の回から普通に計算に入る（ここには何も覚えない）。
        for t in skipped:
            df = prices.get(t)
            info = {"ticker": t, "rows": 0 if df is None else len(df),
                    "first": None if df is None or not len(df) else str(df.index.min().date()),
                    "last": None if df is None or not len(df) else str(df.index.max().date())}
            if skipped_report is not None:
                skipped_report.append(info)
            log("  ★%s は取得した履歴が %d 行（%s 〜 %s）しかないので、今回は新しい日の行を書きません"
                % (t, info["rows"], info["first"], info["last"]))
    elif skipped:
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
    # ★材料の開始日は検証期間の頭に固定する★（2026-10-11）
    #   以前は `end − years年` で切っていたため、作り直すたびに検証期間の頭が削られた。
    cutoff = pd.Timestamp(config.MATERIAL_START)
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


def _drop_holidays(frames: dict, log, what, target: dict | None = None):
    """{名前: 日付 index の DataFrame} から東証の休場日の行を落とす（target に書き戻す）。"""
    import trading_calendar as tc

    target = frames if target is None else target
    days, n = set(), 0
    for k, df in frames.items():
        if df is None or not len(df):
            continue
        out, bad = tc.drop_non_sessions(df, None)
        if len(bad):
            target[k] = out
            days.update(bad)
            n += 1
    if days:
        ds = sorted(days)
        log("  %s: 東証の休場日の行を落としました（%d 銘柄・%d 日: %s）"
            % (what, n, len(ds), "、".join(str(d.date()) for d in ds[:6])
               + (" ほか" if len(ds) > 6 else "")))
