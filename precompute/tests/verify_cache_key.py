# -*- coding: utf-8 -*-
"""キャッシュキーが TypeScript 側と一致するか。

夜間に温めたキャッシュに API が当たるかどうかは、この一致だけにかかっている。
ズレても**エラーにならず「毎回遅い」だけ**なので、機械で確かめる。

    python precompute/tests/verify_cache_key.py

ruletrade-app 側で npx tsx が動くこと（node_modules がある）が前提。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache_key  # noqa: E402

APP_DIR = r"D:\マイドキュメント\Claude\Projects\ruletrade-app"
DATA_THROUGH = "2026-09-04"

# API に投げる形（リクエスト本体）と、Python 側で同じものを組む引数
CASES = [
    (
        "BNF調整域 閾値-15 出来高なし 保有3日",
        {"conditions": [{"column": "dev_25", "op": "lte", "value": -15}],
         "exit": {"hold_days": 3, "stop_pct": -5, "cost_pct": 0}},
        dict(conditions=[{"column": "dev_25", "op": "lte", "value": -15}],
             hold_days=3, stop_pct=-5, cost_pct=0),
    ),
    (
        "出来高1.5倍・地合いON（3環境）",
        {"conditions": [{"column": "dev_25", "op": "lte", "value": -20},
                        {"column": "vol_ratio_20", "op": "gte", "value": 1.5}],
         "exit": {"hold_days": 5, "stop_pct": -5, "cost_pct": 0},
         "regime_filter": ["NEUTRAL", "BEARISH", "PANIC"]},
        dict(conditions=[{"column": "dev_25", "op": "lte", "value": -20},
                         {"column": "vol_ratio_20", "op": "gte", "value": 1.5}],
             hold_days=5, stop_pct=-5, cost_pct=0,
             regimes=["NEUTRAL", "BEARISH", "PANIC"]),
    ),
    (
        "条件の並び順が違っても同じキーになること",
        {"conditions": [{"column": "vol_ratio_20", "op": "gte", "value": 2},
                        {"column": "dev_75", "op": "lte", "value": -12}],
         "exit": {"hold_days": 1, "stop_pct": -5, "cost_pct": 0}},
        dict(conditions=[{"column": "dev_75", "op": "lte", "value": -12},
                         {"column": "vol_ratio_20", "op": "gte", "value": 2}],
             hold_days=1, stop_pct=-5, cost_pct=0),
    ),
    (
        "真偽値の条件とセクター表",
        {"conditions": [{"column": "dev_25", "op": "lte", "threshold_mode": "sector_table"},
                        {"column": "knife_guard", "op": "is_true"}],
         "exit": {"calendar_days": 14, "stop_pct": -5, "ma_revert": 25, "cost_pct": 0},
         "preset": True},
        dict(conditions=[{"column": "dev_25", "op": "lte", "threshold_mode": "sector_table"},
                         {"column": "knife_guard", "op": "is_true"}],
             calendar_days=14, stop_pct=-5, ma_revert=25, cost_pct=0),
    ),
    (
        "相場環境を4つ全部指定＝絞っていないのと同じ",
        {"conditions": [{"column": "dev_25", "op": "lte", "value": -18}],
         "exit": {"hold_days": 2, "stop_pct": -5, "cost_pct": 0},
         "regime_filter": ["BULLISH", "NEUTRAL", "BEARISH", "PANIC"]},
        dict(conditions=[{"column": "dev_25", "op": "lte", "value": -18}],
             hold_days=2, stop_pct=-5, cost_pct=0,
             regimes=["BULLISH", "NEUTRAL", "BEARISH", "PANIC"]),
    ),
    (
        "書籍照合モード（内部専用）",
        {"conditions": [{"column": "dev_25", "op": "lte", "value": -20}],
         "exit": {"hold_days": 10, "stop_pct": -4, "take_pct": 8, "cost_pct": 0.2},
         "max_positions": 10, "entry_at": "open", "exit_style": "touch"},
        dict(conditions=[{"column": "dev_25", "op": "lte", "value": -20}],
             hold_days=10, stop_pct=-4, take_pct=8, cost_pct=0.2,
             max_positions=10, entry_at="open", exit_style="touch"),
    ),
]


def main():
    reqs = [c[1] for c in CASES]
    proc = subprocess.run(
        ["npx", "tsx", "scripts/print-param-hashes.ts", DATA_THROUGH],
        cwd=APP_DIR, input=json.dumps(reqs), capture_output=True, text=True,
        encoding="utf-8", shell=True)
    if proc.returncode != 0:
        print("TypeScript 側の実行に失敗しました:\n" + (proc.stderr or "")[-1500:])
        return 1
    ts = json.loads(proc.stdout.strip().splitlines()[-1])

    failures = 0
    for (name, _req, py_args), t in zip(CASES, ts):
        p = cache_key.normalize(date_to=DATA_THROUGH, **py_args)
        got = cache_key.params_hash(p)
        ok = got == t["hash"]
        print("%s %s" % ("OK  " if ok else "NG  ", name))
        if not ok:
            failures += 1
            print("     TypeScript: %s" % t["hash"])
            print("     Python    : %s" % got)
            print("     TS の正規化: %s" % json.dumps(t["normalized"], ensure_ascii=False))
            print("     Py の文字列: %s" % cache_key.canonical(p))

    print("\n判定: %s" % ("合格（キャッシュキーは一致）" if failures == 0
                        else "不合格（%d 件）" % failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
