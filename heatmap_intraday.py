# -*- coding: utf-8 -*-
"""日本株ヒートマップの回ごとの値を記録する → docs/heatmap_intraday/YYYY-MM-DD.json（株レーダー・2026-10-30）

「今日の再生」（起動文_実務_ヒートマップの再生と今日の3行_2026-10-30.md）の元データ。サイト側の画面はまだ無い。
  形: {"date":"2026-10-30","frames":[{"t":"09:00","c":{"7203":1.23,"8035":-0.85}}, ...]}
  c  前日比（%・小数2桁）。対象は heatmap.json・heatmap_225.json・heatmap_growth.json の銘柄を合わせたもの
  t  狙いの時刻（日本時間を30分単位に丸めたもの・いちばん近い :00/:30）。15:45 以降の回は "16:00"（引け後の確定値）に寄せる。
     同じ t が2度走ったら上書き（重複させない）。frames は t の順
  記録するのは heatmap.json の trade_date が今日（日本時間）の回だけ（休場日・寄り前の回は前の日の値なので記録しない）
  直近60営業日（＝ファイル60個）だけ残し、古いものは消す

使い方: python heatmap_intraday.py   （heatmap.yml の毎回。heatmap_fetch.py の後）
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).resolve().parent / "docs"
DST = DOCS / "heatmap_intraday"
SRC = ["heatmap.json", "heatmap_225.json", "heatmap_growth.json"]
KEEP = 60


def slot(now):
    m = now.hour * 60 + now.minute
    r = int((m + 15) // 30 * 30)          # いちばん近い :00/:30
    if m >= 15 * 60 + 45:
        r = 16 * 60                       # 引け後の回は確定値の1枚に寄せる
    return f"{r // 60:02d}:{r % 60:02d}"


def main(now=None):
    now = now or datetime.now(JST)
    try:
        main_d = json.loads((DOCS / "heatmap.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"heatmap.json を読めない（記録しない）: {e}", file=sys.stderr)
        return
    day = main_d.get("trade_date") or ""
    if day != now.strftime("%Y-%m-%d"):
        print(f"取引日 {day} が今日 {now:%Y-%m-%d} でないので記録しない（休場日・寄り前）")
        return
    t = slot(now)
    if t < "09:00":
        print(f"寄り前（{t}）なので記録しない")
        return
    c = {}
    for name in SRC:
        try:
            d = main_d if name == "heatmap.json" else json.loads((DOCS / name).read_text(encoding="utf-8"))
        except Exception:
            print(f"{name} が無い（その分は入れない）")
            continue
        if d.get("trade_date") and d["trade_date"] != day:
            print(f"{name} の取引日 {d.get('trade_date')} が違うので入れない")
            continue
        for i in d.get("items") or []:
            if i.get("t") and isinstance(i.get("c"), (int, float)):
                c[str(i["t"])] = round(float(i["c"]), 2)
    if not c:
        print("銘柄が0件なので記録しない")
        return
    DST.mkdir(parents=True, exist_ok=True)
    p = DST / f"{day}.json"
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        rec = {"date": day, "frames": []}
    frames = [f for f in rec.get("frames") or [] if f.get("t") != t]
    frames.append({"t": t, "c": dict(sorted(c.items()))})
    rec = {"date": day, "frames": sorted(frames, key=lambda f: f["t"])}
    p.write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    old = sorted(DST.glob("????-??-??.json"))[:-KEEP]
    for f in old:
        f.unlink()
    print(f"heatmap_intraday/{day}.json: {t} の回 {len(c)}銘柄（{len(rec['frames'])}枚目まで・{p.stat().st_size:,}バイト）"
          + (f"・古い記録 {len(old)}日分を消した" if old else ""))


if __name__ == "__main__":
    main()
