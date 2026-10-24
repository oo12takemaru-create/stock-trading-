# -*- coding: utf-8 -*-
"""日次差分 → Supabase へ投入（平日の夕方）。

直近 --days 営業日ぶんだけ upsert する。祝日・取得失敗に耐えるため:
  - 新しい行が1行も無くても異常終了しない（祝日は「何もしない」が正しい）
  - 前計算列は移動平均200日などを含むので、計算自体は全期間ぶん回す
    （キャッシュが効くので実時間は短い）

    python precompute/update_metrics.py --env-file ../ruletrade-app/.env.local
    python precompute/update_metrics.py --days 5 --dry-run
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import metrics  # noqa: E402
import supabase_io  # noqa: E402
from pipeline import run_pipeline  # noqa: E402


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


#: 受け取った値を疑う閾値。分割の記録が無いのに1日でこれ以上動いたら止める
MAX_JUMP = 0.5


#: 同じ銘柄の新しい日を、何営業日続けて書けなかったら本人に知らせるか（2026-10-17・Fable 案C）
SKIP_ALERT_DAYS = 5


def last_stored_rows(tickers, before=None):
    """正本の、銘柄ごとの最後の行 {ticker: {"date", "close"} or None}。
    before を渡すと、その日より前で最後の行。"""
    from concurrent.futures import ThreadPoolExecutor

    import json
    url_base, key = supabase_io.credentials()

    def one(ticker):
        ep = ("%s/rest/v1/daily_metrics?select=date,close&ticker=eq.%s"
              "%s&order=date.desc&limit=1"
              % (url_base, ticker, ("&date=lt.%s" % before.date().isoformat()) if before is not None else ""))
        _, body = supabase_io._request("GET", ep, key)
        rows = json.loads(body.decode("utf-8"))
        return ticker, (rows[0] if rows else None)

    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, sorted(tickers)))


def rows_to_write(panel, recent, stored):
    """書く行を選ぶ。直近 recent の日に加えて、**正本に抜けている END より後の日**も埋める。

    ★履歴が短くて飛ばした銘柄（9508.T など）が戻ったとき、自動で抜けを埋める★（2026-10-17）
      飛ばしていた間の日は、直近 --days の窓から外れていることがある。窓だけ書くと
      その日が正本から永久に欠ける。銘柄ごとに「正本の最後の日（recent より前）」より
      後を書けば、戻った回に抜けごと埋まる。
      ★END 以前（固定した検証期間）は、ここでは絶対に選ばない★
    stored … last_stored_rows(tickers, before=recent の最初の日) の結果
    """
    from portfolio_run import END

    end = pd.Timestamp(END)
    first_new = min(recent)
    keep = panel["date"].isin(recent)
    # 候補は END より後・recent より前の行だけ（全期間を銘柄ごとになめない）
    post = panel[(panel["date"] > end) & (panel["date"] < first_new)]
    lower = post["ticker"].map({t: max(pd.Timestamp(r["date"]), end)
                                for t, r in (stored or {}).items() if r})
    # 正本に1行も無い銘柄（lower が NaT）は埋めない。比較は False になる
    keep.loc[post.index[(post["date"] > lower).to_numpy()]] = True
    return panel[keep]


def report_skipped(skipped, all_dates, log=print):
    """★飛ばした銘柄を黙らせない★（2026-10-17・Fable 案C）

    1. ログ（update.log → ジョブの要約）と、ジョブの要約（GITHUB_STEP_SUMMARY）に
       銘柄・取れた行数・期間・正本の最後の日・書けていない営業日数を出す
    2. 同じ銘柄の新しい日を SKIP_ALERT_DAYS 営業日以上書けていなければ、
       SKIP_ALERT_FILE（ワークフローが渡す）に知らせの本文を書く。
       ワークフローがそれを見て本人にメールする（失敗扱いにはしない）
    数え方: 正本にあるその銘柄の最後の日より後の営業日（他の銘柄の新しい日）の数。
      状態を別に持たないので、戻って書けた回に自然に 0 に戻る。
    """
    if not skipped:
        return []
    last = last_stored_rows([s["ticker"] for s in skipped])
    dates = pd.DatetimeIndex(all_dates)
    lines, alerts = [], []
    for s in skipped:
        row = last.get(s["ticker"])
        last_day = pd.Timestamp(row["date"]) if row else None
        missed = int((dates > last_day).sum()) if last_day is not None else len(dates)
        s["stored_last"] = None if last_day is None else str(last_day.date())
        s["missed_days"] = missed
        line = ("%s: 取得 %d 行（%s 〜 %s）/ 正本の最後の日 %s / 書けていない営業日 %d"
                % (s["ticker"], s["rows"], s["first"], s["last"], s["stored_last"], missed))
        lines.append(line)
        if missed >= SKIP_ALERT_DAYS:
            alerts.append(line)
    log("★履歴が短いので新しい日を書かなかった銘柄: %d" % len(skipped))
    for line in lines:
        log("  " + line)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 新しい日を書かなかった銘柄（取得した履歴が短い）\n\n")
            f.write("検証期間（固定した10年）には触れていません。提供元が戻れば、次の回に抜けごと書きます。\n\n")
            for line in lines:
                f.write("- %s\n" % line)
            f.write("\n")

    path = os.environ.get("SKIP_ALERT_FILE")
    if alerts and path:
        with open(path, "w", encoding="utf-8") as f:
            f.write("[ルールトレード] 前計算：%d 営業日以上書けていない銘柄があります\n" % SKIP_ALERT_DAYS)
            f.write("毎日の前計算（precompute-daily）で、次の銘柄の新しい日の行を書けていません。\n"
                    "提供元（yfinance）が短い履歴しか返さないためです。ジョブは止めずに、"
                    "ほかの銘柄は書いています。\n"
                    "固定した検証期間（公開している10年の数字）には影響しません。\n\n")
            for line in alerts:
                f.write("  ・%s\n" % line)
            f.write("\n長く続くなら、取り方を変えるか（案B）、理由を書いて universe.EXCLUDED_TICKERS に"
                    "足すかを決めてください。\n")
        log("  ★%d 営業日以上書けていない銘柄があるので、本人への知らせを用意しました" % SKIP_ALERT_DAYS)
    return alerts


def report_ended(ended, log=print):
    """上場廃止で履歴を終えた銘柄を、ログとジョブの要約に出す（知らせのメールは出さない）。

    短い履歴（report_skipped）とは別の枠にする。こちらは「提供元が戻れば書く」ではなく、
    最終売買日で終わっていて、もう新しい日が来ない銘柄。
    """
    if not ended:
        return
    lines = ["%s: 最終売買日 %s（取得 %d 行・%s）"
             % (e["ticker"], e["last_trade"], e["rows"],
                "最終売買日までを計算" if e["used"] else "新しい日の計算から外した")
             for e in ended]
    log("上場廃止で履歴を終えた銘柄（docs/delisted.json）: %d" % len(ended))
    for line in lines:
        log("  " + line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 上場廃止で履歴を終えた銘柄（docs/delisted.json）\n\n")
            f.write("最終売買日より後の日は取りに行かず、書きません。短い履歴の知らせには数えません。\n\n")
            for line in lines:
                f.write("- %s\n" % line)
            f.write("\n")


def check_incoming(panel, recent, log=print, stored=None, first_write=None):
    """★正本に足す前に、受け取った値を疑う★（2026-10-11）

    提供元（yfinance）は黙って壊れた値を返すことがある。
    2026-10-02 には10銘柄の調整係数が期間の途中で跳ね、東京海上の
    2018-09-20 の終値が 1,350円ではなく 0.6円で返ってきた。それが正本を上書きし、
    公開数字の最大DDが4ポイント良く見えた。過去の材料は固定したので、
    **足す側で止めないと、固定した材料の続きに壊れた値が積み上がる**。

    見るもの（1つでも当たれば止める。何も足さない）
      1. 足す日が、固定した検証期間（portfolio_run.END まで）に入っていない
      2. 保存済みの最後の日について、今回取った終値と保存済みの終値が
         ±MAX_JUMP 以上ずれていない（ずれる＝提供元が過去を作り直した。
         分割か、10/02 のような壊れ方。どちらも人が見るまで足さない）
      3. 足す日どうしで、終値が1日に ±MAX_JUMP 以上動いていない
    """
    from portfolio_run import END

    errs = []
    first_new = min(recent)
    # 抜けを埋める行（rows_to_write）があれば、書く最初の日はそれより前になる
    first_w = min(first_new, pd.Timestamp(first_write)) if first_write is not None else first_new

    # 1. 固定した検証期間に書かない
    if first_w <= pd.Timestamp(END):
        errs.append("足す日 %s が、固定した検証期間（〜%s）に入っています。"
                    % (first_w.date(), END))

    # 2. 保存済みの最後の日と、今回取った値が食い違っていないか
    fresh = panel.set_index(["ticker", "date"])["close"]
    tickers = sorted(panel["ticker"].unique())
    if stored is None:
        stored = last_stored_rows(tickers, before=first_new)

    shifted = []
    for t in tickers:
        row = stored.get(t)
        if not row or row.get("close") in (None, 0):
            continue
        d = pd.Timestamp(row["date"])
        new = fresh.get((t, d))
        if new is None or pd.isna(new):
            continue
        r = float(new) / float(row["close"])
        if abs(r - 1) >= MAX_JUMP:
            shifted.append("%s %s 保存済み %.2f / 今回 %.2f（比 %.4f）"
                           % (t, d.date(), row["close"], new, r))
    if shifted:
        errs.append("保存済みの値と今回取った値が大きく食い違う銘柄が %d あります"
                    "（提供元が過去を作り直した＝分割か、壊れた値）:\n    %s"
                    % (len(shifted), "\n    ".join(shifted[:10])))

    # 3. 足す日どうしの1日の動き
    jumps = []
    for t, g in panel[panel["date"] >= first_w - pd.Timedelta(days=10)].groupby("ticker"):
        c = g.sort_values("date").set_index("date")["close"]
        ch = c.pct_change().dropna()
        ch = ch[ch.index >= first_w]
        big = ch[ch.abs() >= MAX_JUMP]
        for day, v in big.items():
            jumps.append("%s %s %+.1f%%" % (t, day.date(), v * 100))
    if jumps:
        errs.append("1日で ±%d%% 以上動いた値があります（分割の記録が無い限り疑う）:\n    %s"
                    % (MAX_JUMP * 100, "\n    ".join(jumps[:10])))

    if errs:
        raise SystemExit(
            "★正本に足すのを止めました（何も書いていません）★\n  "
            + "\n  ".join(errs)
            + "\n  人が確かめてから足してください。提供元の一時的な壊れ方なら、"
              "時間をおいて再実行すると直ることがあります。"
        )
    log("  受け取った値の検査: 異常なし（%d 銘柄）" % len(tickers))


def parse_args():
    p = argparse.ArgumentParser(description="前計算バッチ（日次差分）")
    p.add_argument("--days", type=int, default=5, help="直近N営業日ぶんを upsert（既定5）")
    p.add_argument("--years", type=int, default=config.DEFAULT_YEARS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--env-file", default="")
    p.add_argument("--chunk", type=int, default=supabase_io.CHUNK_ROWS)
    return p.parse_args()


def main():
    args = parse_args()
    if args.env_file:
        supabase_io.load_env_file(args.env_file)
    t0 = time.time()

    log("=" * 70)
    log("前計算バッチ（日次差分・直近 %d 営業日）" % args.days)
    log("=" * 70)
    # 日次は必ず最新を取りに行く（キャッシュが古いと1日ぶん増えない）
    # ★履歴が短い銘柄は、新しい日の行だけ飛ばして進める★（2026-10-17・Fable 案C）
    #   ここは END より後の日しか書かない経路なので、検証期間の母集団には触れない。
    #   飛ばした銘柄は report_skipped でログ・要約に出し、続けば本人に知らせる。
    # ★上場廃止した銘柄は最終売買日で終える★（2026-10-24・Fable 判断）
    #   docs/delisted.json にある銘柄は、最終売買日より後を取りに行かず、短い履歴の知らせにも
    #   数えない（report_ended で要約に1行ずつ出すだけ）。
    skipped, ended = [], []
    panel, market, _ = run_pipeline(years=args.years, refresh=True, log=log,
                                    short_ok=True, skipped_report=skipped,
                                    ended_report=ended)
    report_ended(ended, log=log)

    all_dates = sorted(pd.DatetimeIndex(panel["date"].unique()))
    recent = all_dates[-args.days:]
    if not recent:
        log("対象日がありません。何もせず終了します。")
        return 0
    log("対象日: %s 〜 %s (%d 営業日)"
        % (recent[0].date(), recent[-1].date(), len(recent)))
    report_skipped(skipped, all_dates, log=log)

    stored = last_stored_rows(sorted(panel["ticker"].unique()), before=min(recent))
    panel_d = rows_to_write(panel, recent, stored)
    market_d = market[pd.to_datetime(market["date"]).isin(recent)]
    n_gap = int((~panel_d["date"].isin(recent)).sum())
    log("daily_metrics %s 行（うち正本の抜けを埋める行 %d）/ market_condition %d 行"
        % (f"{len(panel_d):,}", n_gap, len(market_d)))

    # ★書く前に受け取った値を疑う★ 当たれば何も書かずに止まる（--dry-run でも見る）
    check_incoming(panel, recent, log=log, stored=stored,
                   first_write=panel_d["date"].min() if len(panel_d) else None)

    if args.dry_run:
        log("--dry-run のため投入しません")
        return 0

    supabase_io.upsert("market_condition", supabase_io.frame_to_records(market_d),
                       "date", log=log, chunk=args.chunk)
    supabase_io.upsert("daily_metrics", supabase_io.frame_to_records(panel_d[metrics.DB_COLUMNS + ["universe_version"]]),
                       "date,ticker", log=log, chunk=args.chunk)

    supabase_io.analyze()

    latest = supabase_io.max_value("daily_metrics", "date")
    log("確認: daily_metrics の最新日 = %s / 総行数 %s"
        % (latest, f"{supabase_io.count_rows('daily_metrics'):,}"))
    log("完了 (%.1f 分)" % ((time.time() - t0) / 60))
    return 0


if __name__ == "__main__":
    sys.exit(main())
