# -*- coding: utf-8 -*-
"""イベントの日付表を作る → event_dates.json

■ 原則: 推測で日付を作らない。1件ごとに出所(source)を残す
  「たぶん中旬」で埋めると、検証の結論が静かに壊れる。取れない年は対象外にする。

■ 出所
  日銀会合   : 日本銀行「過去の金融政策決定会合の開催日等」
               https://www.boj.or.jp/mopo/mpmsche_minu/past.htm
               議事要旨PDFのファイル名 gYYMMDD.pdf が会合最終日そのものなので、それを拾う
  FOMC       : FRB の FOMC calendar / historical calendar
               https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
               https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm
  米CPI/雇用 : BLS の公式リリース日程（年別）
               https://www.bls.gov/schedule/{year}/home.htm
               ※BLSはbot対策で素のHTTPクライアントに403を返す。ブラウザで取得した結果を
                 下の BLS_CPI / BLS_EMP に貼ってある。更新は年1回で足りる
  メジャーSQ : 3・6・9・12月の第2金曜（機械算出。休場なら直前の営業日）
  権利確定日 : 3月末・9月末の最終営業日から受渡日数を戻して算出
               2019-07-16 以降は T+2、それ以前は T+3（JPXの受渡期間短縮）

使い方: python -X utf8 event_dates.py
"""
import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "event_dates.json"
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar/1.0)"}

YEAR_FROM = 2013          # 検証の開始年（価格は2010年から取るが、日付の出所が揃うのはここから）
T2_FROM = date(2019, 7, 16)   # 受渡がT+3→T+2になった日（JPX）


def http(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=60).read().decode("utf-8", "replace")


# ------------------------------------------------------------------ 日銀
def boj():
    """議事要旨PDFのファイル名から会合最終日を拾う。
    past.htm は 2010年以降、index.htm は当年ぶん"""
    out = {}
    for url in ("https://www.boj.or.jp/mopo/mpmsche_minu/past.htm",
                "https://www.boj.or.jp/mopo/mpmsche_minu/index.htm"):
        try:
            s = http(url)
        except Exception as e:
            print(f"  日銀 取得失敗 {url}: {e}", file=sys.stderr)
            continue
        # g251219.pdf / opi251219.pdf → 2025-12-19
        for m in re.finditer(r'/(?:g|opi)(\d{2})(\d{2})(\d{2})\.pdf', s):
            y, mo, d = 2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                out[date(y, mo, d).isoformat()] = url
            except ValueError:
                continue
    return out


# ------------------------------------------------------------------ FOMC
MONTHS = {}
for _i, _m in enumerate(["January", "February", "March", "April", "May", "June",
                         "July", "August", "September", "October", "November", "December"]):
    MONTHS[_m] = _i + 1
    MONTHS[_m[:3]] = _i + 1      # 過去年ページは "Jan/Feb 31-1" のように略称を使う


def fomc():
    """結果発表日（会合の最終日）。
    ページ全体から声明リンクを拾うと緊急対応や別件の声明まで混ざるので、
    必ず「1会合＝1ブロック」の単位で拾う"""
    out = {}

    # 1) 現行カレンダー: class に fomc-meeting を含む div が1会合
    cal = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
    try:
        s = http(cal)
        for ym in re.finditer(r'(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|\Z)', s, re.S):
            year, body = int(ym.group(1)), ym.group(2)
            # 行のclassは "row fomc-meeting" と "fomc-meeting--shaded row fomc-meeting" の2通り
            blocks = re.split(r'(?=<div class="(?:fomc-meeting--shaded )?row fomc-meeting")', body)
            for b in blocks:
                if 'fomc-meeting__month' not in b:
                    continue
                da0 = re.search(r'fomc-meeting__date[^>]*>\s*([0-9\-*\s]+)', b)
                # 定例会合は必ず2日間（"28-29" や月をまたぐ "31-1"）。
                # 単日の行は臨時の声明（例: 2025-08-22 の長期目標の改定）なので対象外にする
                if not da0 or '-' not in da0.group(1):
                    continue
                # 声明リンクの日付が最も確か（月をまたぐ会合でも正しい）
                pdf = re.search(r'monetary(\d{8})', b)
                if pdf:
                    d = pdf.group(1)
                    out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = cal
                    continue
                # まだ開催前で声明が無い会合は、月と日の表記から組む
                mo = re.search(r'fomc-meeting__month[^>]*>(?:<strong>)?\s*([A-Za-z/]+)', b)
                da = re.search(r'fomc-meeting__date[^>]*>\s*([0-9\-*\s]+)', b)
                if mo and da:
                    last = _last_day(year, mo.group(1), da.group(1))
                    if last:
                        out[last.isoformat()] = cal
    except Exception as e:
        print(f"  FOMC 現行カレンダー失敗: {e}", file=sys.stderr)

    # 2) 過去年ページ: 見出し "Jan/Feb 31-1 Meeting - 2017" が1会合
    for y in range(YEAR_FROM, date.today().year + 1):
        url = f"https://www.federalreserve.gov/monetarypolicy/fomchistorical{y}.htm"
        try:
            s = http(url)
        except Exception:
            continue
        # 見出しは "Jan/Feb 31-1 Meeting - 2017"。定例は2日間なので日付に必ずハイフンが入る
        for m in re.finditer(r'([A-Z][a-z]{2,8})(?:/([A-Z][a-z]{2,8}))?\s+(\d+\s*-\s*\d+)\s*Meeting\s*-\s*(\d{4})', s):
            mon = m.group(1) + ("/" + m.group(2) if m.group(2) else "")
            last = _last_day(int(m.group(4)), mon, m.group(3))
            if last:
                out.setdefault(last.isoformat(), url)
    return out


