# -*- coding: utf-8 -*-
"""上場廃止した銘柄の「最終売買日」（履歴の終わりの日）を引く、ただ1か所。

■ なぜ要るか（2026-10-24・Fable 判断）
  株式移転・TOB などで上場廃止になった銘柄は、最終売買日より後に値が無い。
  ところが提供元（yfinance）は、廃止のあと数日「前日の終値・出来高0」で埋めた行を返したり、
  旧コードに後継の新会社の値を流したり、いずれ旧コードを空にしたりする。
  それを「提供元の一時的な不調」と同じに扱うと、
    ・前計算が埋めた行を正本に書く／短い履歴として毎日知らせる／いずれ止まる
    ・バックテストの建玉が、最終売買日のあとの見かけの値で決済される（または黙って消える）
    ・公開の今日のシグナルが「取得失敗」として数える（本当の失敗と見分けられない）
  になる。そこで「この銘柄の履歴はこの日で終わり」を表で持ち、前計算・バックテスト・
  日次スキャナの3か所が同じ答えを使う。

■ 表の出どころ（新しい表は作らない）
  docs/delisted.json（jukyu_events.py が JPX「上場廃止銘柄一覧」から毎週更新している）。
  使うのは source が「JPX 上場廃止銘柄一覧」で、廃止日 d が書いてある行だけ。
    ・「上場一覧から消えた」行（d も理由も無い）は使わない（一覧の取りこぼしで生きている銘柄を
      終わらせないため）
    ・status は見ない。jukyu_events は週1回しか回らないので、廃止日が過ぎても数日は
      "scheduled" のまま残る。そのあいだに埋めた行が入るのを防ぐため、日付だけで決める

■ 最終売買日の決め方
  廃止日 d の前の東証の営業日（trading_calendar の XTKS の表）。
  東証の上場廃止日は「最終売買日の翌営業日」なので、例: 廃止日 2026-09-29(火) → 2026-09-28(月)。
  表（exchange_calendars）より先の廃止日は、まだ関係が無いので返さない。

■ 使い方
  last_trading_days()                  … {"7240.T": Timestamp("2026-09-28"), ...}
  last_trading_days(["7240.T", ...])   … 渡した銘柄のうち表にあるものだけ
"""
from __future__ import annotations

import json
import os
import re

import pandas as pd

#: リポジトリの docs/delisted.json（precompute/ の1つ上）
DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "docs", "delisted.json")

#: 使う行の出どころ（jukyu_events.update_delisted が書く文字列）
JPX_SOURCE = "JPX 上場廃止銘柄一覧"

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def load_items(path: str | None = None) -> dict:
    """docs/delisted.json の items を返す。形が違えば止める（黙って空にしない）。"""
    p = path or DEFAULT_PATH
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        raise ValueError("%s に items がありません（形が変わった？）" % p)
    return items


def _to_ticker(code: str) -> str:
    return code if code.endswith(".T") else code + ".T"


def last_trading_day(delist_date) -> pd.Timestamp | None:
    """廃止日 → 最終売買日（その前の東証の営業日）。表の外なら None。"""
    import trading_calendar as tc

    d = pd.Timestamp(delist_date).normalize()
    prev = tc.sessions(d - pd.Timedelta(days=20), d - pd.Timedelta(days=1))
    if len(prev) == 0:
        return None
    return pd.Timestamp(prev[-1]).normalize()


def last_trading_days(tickers=None, path: str | None = None) -> dict:
    """{ticker（"7240.T" の形）: 最終売買日（Timestamp）}。tickers を渡せばその中だけ。"""
    want = None if tickers is None else {str(t) for t in tickers}
    out = {}
    for code, it in load_items(path).items():
        if not isinstance(it, dict) or it.get("source") != JPX_SOURCE:
            continue
        d = it.get("d")
        if not isinstance(d, str) or not _ISO.match(d):
            continue
        t = _to_ticker(str(it.get("code") or code))
        if want is not None and t not in want:
            continue
        last = last_trading_day(d)
        if last is not None:
            out[t] = last
    return out
