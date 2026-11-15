# -*- coding: utf-8 -*-
"""決算発表カレンダー → docs/kessan.json（株レーダー kaburadar.jp）

JPXが公表する「決算発表予定日」を集計する。上場企業が取引所に届け出た
公式の予定日なので、四季報などの推定値と違って正確。

■ ファイル構成（JPXのindexページから毎回リンクを拾う）
  kessanMM_MMDD.xlsx … MM月に四半期末/期末を迎えた会社の全リスト
  kessan.xlsx        … 直近の更新分（日付が変わった会社など）
  月別を先に読み、kessan.xlsx を後から上書きして最新状態にする。

■ 基準日は2種類ある（2026-11-15 調査）
  月別ファイル … Excelの3行目に「2026年11月5日 現在」。JPXが「期末の翌月上旬に掲載し、
                  その後適宜更新」するもので、更新は不定期（月に数回のこともある）
  kessan.xlsx  … 「毎営業日 午後5時頃に、3月期・9月期決算会社の翌営業日分を更新」。
                  中に「現在」の日付は無い。indexページの「YYYY/MM/DD 更新」が実質の基準
  asof は月別ファイルの基準日（従来どおり）。翌営業日分の鮮度は jpx.page_updated / jpx.daily_for。
  月別の基準日が7日以上古いとログに警告を出す（こちらの取得ではなくJPX側の更新待ちのことが多い）

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
STALE_DAYS = 7       # 月別ファイルの基準日がこれ以上古ければ警告
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


def page_updated(html):
    """indexページの「2026/11/13 更新」→ '2026-11-13'（無ければ None）"""
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*更新", html)
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


def warn(msg):
    """GitHub Actions の注釈（::warning::）と標準エラーの両方に出す"""
    print(f"::warning::{msg}")
    print(f"警告: {msg}", file=sys.stderr)


def main():
    html = http(BASE + INDEX).decode("utf-8", "replace")
    pupd = page_updated(html)
    urls = find_files(html)
    if not urls:
        print("JPXのページから決算予定ファイルを見つけられませんでした", file=sys.stderr)
        sys.exit(1)

    merged, asof, files, daily_for = {}, None, [], None
    for u in urls:
        name = u.rsplit('/', 1)[-1]
        try:
            got, a = parse(http(u))
        except Exception as e:
            print(f"取得/解析失敗 {name}: {e}", file=sys.stderr)
            continue
        if a and (asof is None or a > asof):
            asof = a                # 月別ファイルの基準日（複数あれば新しい方）
        if name == "kessan.xlsx":
            ds = sorted({v["d"] for v in got.values() if v["d"]})
            daily_for = ds[-1] if ds else None
        files.append({"f": name, "asof": a, "n": len(got)})
        merged.update(got)          # 後勝ち＝kessan.xlsx が最新
        print(f"  {name}: {len(got):,}社" + (f"（{a} 現在）" if a else ""))
        time.sleep(0.5)

    if len(merged) < 100:
        print(f"社数が少なすぎます({len(merged)})。JPX側の形式変更を疑ってください", file=sys.stderr)
        sys.exit(1)

    # 監視341銘柄（主要銘柄フラグ）
    major = set()
    try:
        from daily_scanner_v2_8_0 import STOCKS
        major = {t.split(".")[0] for t in STOCKS}
    except BaseException as e:
        # スキャナーはyfinance等が無いとimport時にsys.exit(1)する。
        # SystemExitはExceptionの子ではないので、BaseExceptionで受けないと
        # このtryをすり抜けてプロセスごと死ぬ（2026-08-17のCI障害の原因）
        print(f"主要銘柄リストを読めませんでした（フラグなしで続行）: {e}", file=sys.stderr)

    today = datetime.now(JST).date()
    age = (today - datetime.strptime(asof, "%Y-%m-%d").date()).days if asof else None
    if asof is None:
        warn("JPXの月別ファイルから基準日（〇年〇月〇日 現在）を読めませんでした。形式変更を疑ってください")
    elif age >= STALE_DAYS:
        warn(f"JPXの月別ファイルの基準日が {asof}（{age}日前）。JPX側の更新待ちの可能性が高い"
             f"（翌営業日分 kessan.xlsx: ページ更新 {pupd or '不明'}・{daily_for or '不明'} 分）")
    if not any(f["f"] == "kessan.xlsx" for f in files):
        warn("翌営業日分（kessan.xlsx）を取れませんでした。直前の変更が反映されていない可能性があります")

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
        # JPX側の鮮度（2026-11-15）。asof＝月別ファイルの基準日、page_updated＝JPXのページの更新日、
        # daily_for＝翌営業日分ファイルが対象にしている開示日、fetched＝こちらが取りに行った時刻
        "jpx": {
            "asof_monthly": asof,
            "asof_age_days": age,
            "stale": bool(age is not None and age >= STALE_DAYS),
            "page_updated": pupd,
            "daily_for": daily_for,
            "fetched": datetime.now(JST).isoformat(timespec="seconds"),
            "files": files,
        },
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
          f"/ 未定{len(undecided)}社 / 基準日{asof}（{age}日前）"
          f" / JPXページ更新{pupd} / 翌営業日分={daily_for}")
    for x in future[:5]:
        mark = " ★ラッシュ" if x["rush"] else ""
        print(f"  {x['d']}({x['w']}) {x['n']:>4}社  主要{x['major']:>3}社{mark}")


if __name__ == "__main__":
    main()
