# -*- coding: utf-8 -*-
"""決算発表予定の全社を日付ごとに残す → docs/kessan_yotei_hist/YYYY-MM.json・index.json（株レーダー ①決算発表予定の日付ページ）

起動文: kaburadar\\起動文_実務_日付つき一覧①②③_決算予定とTOBと今日の開示_2026-11-11.md

■ なぜ残すか
  docs/kessan.json は、過ぎた日を主要銘柄だけに縮める（予定の全社が消える）。サイトの kessan-yotei/YYYY-MM-DD.html は
  「その日に発表を予定していた全社」を後から見せたいので、過ぎる前の全社をここに残す。
■ 決まり
  - 今日以降の日（past=false）は毎回いちばん新しい届出で置き換える（会社が日程を変えることがあるため）
  - 過ぎた日は、すでに残っている記録を変えない（kessan.json の縮んだ一覧で上書きしない）。記録が無い日だけ、あるものを入れる
  - 1行は kessan.json の items と同じ形（c・n・s・q・m・maj）。JPX 由来の値なので、サイトでは表示だけに使う
  - kessan.json が読めない・空なら何も書かない

使い方:
  python -X utf8 kessan_yotei_hist.py
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
SRC = HERE / "docs" / "kessan.json"
OUT = HERE / "docs" / "kessan_yotei_hist"


def merge(store, day):
    """store: {d: day}。day は kessan.json の days の1件。置き換えたら True"""
    d = day.get("d")
    if not d:
        return False
    rec = {"d": d, "w": day.get("w"), "n": day.get("n") or len(day.get("items") or []),
           "major": day.get("major"), "items": day.get("items") or []}
    cur = store.get(d)
    if day.get("past"):
        if cur:
            return False          # 過ぎた日は残っている記録を守る
    elif cur == rec:
        return False
    store[d] = rec
    return True


def main():
    try:
        kj = json.loads(SRC.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"kessan.json が読めない（何も書かない）: {e}", file=sys.stderr)
        return 0
    days = kj.get("days") or []
    if not days:
        print("kessan.json に日付が無い（何も書かない）", file=sys.stderr)
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    by_month = {}
    for day in days:
        by_month.setdefault(day["d"][:7], []).append(day)
    changed = 0
    for ym, ds in sorted(by_month.items()):
        f = OUT / f"{ym}.json"
        store = {}
        if f.exists():
            try:
                store = {x["d"]: x for x in json.loads(f.read_text(encoding="utf-8")).get("days") or []}
            except Exception as e:
                print(f"  {f.name} が読めない（作り直す）: {e}", file=sys.stderr)
        n = sum(1 for day in ds if merge(store, day))
        if n or not f.exists():
            f.write_text(json.dumps({"updated": datetime.now(JST).isoformat(timespec="seconds"), "asof": kj.get("asof"),
                                     "note": "上場企業がJPXに届け出た決算発表予定日（日付ごとの全社）。過ぎた日は過ぎる前の最後の届出のまま",
                                     "days": [store[k] for k in sorted(store)]},
                                    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            changed += n
    months = sorted(p.stem for p in OUT.glob("????-??.json"))
    try:
        same = json.loads((OUT / "index.json").read_text(encoding="utf-8")).get("months") == months
    except Exception:
        same = False
    if not same:     # 月が増えた回だけ書く（毎回の差分を作らない）
        (OUT / "index.json").write_text(json.dumps({"updated": datetime.now(JST).isoformat(timespec="seconds"), "months": months},
                                                   ensure_ascii=False), encoding="utf-8")
    print(f"決算発表予定の記録: {changed}日を更新／{len(months)}か月（{', '.join(months)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
