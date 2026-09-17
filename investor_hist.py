# -*- coding: utf-8 -*-
"""投資部門別売買状況の週次履歴を再構築する → docs/investor_hist.json

■ なぜ作るか
  investor_flow.json は「今週と前週」の2点しか持っていない。
  AI検索から来る質問は「誰が買っているか」に集中しているのに、2点では答えられない。
  JPXの年別アーカイブを辿れば週次の履歴を作れる。

■ 出所
  JPX「投資部門別売買状況」の年別アーカイブ
  https://www.jpx.co.jp/markets/statistics-equities/investor-type/00-00-archives-NN.html
  ファイル名 stock_val_1_YYMMWW.xls が「YY年MM月第WW週」を表す。
  **アーカイブは2016年までしか遡れない**（archives-11 以降は404）。
  取れない年は取らない。埋めない。

■ 市場区分が2022年4月に変わっている
  2022-04-04 の再編で「東証1部」→「プライム」。Excelのシート名も TSE 1st → TSE Prime。
  構成銘柄が違うので、**黙って1本の系列として繋がない**。
  JSONの meta に変更日を持たせ、画面でも分かるようにする。

■ 既存の investor_flow.py は無変更。パースの作法（カンマ入り文字列・千円→億円）だけ合わせる
"""
import io
import json
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
CACHE = HERE / "investor_cache"
OUT = HERE / "docs" / "investor_hist.json"
BASE = "https://www.jpx.co.jp"
INDEX = "/markets/statistics-equities/investor-type/index.html"
ARCHIVE = "/markets/statistics-equities/investor-type/00-00-archives-{n:02d}.html"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 市場区分の変更日（東証1部 → プライム）
REORG = "2022-04-04"

CATEGORIES = [
    ("海外投資家", "foreigners",  "海外投資家"),
    ("個",         "individuals", "個人投資家"),
    ("信託銀行",   "trust_banks", "信託銀行（年金など）"),
    ("投資信託",   "inv_trusts",  "投資信託"),
    ("事業法人",   "business",    "事業法人（自社株買いなど）"),
    ("自己",       "proprietary", "証券自己（プロップ）"),
]


def http(url, tries=3):
    for i in range(tries):
        try:
            return urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=60).read()
        except Exception as e:
            if i == tries - 1:
                raise
            print(f"  再試行{i+1} {url}: {e}", file=sys.stderr)
            time.sleep(3 * (i + 1))


def links():
    """最新ページ＋年別アーカイブから、週次xlsのURLを全部集める"""
    found = {}
    pages = [INDEX] + [ARCHIVE.format(n=n) for n in range(0, 11)]
    for p in pages:
        try:
            s = http(BASE + p).decode("utf-8", "replace")
        except Exception as e:
            print(f"  ページ取得失敗 {p}: {e}", file=sys.stderr)
            continue
        n = 0
        for m in re.finditer(r'href="([^"]*stock_val_1_(\d{6})\.xls)"', s):
            found.setdefault(m.group(2), BASE + m.group(1))
            n += 1
        print(f"  {p.split('/')[-1]:28s} {n}件")
        time.sleep(0.4)
    return found


def first_number(vals):
    """行から最初の大きな数値（売買代金・千円）を返す。カンマ入り文字列にも対応"""
    for v in vals:
        x = None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            x = float(v)
        elif isinstance(v, str):
            s = v.replace(",", "").replace("△", "-").strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", s):
                x = float(s)
        if x is not None and abs(x) > 100000:
            return x
    return None


def sheet_for(xl):
    """東証1部（〜2022年3月）と プライム（2022年4月〜）でシート名が違う"""
    for want in ("TSE Prime", "TSE 1st", "TSE 1st section"):
        for s in xl.sheet_names:
            if s.strip().lower() == want.lower():
                return s
    return xl.sheet_names[0]


