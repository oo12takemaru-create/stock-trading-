# -*- coding: utf-8 -*-
"""出来事の台帳（v2）の記録 → data/ledger2/events/YYYY-MM.jsonl（追記のみ）

■ 何か（検証台帳 設計書 v1 段階1・2026-11-18）
  株レーダーが毎日拾っているデータから「出来事」を1件1行で残す。答え合わせは ledger2_score.py が
  別ファイル（data/ledger2/scores/）に1行ずつ足す。既存のシグナル台帳（docs/ledger/）はそのまま。
  検出の中身は ledger2_detect.py（1種類＝1関数）。

■ 置き場が docs/ の外の理由
  - 既存の読み手（サイトのシグナル台帳ページ＝docs/ledger）に触れない
  - 行が多い（1日数百件・年に百MB級）ので GitHub Pages（1GB まで）に載せない。data/bars と同じ扱い
  - snapshot に JPX 由来の値（信用残・空売り残高）が入るので、Pages の機械可読 JSON として配らない
  集計（段階4）が要る時に、小さな統計だけを docs/ に出す。

■ 行の形（設計書 3章）
  event_id         YYYYMMDD_code_event_type（一意。同じ日・同じ銘柄・同じ種類は1行にまとめる）
  date / time      その情報が公開された日・時刻（分かるものだけ）
  code             銘柄コード（市場全体は MKT）
  event_type       出来事の種類（英字）
  event_params     判定に使ったしきい値と値
  context          発生時点の状況（温度計の段階・℃、時価総額帯、売買代金帯、業種、チャート項目。取れないものは null）
  snapshot         発生時点の生の値（後から上書きしない）。hide_value:true はサイトに数字を出さない印
  known            いつ知り得たか（pre_open / intraday / after_close）→ 答え合わせの起点を決める
  base_date        答え合わせの起点（この日の始値）
  origin           live（当日記録）/ backfill（遡及）
  detector_version 検出関数の版
  status           記録した時点は open。答え合わせの結果は scores/ の行で持つ（この行は書き換えない）
  src              元データの場所
  recorded_at      この行を書いた時刻

■ 二重に書かない
  event_id がもうあれば書かない。同じ日に何回走っても行は増えない。
  取りこぼしを拾うため、毎回「今日から7日前まで」を見直す（LIVE_START より前は見ない）。
  元データに日付が入っていない「今の値だけ」のファイルは、その日の版のときだけ使う（ledger2_detect.Sources）。

使い方:
  python -X utf8 ledger2.py                          # 毎営業日（ledger-daily.yml）
  python -X utf8 ledger2.py --date 2026-11-18        # その日だけ
  python -X utf8 ledger2.py --date 2026-11-12 --out <scratchpad> --since 2000-01-01   # 試験（本番の場所に書かない）
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ledger2_detect as L

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "ledger2"
JST = timezone(timedelta(hours=9))
WINDOW_DAYS = 7


def load_ids(out):
    ids = set()
    for f in sorted((out / "events").glob("*.jsonl")):
        for r in L.jsonl(f):
            if r.get("event_id"):
                ids.add(r["event_id"])
    return ids


def detect_all(d, data, origin="live"):
    """その日の出来事をすべての検出関数で拾い、台帳の行の形にする"""
    rows, now = [], datetime.now(JST).isoformat(timespec="seconds")
    for name, types, fn, ver in L.REGISTRY:
        try:
            got = fn(d, data) or []
        except Exception as e:                       # 1つの検出関数が壊れても他は記録する
            print(f"::warning::{name}({d}) で失敗: {type(e).__name__}: {e}")
            continue
        for e in got:
            if L.dirty_skip(d, e, data):              # 株価の汚れの守り（ledger2_detect.DIRTY_LOOKBACK）
                print(f"  株価の検査で飛ばした: {d} {e['code']} {e['event_type']}（{data.px_dirty(d, L.DIRTY_LOOKBACK[e['event_type']]).get(e['code'])}）")
                continue
            eid = f"{d.replace('-', '')}_{e['code']}_{e['event_type']}"
            try:
                ctx = L.context(d, e["code"], data)
            except Exception as ex:
                print(f"::warning::context({eid}) で失敗: {ex}")
                ctx = {}
            rows.append({
                "event_id": eid, "date": d, "time": e.get("time"), "code": e["code"],
                "event_type": e["event_type"], "event_params": e.get("event_params") or {},
                "context": ctx, "snapshot": e.get("snapshot") or {},
                "known": e.get("known") or "after_close", "base_date": L.base_date(d, e.get("known") or "after_close"),
                "origin": origin, "detector_version": ver, "status": "open",
                "src": e.get("src"), "recorded_at": now,
            })
    return rows


def write_types(out):
    meta = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
            "note": "出来事の台帳（v2）の種類の一覧。しきい値は設計書 v1 の仮の値。known は答え合わせの起点の決め方"
                    "（pre_open=その日の始値・それ以外=翌営業日の始値・disclosure_time/row_time=公開時刻で決める）。",
            "types": L.TYPES}
    (out / "types.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="この日だけ記録する（YYYY-MM-DD）")
    ap.add_argument("--today", help="今日の日付を差し替える（試験用）")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--docs", help="データの docs/ を差し替える（遡及・試験用）")
    ap.add_argument("--since", default=L.LIVE_START, help="これより前の日は記録しない")
    ap.add_argument("--origin", default="live", choices=("live", "backfill"))
    a = ap.parse_args()

    out = Path(a.out)
    (out / "events").mkdir(parents=True, exist_ok=True)
    today = date.fromisoformat(a.today) if a.today else datetime.now(JST).date()
    if a.date:
        days = [a.date]
    else:
        days = [(today - timedelta(days=i)).isoformat() for i in range(WINDOW_DAYS, -1, -1)]
    days = [d for d in days if d >= a.since and d <= today.isoformat()]

    data = L.Sources(docs=a.docs) if a.docs else L.Sources()
    have = load_ids(out)
    new, cnt_day = [], Counter()
    for d in days:
        for r in detect_all(d, data, a.origin):
            if r["event_id"] in have:
                continue
            have.add(r["event_id"])
            new.append(r)
            cnt_day[d] += 1

    by_month = {}
    for r in sorted(new, key=lambda r: (r["date"], r["event_type"], r["code"])):
        by_month.setdefault(r["date"][:7], []).append(r)
    for ym, rows in by_month.items():
        with open(out / "events" / f"{ym}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    write_types(out)

    cnt = Counter(r["event_type"] for r in new)
    print(f"出来事の台帳に追記 {len(new)}件（見た日 {days[0] if days else '-'}〜{days[-1] if days else '-'}）")
    for d, n in sorted(cnt_day.items()):
        print(f"  {d}: {n}件")
    for k, v in sorted(cnt.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