def _last_day(year, mon, dd):
    """"Jan/Feb" + "31-1" → 2月1日。最終日（＝結果発表日）を返す"""
    dd = dd.replace("*", "").strip()
    nums = [int(x) for x in re.findall(r'\d+', dd)]
    parts = [p for p in mon.split("/") if p in MONTHS]
    if not nums or not parts:
        return None
    m = MONTHS[parts[-1]]
    y = year + 1 if (len(parts) == 2 and MONTHS[parts[0]] == 12 and m == 1) else year
    try:
        return date(y, m, nums[-1])
    except ValueError:
        return None


# ------------------------------------------------------------------ BLS（ブラウザで取得済み）
BLS_SRC = "https://www.bls.gov/schedule/{year}/home.htm"
BLS_CPI = """2013-01-16 2013-02-21 2013-03-15 2013-04-16 2013-05-16 2013-06-18 2013-07-16 2013-08-15
2013-09-17 2013-10-30 2013-11-20 2013-12-17 2014-01-16 2014-02-20 2014-03-18 2014-04-15 2014-05-15
2014-06-17 2014-07-22 2014-08-19 2014-09-17 2014-10-22 2014-11-20 2014-12-17 2015-01-16 2015-02-26
2015-03-24 2015-04-17 2015-05-22 2015-06-18 2015-07-17 2015-08-19 2015-09-16 2015-10-15 2015-11-17
2015-12-15 2016-01-20 2016-02-19 2016-03-16 2016-04-14 2016-05-17 2016-06-16 2016-07-15 2016-08-16
2016-09-16 2016-10-18 2016-11-17 2016-12-15 2017-01-18 2017-02-15 2017-03-15 2017-04-14 2017-05-12
2017-06-14 2017-07-14 2017-08-11 2017-09-14 2017-10-13 2017-11-15 2017-12-13 2018-01-12 2018-02-14
2018-03-13 2018-04-11 2018-05-10 2018-06-12 2018-07-12 2018-08-10 2018-09-13 2018-10-11 2018-11-14
2018-12-12 2019-01-11 2019-02-13 2019-03-12 2019-04-10 2019-05-10 2019-06-12 2019-07-11 2019-08-13
2019-09-12 2019-10-10 2019-11-13 2019-12-11 2020-01-14 2020-02-13 2020-03-11 2020-04-10 2020-05-12
2020-06-10 2020-07-14 2020-08-12 2020-09-11 2020-10-13 2020-11-12 2020-12-10 2021-01-13 2021-02-10
2021-03-10 2021-04-13 2021-05-12 2021-06-10 2021-07-13 2021-08-11 2021-09-14 2021-10-13 2021-11-10
2021-12-10 2022-01-12 2022-02-10 2022-03-10 2022-04-12 2022-05-11 2022-06-10 2022-07-13 2022-08-10
2022-09-13 2022-10-13 2022-11-10 2022-12-13 2023-01-12 2023-02-14 2023-03-14 2023-04-12 2023-05-10
2023-06-13 2023-07-12 2023-08-10 2023-09-13 2023-10-12 2023-11-14 2023-12-12 2024-01-11 2024-02-13
2024-03-12 2024-04-10 2024-05-15 2024-06-12 2024-07-11 2024-08-14 2024-09-11 2024-10-10 2024-11-13
2024-12-11 2025-01-15 2025-02-12 2025-03-12 2025-04-10 2025-05-13 2025-06-11 2025-07-15 2025-08-12
2025-09-11 2025-10-24 2025-12-18"""
BLS_EMP = """2013-01-04 2013-02-01 2013-03-08 2013-04-05 2013-05-03 2013-06-07 2013-07-05 2013-08-02
2013-09-06 2013-10-22 2013-11-08 2013-12-06 2014-01-10 2014-02-07 2014-03-07 2014-04-04 2014-05-02
2014-06-06 2014-07-03 2014-08-01 2014-09-05 2014-10-03 2014-11-07 2014-12-05 2015-01-09 2015-02-06
2015-03-06 2015-04-03 2015-05-08 2015-06-05 2015-07-02 2015-08-07 2015-09-04 2015-10-02 2015-11-06
2015-12-04 2016-01-08 2016-02-05 2016-03-04 2016-04-01 2016-05-06 2016-06-03 2016-07-08 2016-08-05
2016-09-02 2016-10-07 2016-11-04 2016-12-02 2017-01-06 2017-02-03 2017-03-10 2017-04-07 2017-05-05
2017-06-02 2017-07-07 2017-08-04 2017-09-01 2017-10-06 2017-11-03 2017-12-08 2018-01-05 2018-02-02
2018-03-09 2018-04-06 2018-05-04 2018-06-01 2018-07-06 2018-08-03 2018-09-07 2018-10-05 2018-11-02
2018-12-07 2019-01-04 2019-02-01 2019-03-08 2019-04-05 2019-05-03 2019-06-07 2019-07-05 2019-08-02
2019-09-06 2019-10-04 2019-11-01 2019-12-06 2020-01-10 2020-02-07 2020-03-06 2020-04-03 2020-05-08
2020-06-05 2020-07-02 2020-08-07 2020-09-04 2020-10-02 2020-11-06 2020-12-04 2021-01-08 2021-02-05
2021-03-05 2021-04-02 2021-05-07 2021-06-04 2021-07-02 2021-08-06 2021-09-03 2021-10-08 2021-11-05
2021-12-03 2022-01-07 2022-02-04 2022-03-04 2022-04-01 2022-05-06 2022-06-03 2022-07-08 2022-08-05
2022-09-02 2022-10-07 2022-11-04 2022-12-02 2023-01-06 2023-02-03 2023-03-10 2023-04-07 2023-05-05
2023-06-02 2023-07-07 2023-08-04 2023-09-01 2023-10-06 2023-11-03 2023-12-08 2024-01-05 2024-02-02
2024-03-08 2024-04-05 2024-05-03 2024-06-07 2024-07-05 2024-08-02 2024-09-06 2024-10-04 2024-11-01
2024-12-06 2025-01-10 2025-02-07 2025-03-07 2025-04-04 2025-05-02 2025-06-06 2025-07-03 2025-08-01
2025-09-05 2025-11-20 2025-12-16"""


