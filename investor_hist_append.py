# -*- coding: utf-8 -*-
"""今週ぶんを docs/investor_hist.json に1週だけ足す

■ Excelを取り直さない
  investor_flow.py が直前に取った docs/investor_flow.json をそのまま使う。
  全期間を作り直す investor_hist.py は557本のExcelを取りに行くので、週次には向かない。

■ 冪等
  同じ週（開始日がキー）が既にあれば上書きする。何度走らせても結果は同じ。
  最後に必ず開始日でソートし直す。

■ 市場区分はシート名から決める
  investor_flow.py が使ったシート名を JSON に残してあるので、それを
  investor_hist.market_of() に渡す。日付でのハードコードはしない
  （次に市場再編があってもここは壊れない）。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from investor_hist import market_of, week_dates   # noqa: E402

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
FLOW = HERE / "docs" / "investor_flow.json"
HIST = HERE / "docs" / "investor_hist.json"


def main():
    if not HIST.exists():
        print("investor_hist.json が無い。まず investor_hist.py で作ること", file=sys.stderr)
        return 1
    flow = json.loads(FLOW.read_text(encoding="utf-8"))
    hist = json.loads(HIST.read_text(encoding="utf-8"))

    # ファイル名の YYMMWW（年の判定に使う）
    m = re.search(r"stock_val_1_(\d{6})\.xls", flow.get("source_file") or "")
    if not m:
        print(f"source_file から週コードを取れない: {flow.get('source_file')}", file=sys.stderr)
        return 1
    start, end = week_dates(flow.get("week"), m.group(1))
    if not start:
        print(f"週ラベルから日付を取れない: {flow.get('week')}", file=sys.stderr)
        return 1

    mk = market_of(flow.get("sheet"))
    if mk == "unknown":
        print(f"シート名から市場区分を判定できない: {flow.get('sheet')}", file=sys.stderr)
        return 1

    net = {}
    for it in flow.get("items") or []:
        if it.get("net_oku") is not None:
            net[it["key"]] = int(it["net_oku"])
    if len(net) < 4:
        print(f"主体が足りない（{len(net)}）。追記しない", file=sys.stderr)
        return 1

    weeks = hist.get("weeks") or []
    before = len(weeks)
    weeks = [w for w in weeks if w["s"] != start]          # 同じ週は捨てて入れ直す（冪等）
    replaced = len(weeks) != before
    weeks.append({"s": start, "e": end, "m": mk, "net": net})
    weeks.sort(key=lambda w: w["s"])

    hist["weeks"] = weeks
    hist["updated"] = datetime.now(JST).isoformat(timespec="seconds")
    hist["coverage"]["weeks"] = len(weeks)
    hist["coverage"]["from"] = weeks[0]["s"]
    hist["coverage"]["to"] = weeks[-1]["e"]
    HIST.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"{start}〜{end}（{mk}）を{'上書き' if replaced else '追加'} "
          f"／{before}週 → {len(weeks)}週")
    print("  " + " ".join(f"{k}={v:+,}" for k, v in net.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
