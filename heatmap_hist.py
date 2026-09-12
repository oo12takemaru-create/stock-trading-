# -*- coding: utf-8 -*-
"""docs/heatmap.json のその日ぶんを docs/heatmap_hist/YYYY-MM.json に貯める

■ なぜ今から貯めるのか
  heatmap.json は最新スナップショットしか持たない。過去の日を後から作ることはできない。
  画面はまだ無いが、貯め始めないと「3か月前のヒートマップ」は永遠に作れないので、
  保存だけ先に始める（自社株買いの生データ保全と同じ判断）。

■ 形式
  {"m":"2026-09","rows":{"2026-09-12":[["8035",-2.56,1982.6,1.36], ...]}}
  行 = [コード, 1日騰落%, 売買代金(億円), 出来高倍率]。キー名を省いて容量を抑える。
  337銘柄で約5KB/日・1年で約1.2MB。

■ 日付は実行日ではなく heatmap.json の trade_date（実際の株価の日）
  平日6回走るので同じ取引日を何度も書くが、毎回上書きなので最後の実行が残る（冪等）。

■ heatmap_fetch.py は無変更。このスクリプトは読むだけ。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE / "docs" / "heatmap.json"
OUTDIR = HERE / "docs" / "heatmap_hist"


def r(v, n):
    """数値を丸める。取れていなければ None のまま残す（0で埋めない）"""
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def main():
    if not SRC.exists():
        print("heatmap.json が無いので何もしない", file=sys.stderr)
        return 0
    d = json.loads(SRC.read_text(encoding="utf-8"))
    day = d.get("trade_date")
    items = d.get("items") or []
    if not day or not items:
        print("trade_date か items が空。保存しない", file=sys.stderr)
        return 0

    rows = [[i.get("t"), r(i.get("c"), 2), r(i.get("v"), 1), r(i.get("r"), 2)]
            for i in items if i.get("t")]

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"{day[:7]}.json"
    store = {"m": day[:7], "rows": {}}
    if out.exists():
        try:
            store = json.loads(out.read_text(encoding="utf-8"))
            store.setdefault("rows", {})
        except Exception as e:
            print(f"既存ファイルを読めないので作り直す: {e}", file=sys.stderr)
            store = {"m": day[:7], "rows": {}}

    new_day = day not in store["rows"]
    store["rows"][day] = rows
    store["rows"] = {k: store["rows"][k] for k in sorted(store["rows"])}
    out.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"{out.name}: {day} {'追加' if new_day else '上書き'} "
          f"{len(rows)}銘柄 / 収録{len(store['rows'])}日 / {kb:.1f}KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
