# -*- coding: utf-8 -*-
"""月次エクイティカーブの受け入れ試験。

    python precompute/tests/test_equity_curve.py

見るもの
  1. 曲線の谷が calc_stats の最大DDと一致する（いちばん大事）
  2. 決済の無い月も並び、月が飛ばない
  3. 円建ての値を持たない
  4. export の検査が、壊したときに本当に落ちる（陰性対照）
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import portfolio_engine as pe  # noqa: E402
import export_equity_curve as ec  # noqa: E402

PASS = FAIL = 0


def check(ok, name, extra=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print("OK   " + name + ("  " + extra if extra else ""))
    else:
        FAIL += 1
        print("NG   " + name + ("  " + extra if extra else ""))


def trade(entry, exit_, entry_price, exit_price, shares=100):
    return pe.Trade(ticker="1234", sector="X", strategy="s", regime="NORMAL",
                    entry_date=pd.Timestamp(entry), exit_date=pd.Timestamp(exit_),
                    entry_price=entry_price, exit_price=exit_price,
                    shares=shares, reason="損切り")


def make_trades():
    """勝ち→大負け→回復。**谷が月末ではなく月の途中に来る**ように作る。
       月末だけ見る実装だと谷を浅く描くので、そこを突く。"""
    return [
        trade("2016-09-01", "2016-09-20", 1000, 1200),   # +20,000
        trade("2016-10-01", "2016-11-05", 1000,  400),   # -60,000  ← 月の途中で谷
        trade("2016-11-06", "2016-11-25", 1000, 1300),   # +30,000  ← 月末は戻っている
        # 2016-12 は決済なし（空の月）
        trade("2017-01-05", "2017-01-20", 1000, 1150),   # +15,000
        trade("2017-08-01", "2017-08-10", 1000, 1050),   # +5,000（間の月は空）
    ]


def main():
    trades = make_trades()
    stats = pe.calc_stats(trades)
    curve = pe.monthly_equity_curve(trades, start="2016-09-06", end="2017-12-31")

    print("― 1. 谷が最大DDと一致するか ―")
    deepest = min(p["dd_min_pct"] for p in curve)
    check(abs(deepest - stats["max_dd"]) < 0.05,
          "★曲線の谷 == calc_stats の最大DD★",
          "曲線 %.1f / summary %.1f" % (deepest, stats["max_dd"]))

    nov = next(p for p in curve if p["month"] == "2016-11")
    check(nov["dd_min_pct"] < nov["dd_pct"] - 1,
          "★月の途中の谷を、月末の値と別に持っている★",
          "月内 %.1f / 月末 %.1f" % (nov["dd_min_pct"], nov["dd_pct"]))
    # 月末だけ見る実装なら、この2つが同じになって上が落ちる

    print("\n― 2. 月の並び ―")
    want = ["2016-%02d" % m for m in (9, 10, 11, 12)] + \
           ["2017-%02d" % m for m in range(1, 13)]
    check([p["month"] for p in curve] == want,
          "★決済の無い月(2016-12・2017-02〜07)も並ぶ・月が飛ばない★",
          "%d か月" % len(curve))
    dec = next(p for p in curve if p["month"] == "2016-12")
    check(dec["trades"] == 0 and dec["cum_return_pct"] == nov["cum_return_pct"],
          "空の月は前月の水準を引き継ぐ")

    print("\n― 3. 円を持たない ―")
    keys = set()
    for p in curve:
        keys |= set(p.keys())
    check(not (keys & ec.FORBIDDEN_KEYS), "公開禁止のキーが無い", str(sorted(keys)))

    print("\n― 4. 終点と合計 ―")
    check(abs(curve[-1]["cum_return_pct"] - stats["total_return"]) < 0.06,
          "終点 == 累積リターン",
          "%.2f / %.1f" % (curve[-1]["cum_return_pct"], stats["total_return"]))
    check(sum(p["trades"] for p in curve) == stats["trades"], "件数の合計 == トレード数")

    print("\n― 5. ★陰性対照★ 壊したら export の検査が落ちるか ―")

    def row(c):
        return {"computed_at": "2026-09-18T00:00:00+00:00",
                "result": {"summary": stats, "period": {"from": "2016-09-06",
                                                        "to": "2017-12-31"},
                           "basis": {}, "equity_curve": c}}

    ok_payload = ec.build(row(curve))
    check(ec.validate(ok_payload) == [], "（対照）壊していなければ合格",
          str(ec.validate(ok_payload))[:80])

    # (a) 谷を浅くする ＝ 2026-09-21 にサイトで起きたのと同じ型
    shallow = [dict(p) for p in curve]
    for p in shallow:
        p["dd_min_pct"] = round(p["dd_min_pct"] * 0.9, 1)
        p["dd_pct"] = round(p["dd_pct"] * 0.9, 1)
    check(any("谷" in e for e in ec.validate(ec.build(row(shallow)))),
          "★谷を浅くしたら落ちる★")

    # (b) 月を1つ抜く
    gap = [dict(p) for p in curve if p["month"] != "2016-12"]
    check(any("飛んで" in e for e in ec.validate(ec.build(row(gap)))),
          "★月を抜いたら落ちる★")

    # (c) 終点をずらす
    moved = [dict(p) for p in curve]
    moved[-1]["cum_return_pct"] += 5
    check(any("終点" in e for e in ec.validate(ec.build(row(moved)))),
          "★終点をずらしたら落ちる★")

    # (d) 円建ての値を混ぜる
    #     ★守りは2枚ある★
    #       build() は月ごとに持つキーを決め打ちで組み直す（白名簿）。
    #       だから混ぜた equity は validate まで届かず、そこで消える。
    #       白名簿のほうが黒名簿より強いので、消えることを試験する。
    money = [dict(p) for p in curve]
    money[0]["equity"] = 1_020_000
    built = ec.build(row(money))
    text = json.dumps(built, ensure_ascii=False)
    check("equity" not in text and "1020000" not in text,
          "★混ぜた円建ての値が build() で落ちる★")
    check(ec.validate(built) == [], "（対照）落としたあとは合格する")
    #     黒名簿のほうも生きていることを別に見る（白名簿を通り抜ける場所用）
    leaked = dict(ok_payload)
    leaked["totals"] = dict(leaked["totals"], equity=1_020_000)
    check(any("公開してはいけない" in e for e in ec.validate(leaked)),
          "★それでも混ざったら validate が捕まえる★")

    # (e) portfolio_stats.json と asof が食い違う
    other = {"asof": "2026-09-06", "trades": stats["trades"]}
    check(any("asof" in e for e in ec.validate(ok_payload, other)),
          "★stats と asof が違ったら落ちる★")

    # (f) equity_curve がまだ入っていない古い行
    old = row(curve)
    del old["result"]["equity_curve"]
    try:
        ec.build(old)
        check(False, "★古い行は止まる★", "止まりませんでした")
    except SystemExit as e:
        check("build_portfolio" in str(e), "★古い行は止まる（直し方も言う）★")

    print("\n===== 成功 %d / 失敗 %d =====" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
