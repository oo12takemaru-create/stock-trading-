# -*- coding: utf-8 -*-
"""決算発表カレンダー → docs/kessan.json（株レーダー kaburadar.jp）

JPXが公表する「決算発表予定日」を集計する。上場企業が取引所に届け出た
公式の予定日なので、四季報などの推定値と違って正確。

■ ファイル構成（JPXのindexページから毎回リンクを拾う）
  kessanMM_MMDD.xlsx … MM月に四半期末/期末を迎えた会社の全リスト
  kessan.xlsx        … 直近の更新分（日付が変わった会社など）
  月別を先に読み、kessan.xlsx を後から上書きして最新状態にする。

■ 予定日は変わる
  会社の都合で前後するため毎日取り直す。「未定」の会社も21社ほどあるので
  消さずに別枠で出す（未定を消すと「決算がない」と誤解される）。

■ 決算ラッシュ日
  1日に300社超が集中する日がある。その日は個別材料が埋もれ、
  値動きが荒れやすいので、日別の社数を出して警告に使う。
"""
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from io import BytesIO
from pathlib import Path

import pandas as pd

JST = timezone(timedelta(hours=9))
DOCS = Path(__file__).parent / "docs"
OUT = DOCS / "kessan.json"

BASE = "https://www.jpx.co.jp"
INDEX = "/listing/event-schedules/financial-announcement/index.html"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0)"}

PAST_DAYS = 3        # 過去何日ぶんを残すか（「昨日の決算」を見たい人がいる）
FUTURE_DAYS = 90     # 先何日ぶんを出すか
RUSH = 150           # この社数以上を「決算ラッシュ」とみなす
W = "月火水木金土日"


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40 + i * 20) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 + i * 3)
    raise last


def find_files(html):
    """月別ファイル → kessan.xlsx の順に並べたURLリスト"""
    found = set(re.findall(r'href="(/listing/event-schedules/[^"]*?kessan[^"]*?\.xlsx)"', html))
    monthly = sorted(p for p in found if not p.endswith("/kessan.xlsx"))
    latest = [p for p in found if p.endswith("/kessan.xlsx")]
    return [BASE + p for p in monthly + latest]   # kessan.xlsx を最後に＝上書き優先


def parse(data):
    """({code: dict}, 基準日) を返す"""
    d = pd.read_excel(BytesIO(data), sheet_name=0, header=None)
    # 「2026年8月6日 現在」はindexページではなくExcelの3行目に入っている
    asof = None
    for i in range(min(5, len(d))):
        m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", str(d.iloc[i, 0]))
        if m:
            asof = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            break
    out = {}
    for _, r in d.iloc[5:].iterrows():
        code = str(r[1]).strip()
        if not re.match(r"^[0-9][0-9A-Z]{3}$", code):
            continue
        raw = r[0]
        dt = pd.to_datetime(raw, errors="coerce")
        out[code] = {
            "c": code,
            "n": str(r[2]).strip(),
            "d": None if pd.isna(dt) else dt.strftime("%Y-%m-%d"),
            "fy": (lambda v: None if pd.isna(v) else v.strftime("%Y-%m-%d"))(
                pd.to_datetime(r[4], errors="coerce")),
            "s": str(r[5]).strip() if not pd.isna(r[5]) else "",
            "q": str(r[7]).strip() if not pd.isna(r[7]) else "",
            "m": str(r[9]).strip() if not pd.isna(r[9]) else "",
        }
    return out, asof


def main():
    html = http(BASE + INDEX).decode("utf-8", "replace")
    urls = find_files(html)
    if not urls:
        print("JPXのページから決算予定ファイルを見つけられませんでした", file=sys.stderr)
        sys.exit(1)

    merged, asof = {}, None
    for u in urls:
        try:
            got, a = parse(http(u))
        except Exception as e:
            print(f"取得/解析失敗 {u.rsplit('/',1)[-1]}: {e}", file=sys.stderr)
            continue
        asof = a or asof
        merged.update(got)          # 後勝ち＝kessan.xlsx が最新
        print(f"  {u.rsplit('/',1)[-1]}: {len(got):,}社")
        time.sleep(0.5)

    if len(merged) < 100:
        print(f"社数が少なすぎます({len(merged)})。JPX側の形式変更を疑ってください", file=sys.stderr)
        sys.exit(1)

    # 監視341銘柄（主要銘柄フラグ）
    major = set()
    try:
        from daily_scanner_v2_8_0 import STOCKS
        major = {t.split(".")[0] for t in STOCKS}
    except Exception as e:
        print(f"主要銘柄リストを読めませんでした（フラグなしで続行）: {e}", file=sys.stderr)

    today = datetime.now(JST).date()
    lo = today - timedelta(days=PAST_DAYS)
    hi = today + timedelta(days=FUTURE_DAYS)

    days, undecided = {}, []
    for v in merged.values():
        v["maj"] = v["c"] in major
        if not v["d"]:
            undecided.append(v)
            continue
        dt = datetime.strptime(v["d"], "%Y-%m-%d").date()
        if lo <= dt <= hi:
            days.setdefault(v["d"], []).append(v)

    day_list = []
    for d, items in sorted(days.items()):
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        # 主要銘柄を先頭に、あとはコード順
        items.sort(key=lambda x: (not x["maj"], x["c"]))
        day_list.append({
            "d": d, "w": W[dt.weekday()],
            "n": len(items),
            "major": sum(1 for x in items if x["maj"]),
            "rush": len(items) >= RUSH,
            "past": dt < today,
            # 過去日は主要銘柄だけ残す。全部持つとJSONが倍以上に膨らむわりに
            # 「昨日どこが決算だったか」は主要銘柄しか見られないため。
            "items": [{k: x[k] for k in ("c", "n", "s", "q", "m", "maj")}
                      for x in items if dt >= today or x["maj"]],
        })

    undecided.sort(key=lambda x: x["c"])
    future = [x for x in day_list if not x["past"]]
    rush = sorted([x for x in future if x["rush"]], key=lambda x: -x["n"])[:8]

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "asof": asof,
        "today": today.strftime("%Y-%m-%d"),
        "summary": {
            "total": len(merged),
            "listed": sum(x["n"] for x in future),
            "days": len(future),
            "undecided": len(undecided),
            "rush_threshold": RUSH,
        },
        "rush": [{"d": x["d"], "w": x["w"], "n": x["n"]} for x in rush],
        "days": day_list,
        "undecided": [{k: x[k] for k in ("c", "n", "s", "q", "m", "maj")} for x in undecided],
        "note": "上場企業がJPXに届け出た決算発表予定日。会社の都合で変更されることがあります。",
    }
    DOCS.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    kb = OUT.stat().st_size // 1024
    print(f"OK kessan.json ({kb}KB): 全{len(merged):,}社 / 今後{len(future)}日に{data['summary']['listed']:,}社 "
          f"/ 未定{len(undecided)}社 / 基準日{asof}")
    for x in future[:5]:
        mark = " ★ラッシュ" if x["rush"] else ""
        print(f"  {x['d']}({x['w']}) {x['n']:>4}社  主要{x['major']:>3}社{mark}")


if __name__ == "__main__":
    main()
