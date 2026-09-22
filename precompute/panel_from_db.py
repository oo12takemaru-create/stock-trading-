# -*- coding: utf-8 -*-
"""検証用のパネルを、外部に取りに行かず **正本（Supabase）から読む**。

■ なぜ要るか（2026-09-22・Fable 案2）
公開している「10年の検証結果」が、同じ期間を計算し直すたびに違う答えになっていた。

  実測: asof 2026-09-08〜09-21 の公開17件で
        trades 1,796〜1,820 / max_dd −24.2〜−27.1 / cum 333.9〜352.5
        9/10 は同じ日に3回走って 1,799 / 1,810 / 1,814

原因はコードではない（同じデータで2回まわすと、採用トレードの並びまで完全に一致する）。
**毎回ちがう銘柄が母集団から落ちていた**。yfinance の取得が一過性に失敗すると、
fetching.get_prices がその銘柄を丸ごと捨て、pipeline がそのまま先へ進む。
同時保有10の枠の取り合いなので、1銘柄の出入りで以降の建玉が総入れ替わりになる。

  実測: 336銘柄から 6532.T を1つ外すだけで
        トレードは4件しか減らないのに 最大DD −27.3 → −31.4（4.1ポイント）

いちばん気づけなかったのは、**件数が同じだった**こと。340銘柄のうち3つは
恒常的に取得できない（EXCLUDED_TICKERS）ので上限は337。公開はいつも 336 で、
毎回もう1銘柄落ちていたのに、数が変わらないので誰も気づかなかった。

■ この直し方
**過去は変わらないものなので、毎回取りに行くこと自体が間違い。**
daily_metrics には検証に要る15列が全部そろっていて、しかも
**その回の取得に失敗した銘柄の履歴も残っている**（取得のたびに消えたりしない）。
だから正本から読めば、母集団は毎回同じになる。

■ ここは黙って進まない
足りないもの・欠けている銘柄があれば **計算せずに止める**。
「取れなかったから除外する」を二度とやらないための番人。
グローバル指数が既にこの作法（pipeline.py の SystemExit）なので、それに揃える。

    from panel_from_db import load_panel
    panel, market = load_panel(log=print)
"""
from __future__ import annotations

import json

import pandas as pd

import supabase_io
from universe import ACTIVE_TICKERS, EXCLUDED_TICKERS

#: portfolio_run.NEEDED_COLUMNS と market 側が要る列。
#: ここを増やしたら daily_metrics にもあるか確かめること。
STOCK_COLUMNS = [
    "ticker", "date",
    "close", "low", "dev_25", "dev_50", "dev_75", "dev_200", "vol_ratio_20",
    "bb_pos_1_5", "high_20_ratio", "high_52w_ratio", "ret_5d", "day_change",
    "knife_guard", "minervini_entry", "ema_50_pos",
]
MARKET_COLUMNS = ["date", "regime", "sp500_change_1d", "sp500_change_3d", "is_halt"]

#: 指標の計算に最低限要る行数（pipeline.py と同じ）
MIN_ROWS = 200


def _read_all(table, columns, query, log, workers=8):
    """表を丸ごと読む。**先に件数を数えて、読めた行数と突き合わせる。**

    ★PostgREST は1回に1000行までしか返さない★
      しかも `limit=10000` と書いても、エラーを出さずに1000行だけ返す
      （2026-09-22 実測）。ページングを間違えると「全部取ったつもりで
      一部しか無い」という、いちばん気づけない壊れ方をする。
      だから件数を先に数え、最後に一致を確かめる。

    81万行を1本ずつ読むと7分半かかる（実測）。日次に載せるので、
    オフセットを分けて並列に読む。順序は最後に並べ直すので影響しない。
    """
    from concurrent.futures import ThreadPoolExecutor

    total = supabase_io.count_rows(table, query)
    if total == 0:
        _die("%s に1行もありません。" % table)
    page = 1000
    offsets = list(range(0, total, page))
    log("  %s: %s 行を %d 回に分けて読みます" % (table, f"{total:,}", len(offsets)))

    url_base, key = supabase_io.credentials()

    def one(off):
        endpoint = "%s/rest/v1/%s?select=%s%s&limit=%d&offset=%d" % (
            url_base, table, columns, ("&" + query) if query else "", page, off)
        return _get_with_retry(endpoint, key, "%s(offset=%d)" % (table, off))

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(one, offsets):
            rows.extend(part)

    # ★数が合わなければ止める★ 足りないまま計算すると別物の数字になる
    if len(rows) != total:
        _die("%s を %s 行読むはずが %s 行でした（取りこぼし）。"
             % (table, f"{total:,}", f"{len(rows):,}"))
    return rows


#: 読み出しの再試行。ネットワークの一過性の失敗で丸ごと落ちないため。
READ_ATTEMPTS = 4


def _get_with_retry(endpoint, key, what):
    """1回ぶんの GET。一過性の失敗は待って粘る。

    ★ここに再試行が要る理由（2026-09-22 に踏んだ）★
      337銘柄を並列で読むので、1本でもタイムアウトすると全部やり直しになる。
      実際 WinError 10060 で読み出しが丸ごと落ちた。
      **日次に載せるものが一度の瞬断で止まるのは、作りが弱い。**
      ただし粘るのは通信の失敗だけ。欠けたデータを黙って受け入れるための
      再試行ではない（行数の突き合わせは呼び出し側でそのまま行う）。
    """
    import time
    import urllib.error

    last = None
    for i in range(READ_ATTEMPTS):
        try:
            _, body = supabase_io._request("GET", endpoint, key)
            return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = e
            if i < READ_ATTEMPTS - 1:
                time.sleep(2.0 * (i + 1))
    _die("%s を読めませんでした（%d回試行）: %s" % (what, READ_ATTEMPTS, last))


