# -*- coding: utf-8 -*-
"""TDnetの当日開示一覧を貯める → docs/tdnet_list.json（直近10営業日）

決算速報（kessan_flash.py）と値動きの理由（movers）の共用部品。

■ なぜ貯めるか
  TDnetの公開一覧は約1か月（実測で27営業日）しか残らない。消える前に拾っておく。

■ 構造は実物で確認した（2026-10-03・I_list_001_20261002.html）
  行は <td class="... kjTime|kjCode|kjName|kjTitle|kjXbrl|kjPlace|kjHistroy"> の並び。
  表題セルの <a href="140120261002545289.pdf"> が短信PDF、
  XBRLセルの <a href="081220261002545289.zip"> が XBRL。**末尾の数字が共通で接頭辞だけ違う**。
  ページ送りは I_list_002_... 以降。ページ数は「全189件」÷100 で決まる。

■ TDnetへの負荷
  取得間隔は1秒。当日（と指定日）のぶんだけ。UAに kaburadar.jp を名乗る。
  失敗したら前回のファイルをそのまま残す（空で上書きしない）。

使い方:
  python -X utf8 tdnet_list.py              # 当日（JST）
  python -X utf8 tdnet_list.py 2026-09-12   # 日付指定（複数可・検証用）
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
OUT = HERE / "docs" / "tdnet_list.json"

BASE = "https://www.release.tdnet.info/inbs/"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}
KEEP_DAYS = 10          # 保持する営業日数
MAX_PAGES = 30          # 1日1,600件超の日があるので余裕をみる（1ページ100件）
SLEEP = 1.0             # TDnetへの間隔（秒）

CELL = re.compile(
    r'<td[^>]*class="[^"]*kjTime[^"]*"[^>]*>([\s\S]*?)</td>[\s\S]*?'
    r'<td[^>]*class="[^"]*kjCode[^"]*"[^>]*>([\s\S]*?)</td>[\s\S]*?'
    r'<td[^>]*class="[^"]*kjName[^"]*"[^>]*>([\s\S]*?)</td>[\s\S]*?'
    r'<td[^>]*class="[^"]*kjTitle[^"]*"[^>]*>([\s\S]*?)</td>[\s\S]*?'
    r'<td[^>]*class="[^"]*kjXbrl[^"]*"[^>]*>([\s\S]*?)</td>[\s\S]*?'
    r'<td[^>]*class="[^"]*kjPlace[^"]*"[^>]*>([\s\S]*?)</td>')


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html or "")
                  .replace("&nbsp;", " ").replace("&amp;", "&")
                  .replace("&lt;", "<").replace("&gt;", ">")).strip()


def parse_list(html):
    """一覧HTML1枚 → 開示の配列。構造が変わったら空が返るので呼び元で気づける"""
    out = []
    for m in CELL.finditer(html):
        title_cell = m.group(4)
        pdf = re.search(r'href="([^"]+\.pdf)"', title_cell, re.I)
        xbrl = re.search(r'href="([^"]+\.zip)"', m.group(5), re.I)
        title = text(title_cell)
        if not title or not pdf:
            continue
        out.append({
            "code": text(m.group(2)),
            "name": text(m.group(3)),
            "time": text(m.group(1)),
            "title": title,
            "place": text(m.group(6)),
            "pdf_url": BASE + pdf.group(1),
            "xbrl_url": BASE + xbrl.group(1) if xbrl else None,
            "id": re.sub(r"\D", "", pdf.group(1)),
        })
    return out


def fetch_day(dateISO):
    """その日の全ページを取る。1ページも取れなければ None（前回値を守るため）"""
    ymd = dateISO.replace("-", "")
    items, total, got_any = [], None, False
    for page in range(1, MAX_PAGES + 1):
        url = f"{BASE}I_list_{page:03d}_{ymd}.html"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                html = r.read().decode("utf-8", "replace")
        except Exception as e:
            if page == 1:
                print(f"  {dateISO} 1ページ目が取れない: {e}", file=sys.stderr)
                return None
            break                      # 2ページ目以降の404＝そこで終わり
        got_any = True
        rows = parse_list(html)
        if page == 1 and not rows:
            # 開示が0件の日（休場）もあるが、構造が変わった場合と区別できない。
            # 「全N件」が読めて0なら本当に0件
            m = re.search(r"全\s*([0-9,]+)\s*件", html)
            if m and int(m.group(1).replace(",", "")) > 0:
                print(f"  ⚠ {dateISO} 全{m.group(1)}件なのに1件も解析できない"
                      f"（TDnetのHTML構造が変わった可能性）", file=sys.stderr)
                return None
        items += rows
        m = re.search(r"全\s*([0-9,]+)\s*件", html)
        if not m:
            break
        total = int(m.group(1).replace(",", ""))
        if page * 100 >= total:
            break
        time.sleep(SLEEP)
    if not got_any:
        return None
    # 同じIDが複数ページに出ることはないが、念のため初出を残す
    seen, uniq = set(), []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)
    return {"d": dateISO, "count": len(uniq), "total_disclosures": total, "items": uniq}


def main():
    args = [a for a in sys.argv[1:] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", a)]
    days = args or [datetime.now(JST).strftime("%Y-%m-%d")]

    prev = {}
    if OUT.exists():
        try:
            for d in json.loads(OUT.read_text(encoding="utf-8")).get("days", []):
                prev[d["d"]] = d
        except Exception as e:
            print(f"  前回ファイルが読めない（新規として続行）: {e}", file=sys.stderr)

    ok = 0
    for d in days:
        got = fetch_day(d)
        if got is None:
            print(f"  {d}: 取得できず（前回ぶんを残す）")
            continue
        prev[d] = got
        ok += 1
        print(f"  {d}: {got['count']}件（TDnet全体 {got['total_disclosures']}件）")
        time.sleep(SLEEP)

    if not ok and OUT.exists():
        print("今回1日も取れなかったので docs/tdnet_list.json は書き換えない", file=sys.stderr)
        return 1

    keep = sorted(prev.values(), key=lambda x: x["d"])[-KEEP_DAYS:]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"updated": datetime.now(JST).isoformat(timespec="seconds"),
         "source": "TDnet（適時開示情報閲覧サービス）の当日一覧",
         "note": "TDnetの一覧は約1か月で消えるため、消える前に直近"
                 f"{KEEP_DAYS}営業日ぶんを保持している。PDF本体は再配布せずURLだけ持つ",
         "keep_days": KEEP_DAYS, "days": keep},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"→ {OUT.name}: {len(keep)}日 / 計{sum(x['count'] for x in keep)}件 "
          f"({OUT.stat().st_size / 1024:.0f}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