def main():
    ev = {}

    d = boj()
    ev["boj"] = {"label": "日銀 金融政策決定会合", "react": "same",
                 "note": "結果発表は会合最終日の昼ごろ。当日の引けで反応する",
                 "dates": [{"d": k, "source": v} for k, v in sorted(d.items()) if k[:4] >= str(YEAR_FROM)]}

    d = fomc()
    ev["fomc"] = {"label": "FOMC", "react": "next",
                  "note": "結果発表は米国時間の午後。日本では翌営業日の寄りから反応する",
                  "dates": [{"d": k, "source": v} for k, v in sorted(d.items()) if k[:4] >= str(YEAR_FROM)]}

    for key, label, raw in (("cpi", "米CPI", BLS_CPI), ("payroll", "米雇用統計", BLS_EMP)):
        ds = sorted(set(raw.split()))
        ev[key] = {"label": label, "react": "next",
                   "note": "発表は米国時間8:30（日本時間21:30／冬22:30）。翌営業日で反応する",
                   "dates": [{"d": x, "source": BLS_SRC.format(year=x[:4])} for x in ds]}

    OUT.write_text(json.dumps(ev, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in ev.items():
        ds = [x["d"] for x in v["dates"]]
        print(f"{k:8s} {len(ds):4d}件  {ds[0]} 〜 {ds[-1]}" if ds else f"{k:8s} 0件")
    print(f"→ {OUT.name}")


if __name__ == "__main__":
    main()
