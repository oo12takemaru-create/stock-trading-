# -*- coding: utf-8 -*-
"""米国株ヒートマップの対象銘柄 → data/sp500.csv / data/ndx100.csv（ticker,name,sector）

■ 出所: Wikipedia の構成銘柄の表（米国株ヒートマップ 起動文 2026-10-24）
    https://en.wikipedia.org/wiki/List_of_S%26P_500_companies   （id="constituents"）
    https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies   （id="constituents"）
  sector は S&P500 が GICS の11セクター、ナスダック100 が ICB の Industry（どちらも英語のまま。
  日本語にするのは heatmap_us.py。両方に入る銘柄は S&P500 の GICS を使う）。
■ 表が取れない・行数がおかしい（S&P500 は 495〜510、ナスダック100 は 98〜104）ときは既存の CSV を残す。
  入替は年4回（3・6・9・12月の第3金曜）＋臨時なので、古くても1日で困ることはない。
  Wikipedia が恒常的に取れなくなったら、この CSV を手で直せばよい（列は3つだけ）。
■ ticker は Yahoo の表記に直す（BRK.B → BRK-B）。
使い方: python -X utf8 us_index_list.py
"""
import csv
import io
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar/1.0; +https://kaburadar.jp)"}
LISTS = [
    # (出力, URL, ticker列, name列, 許容件数)
    ("sp500.csv", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "Symbol", "Security", (495, 510)),
    ("ndx100.csv", "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies", "Ticker", "Company", (98, 104)),
]


def fetch_table(url, tcol, ncol):
    import pandas as pd
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")
    # id="constituents" の表（無ければ ticker 列を持つ最初の表）
    try:
        tables = pd.read_html(io.StringIO(html), attrs={"id": "constituents"})
    except ValueError:
        tables = [t for t in pd.read_html(io.StringIO(html)) if tcol in t.columns]
    df = tables[0]
    scol = next(c for c in df.columns
                if ("GICS" in str(c) and "Sector" in str(c)) or ("ICB" in str(c) and "Industry" in str(c)))
    out, seen = [], set()
    for _, r in df.iterrows():
        t = str(r[tcol]).strip().replace(".", "-").upper()
        if not t or t == "NAN" or t in seen:
            continue
        seen.add(t)
        out.append((t, str(r[ncol]).strip(), str(r[scol]).strip()))
    return out


def main():
    for fname, url, tcol, ncol, (lo, hi) in LISTS:
        dst = HERE / "data" / fname
        try:
            rows = fetch_table(url, tcol, ncol)
        except Exception as e:
            print(f"{fname}: 表を取れなかった（既存を使う）: {e}", file=sys.stderr)
            continue
        if not (lo <= len(rows) <= hi):
            print(f"{fname}: {len(rows)}件（{lo}〜{hi}のはず）。様式が変わった可能性。既存を使う", file=sys.stderr)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["ticker", "name", "sector"])
            w.writerows(sorted(rows))
        print(f"{fname}: {len(rows)}銘柄")
    return 0


if __name__ == "__main__":
    sys.exit(main())
