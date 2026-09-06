# -*- coding: utf-8 -*-
"""backtest_cache のキー（params_hash）を作る。

★TypeScript 側と1バイトも違ってはいけない★
  正本: ruletrade-app/src/lib/backtest/params.ts の paramsHash()
  ここが1文字でもズレると、夜間に温めたキャッシュに API が当たらず、
  会員は毎回ライブ計算（8〜28秒）を待たされる。しかも「遅いだけ」で
  エラーにならないので気づきにくい。

  → tests/verify_cache_key.py が、TypeScript の実装と突き合わせて検証する。
    キーの作り方を変えるときは必ず両方直して、その試験を回すこと。

■ 正規化の決まり（params.ts の normalize() と同じ）
  ・条件は (列, 演算子, 値, 閾値モード) の順に並べた文字列で昇順に並べ替える
  ・真偽値の条件は値・閾値モードとも null
  ・期間の to は「前計算テーブルの最終日」で頭打ち
  ・相場環境は重複を除いて昇順。4つ全部なら null（＝絞っていないのと同じ）
  ・JSON は JavaScript の JSON.stringify と同じ形（空白なし・整数は小数点を付けない）
"""
from __future__ import annotations

import hashlib
import json

REGIMES = ("BEARISH", "BULLISH", "NEUTRAL", "PANIC")


def _num(v):
    """JavaScript の JSON.stringify と同じ数値表現にする。

    JS には整数と小数の区別が無いので 1.0 は "1" になる。
    Python の json は 1.0 を "1.0" と書くため、整数値の float は int に直す。
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _sort_key(cond) -> str:
    """params.ts の sortKey() と同じ。"""
    value = cond.get("value")
    return "%s|%s|%s" % (cond["column"], cond["op"],
                         "" if value is None else _js_number_str(value))


def _js_number_str(v) -> str:
    n = _num(v)
    return json.dumps(n)


def normalize(conditions, *, hold_days=None, calendar_days=None, ma_revert=None,
              stop_pct=None, take_pct=None, cost_pct=0, regimes=None,
              max_positions=None, date_from="2016-01-01", date_to=None,
              universe="jp340_v2_8_0", tickers=None,
              entry_at="close", exit_style="close") -> dict:
    """API の normalize() と同じ結果を作る。"""
    conds = []
    for c in conditions:
        conds.append({
            "column": c["column"],
            "op": c["op"],
            "value": _num(c.get("value")),
            "threshold_mode": c.get("threshold_mode"),
        })
    conds.sort(key=_sort_key)

    rg = None
    if regimes:
        uniq = sorted(set(regimes))
        rg = None if len(uniq) == len(REGIMES) else uniq

    return {
        "conditions": conds,
        "hold_days": hold_days,
        "calendar_days": calendar_days,
        "ma_revert": ma_revert,
        "stop_pct": _num(stop_pct),
        "take_pct": _num(take_pct),
        "cost_pct": _num(cost_pct),
        "regimes": rg,
        "max_positions": max_positions,
        "from": date_from,
        "to": date_to,
        "universe": universe,
        "tickers": tickers,
        "entry_at": entry_at,
        "exit_style": exit_style,
    }


def canonical(p: dict) -> str:
    """params.ts の canonical と同じ文字列。"""
    arr = [
        [[c["column"], c["op"], c.get("value"), c.get("threshold_mode")]
         for c in p["conditions"]],
        p["hold_days"], p["calendar_days"], p["ma_revert"],
        p["stop_pct"], p["take_pct"], p["cost_pct"],
        p["regimes"], p["max_positions"], p["from"], p["to"], p["universe"],
        p["tickers"], p["entry_at"], p["exit_style"],
    ]
    return json.dumps(arr, ensure_ascii=False, separators=(",", ":"))


def params_hash(p: dict) -> str:
    return hashlib.sha256(canonical(p).encode("utf-8")).hexdigest()
