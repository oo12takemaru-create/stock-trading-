# -*- coding: utf-8 -*-
"""日経平均の構成225銘柄 → data/n225.csv（code,name）

■ 出所: 日経の公表リスト https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225
  （入口強化 起動文「日経225の構成は日経の公表リストが正」2026-10-09）
■ 取れなかったとき・225件に満たないときは既存の CSV を残す（入れ替えは年2回なので古くても1日で困らない）
使い方: python -X utf8 n225_list.py
"""
import csv
import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "data" / "n225.csv"
URL = "https://indexes.nikkei.co.jp/nkave/index/component?idx=nk225"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar/1.0; +https://kaburadar.jp)"}


def main():
    try:
        s = urllib.request.urlopen(urllib.request.Request(URL, headers=UA), timeout=60).read().decode("utf-8", "replace")
    except Exception as e:
        print(f"日経225の一覧を取れなかった（既存の {OUT.name} を使う）: {e}", file=sys.stderr)
        return 0
    # 行: <td>7203</td><td><a ...>トヨタ</a></td><td>トヨタ自動車（株）</td>
    rows = re.findall(r"<tr>\s*<td>\s*(\d{3}[0-9A-Z])\s*</td>\s*<td>.*?</td>\s*<td>\s*([^<]+?)\s*</td>", s, re.S)
    seen, out = set(), []
    for code, name in rows:
        if code not in seen:
            seen.add(code)
            out.append((code, name.replace("（株）", "").replace("(株)", "").strip()))
    if len(out) != 225:
        print(f"日経225の行が{len(out)}件（225のはず）。様式が変わった可能性。既存の {OUT.name} を使う", file=sys.stderr)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["code", "name"])
        w.writerows(sorted(out))
    print(f"{OUT.name}: {len(out)}銘柄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