def parse(raw, code):
    import pandas as pd
    xl = pd.ExcelFile(io.BytesIO(raw))
    sh = sheet_for(xl)
    df = xl.parse(sheet_name=sh, header=None)

    label = None
    for i in range(min(8, len(df))):
        for v in df.iloc[i].tolist():
            s = str(v)
            if "週" in s and "年" in s:
                label = re.sub(r"\s+", " ", s).strip()
                break
        if label:
            break

    # ラベル末尾の "( 9/1 - 9/4 )" から週の開始・終了日を作る（年はファイル名から）
    y = 2000 + int(code[:2])
    start = end = None
    m = re.search(r"\(\s*(\d{1,2})/(\d{1,2})\s*-\s*(\d{1,2})/(\d{1,2})\s*\)", label or "")
    if m:
        m1, d1, m2, d2 = (int(x) for x in m.groups())
        try:
            start = date(y, m1, d1).isoformat()
            # 年をまたぐ週（12/29 - 1/4 のような形）
            end = date(y + 1 if m2 < m1 else y, m2, d2).isoformat()
        except ValueError:
            start = end = None

    res = {}
    for i in range(len(df) - 1):
        lab = str(df.iloc[i, 0]).replace("　", "").replace(" ", "")
        for prefix, key, _ in CATEGORIES:
            if key in res:
                continue
            if lab.startswith(prefix):
                sell = first_number(df.iloc[i].tolist()[1:])
                buy = first_number(df.iloc[i + 1].tolist()[1:])
                if sell and buy:
                    res[key] = round((buy - sell) / 100000)   # 千円 → 億円
    return {"code": code, "label": label, "start": start, "end": end,
            "sheet": sh, "net": res}


def main():
    CACHE.mkdir(exist_ok=True)
    print("JPXの週次ファイルを集める")
    urls = links()
    print(f"合計 {len(urls)}週ぶんのファイル")

    weeks, bad = [], []
    for i, code in enumerate(sorted(urls), 1):
        f = CACHE / f"{code}.xls"
        if not f.exists():
            try:
                f.write_bytes(http(urls[code]))
            except Exception as e:
                bad.append((code, str(e)[:60]))
                continue
            time.sleep(0.4)          # JPXへの礼儀
        try:
            w = parse(f.read_bytes(), code)
        except Exception as e:
            bad.append((code, str(e)[:60]))
            continue
        if len(w["net"]) < 4 or not w["start"]:
            bad.append((code, f"抽出不足 {len(w['net'])}主体 / start={w['start']}"))
            continue
        w["src"] = urls[code]
        weeks.append(w)
        if i % 60 == 0:
            print(f"  {i}/{len(urls)} 済み（採用{len(weeks)}・除外{len(bad)}）", flush=True)

    weeks.sort(key=lambda w: w["start"])
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "JPX 投資部門別売買状況（週間・金額・委託と自己の合計）",
        "source_index": BASE + INDEX,
        "unit": "億円",
        "market_note": ("2022年4月4日の市場再編で対象が「東証1部」から「プライム」に変わりました。"
                        "構成銘柄が違うため、同じ系列として扱う場合は注意が必要です。"),
        "market_change": REORG,
        "categories": [{"key": k, "label": d} for _, k, d in CATEGORIES],
        "coverage": {"weeks": len(weeks),
                     "from": weeks[0]["start"] if weeks else None,
                     "to": weeks[-1]["end"] if weeks else None,
                     "skipped": len(bad),
                     "note": "JPXのアーカイブは2016年までしか遡れません。それ以前は取得していません"},
        "weeks": [{"s": w["start"], "e": w["end"], "m": ("prime" if w["start"] >= REORG else "tse1"),
                   "net": w["net"]} for w in weeks],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"\n採用 {len(weeks)}週 / 除外 {len(bad)}週")
    if bad:
        for c, e in bad[:10]:
            print(f"  除外 {c}: {e}")
    if weeks:
        print(f"期間 {weeks[0]['start']} 〜 {weeks[-1]['end']}")
        print(f"区分 東証1部 {sum(1 for w in out['weeks'] if w['m']=='tse1')}週 / "
              f"プライム {sum(1 for w in out['weeks'] if w['m']=='prime')}週")
    print(f"→ {OUT.name} ({OUT.stat().st_size/1024:.1f}KB)")


if __name__ == "__main__":
    main()
