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

■ 新形式（2026年9月第3週〜 stock_1_w_YYYYMMDD_YYYYMMDD.xlsx）
  ファイル名に開始日・終了日がそのまま入っているので、週ラベルの括弧から推測しない。

■ 取りこぼした週を足す（URL を引数に渡す）
  python -X utf8 investor_hist_append.py URL [URL ...]
  渡した週次Excel（新旧どちらの形式でも可）を1本ずつ取りに行って足す。冪等。
  引数なしなら従来どおり docs/investor_flow.json の1週ぶんを足す。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from investor_hist import NEW_FILE_RE, http, market_of, parse, week_dates   # noqa: E402

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
FLOW = HERE / "docs" / "investor_flow.json"
HIST = HERE / "docs" / "investor_hist.json"


def from_flow(flow):
    """investor_flow.json から (開始日, 終了日, シート名, net) を作る。取れなければ例外"""
    src = flow.get("source_file") or ""
    m_new = NEW_FILE_RE.search(src)
    if m_new:
        # 新形式: ファイル名に日付がある
        start = datetime.strptime(m_new.group(1), "%Y%m%d").date().isoformat()
        end = datetime.strptime(m_new.group(2), "%Y%m%d").date().isoformat()
    else:
        # 旧形式: ファイル名の YYMMWW（年の判定に使う）＋週ラベルの括弧
        m = re.search(r"stock_val_1_(\d{6})\.xls", src)
        if not m:
            raise ValueError(f"source_file から週を取れない: {src}")
        start, end = week_dates(flow.get("week"), m.group(1))
        if not start:
            raise ValueError(f"週ラベルから日付を取れない: {flow.get('week')}")
    net = {}
    for it in flow.get("items") or []:
        if it.get("net_oku") is not None:
            net[it["key"]] = int(it["net_oku"])
    return start, end, flow.get("sheet"), net


def from_url(url):
    """週次Excelを1本取りに行って (開始日, 終了日, シート名, net) を作る"""
    code = (re.search(r"stock_val_1_(\d{6})\.xls", url) or NEW_FILE_RE.search(url))
    if not code:
        raise ValueError(f"週次Excelの URL ではない: {url}")
    w = parse(http(url), code.group(1), url)
    if not w["start"]:
        raise ValueError(f"日付を取れない: {url}")
    return w["start"], w["end"], w["sheet"], w["net"]


def add(weeks, start, end, sheet, net):
    """weeks に1週足す（同じ開始日は入れ直す）。戻り値 (weeks, 上書きしたか)"""
    mk = market_of(sheet)
    if mk == "unknown":
        raise ValueError(f"シート名から市場区分を判定できない: {sheet}")
    if len(net) < 4:
        raise ValueError(f"主体が足りない（{len(net)}）。追記しない")
    before = len(weeks)
    weeks = [w for w in weeks if w["s"] != start]          # 同じ週は捨てて入れ直す（冪等）
    replaced = len(weeks) != before
    weeks.append({"s": start, "e": end, "m": mk, "net": net})
    print(f"{start}〜{end}（{mk}）を{'上書き' if replaced else '追加'}")
    print("  " + " ".join(f"{k}={v:+,}" for k, v in net.items()))
    return weeks, replaced


def main(argv):
    if not HIST.exists():
        print("investor_hist.json が無い。まず investor_hist.py で作ること", file=sys.stderr)
        return 1
    hist = json.loads(HIST.read_text(encoding="utf-8"))
    weeks = hist.get("weeks") or []
    before = len(weeks)

    try:
        if argv:
            # 全部取れてから書く（途中で転んだら何も書かない）
            for url in argv:
                weeks, _ = add(weeks, *from_url(url))
        else:
            flow = json.loads(FLOW.read_text(encoding="utf-8"))
            weeks, _ = add(weeks, *from_flow(flow))
    except Exception as e:
        print(f"追記しない: {e}", file=sys.stderr)
        return 1

    weeks.sort(key=lambda w: w["s"])
    hist["weeks"] = weeks
    hist["updated"] = datetime.now(JST).isoformat(timespec="seconds")
    hist["coverage"]["weeks"] = len(weeks)
    hist["coverage"]["from"] = weeks[0]["s"]
    hist["coverage"]["to"] = weeks[-1]["e"]
    HIST.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{before}週 → {len(weeks)}週")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