def _read_by_ticker(query, log, workers=6):
    """daily_metrics を銘柄ごとに読む。総行数は件数と突き合わせる。"""
    from concurrent.futures import ThreadPoolExecutor

    total = supabase_io.count_rows("daily_metrics", query)
    if total == 0:
        _die("daily_metrics に1行もありません。")
    log("  daily_metrics: %s 行 / %d 銘柄を読みます" % (f"{total:,}", len(ACTIVE_TICKERS)))

    url_base, key = supabase_io.credentials()
    cols = ",".join(STOCK_COLUMNS)
    page = 1000

    def one(ticker):
        out, off = [], 0
        while True:
            endpoint = ("%s/rest/v1/daily_metrics?select=%s&ticker=eq.%s%s"
                        "&order=date.asc&limit=%d&offset=%d"
                        % (url_base, cols, ticker, ("&" + query) if query else "", page, off))
            part = _get_with_retry(endpoint, key, ticker)
            out.extend(part)
            if len(part) < page:
                return out
            off += page

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for part in ex.map(one, ACTIVE_TICKERS):
            rows.extend(part)

    # ★数が合わなければ止める★
    #   一覧に無い銘柄が正本に残っていると、ここで「読んだほうが少ない」になる。
    #   多い・少ないのどちらでも、黙って進まない。
    if len(rows) != total:
        _die(
            "daily_metrics は %s 行ありますが、一覧の %d 銘柄ぶんを読んだら %s 行でした。\n"
            "  universe.py と正本が食い違っています"
            "（一覧から外した銘柄の行が正本に残っている可能性）。"
            % (f"{total:,}", len(ACTIVE_TICKERS), f"{len(rows):,}")
        )
    return rows


def _die(msg):
    raise SystemExit(
        "[正本からの読み込み] " + msg +
        "\n  欠けたまま計算すると**別物の数字**になるので、公開しないでここで止めます。"
    )


def load_panel(start=None, end=None, log=print):
    """(panel, market) を返す。足りなければ SystemExit で止まる。"""
    q = []
    if start:
        q.append("date=gte.%s" % start)
    if end:
        q.append("date=lte.%s" % end)
    query = "&".join(q)

    log("  正本 daily_metrics を読みます（%s）" % (query or "全期間"))
    # ★銘柄ごとに読む★（2026-09-22）
    #   表を丸ごと offset で辿ると、深いところで毎回その手前まで走査されるため
    #   並列にしても速くならない（実測 7分30秒 → 4分51秒 どまり）。
    #   銘柄で絞れば索引が効き、1銘柄2,472行＝3ページで済む。
    #   ついでに「その銘柄が何行あったか」がそのまま分かる。
    rows = _read_by_ticker(query, log)
    panel = pd.DataFrame(rows)
    panel["date"] = pd.to_datetime(panel["date"])
    log("  daily_metrics: %s 行 / %d 列" % (f"{len(panel):,}", panel.shape[1]))

    # ★母集団が毎回同じであることを、ここで機械に確かめさせる★
    got = set(panel["ticker"].unique())
    want = set(ACTIVE_TICKERS)
    missing = sorted(want - got)
    extra = sorted(got - want)
    if missing:
        _die(
            "正本に無い銘柄が %d 件あります: %s\n"
            "  先に pipeline を流して daily_metrics を埋めてください。\n"
            "  恒常的に取得できない銘柄なら universe.EXCLUDED_TICKERS に"
            "理由を書いて足してください（一過性の失敗を足さないこと）。"
            % (len(missing), ", ".join(missing[:20]))
        )
    if extra:
        # 除外したはずの銘柄が正本に入っている＝除外の前提が変わっている
        _die(
            "一覧に無い銘柄が正本に入っています: %s\n"
            "  universe.py と daily_metrics が食い違っています。"
            % ", ".join(extra[:20])
        )

    # 行数が足りない銘柄も、黙って落とさずに止める
    counts = panel.groupby("ticker").size()
    thin = sorted(counts[counts < MIN_ROWS].index)
    if thin:
        _die(
            "行数が %d 未満の銘柄があります: %s"
            % (MIN_ROWS, ", ".join("%s(%d)" % (t, counts[t]) for t in thin[:10]))
        )

    log("  銘柄 %d（除外 %d を差し引いた一覧と一致）" % (len(got), len(EXCLUDED_TICKERS)))

    log("  正本 market_condition を読みます")
    mrows = _read_all("market_condition", ",".join(MARKET_COLUMNS),
                      query + ("&" if query else "") + "order=date.asc", log)
    market = pd.DataFrame(mrows)
    market["date"] = pd.to_datetime(market["date"])
    # 相場環境が欠けると別物になる（pipeline.py のグローバル指数と同じ理由）
    if market["regime"].isna().any():
        _die("market_condition の regime に欠けがあります。")
    log("  market_condition: %s 行" % f"{len(market):,}")

    return panel, market
