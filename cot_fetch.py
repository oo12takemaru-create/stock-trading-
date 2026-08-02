"""CFTC建玉報告(投機筋ポジション)の取得 → docs/cot.json (株レーダー用)

CFTC公式のSocrata API(legacy COT, futures only)から、投機筋(non-commercial)の
ロング/ショートを取得し、直近2回分のネットポジションを公開JSONにする。
毎週金曜15:30 ET(土曜朝JST)公表 → 土曜朝に実行。

使い方: python cot_fetch.py docs/cot.json
依存: 標準ライブラリのみ
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

# (key, 表示名, market_and_exchange_names の前方一致プレフィックス候補)
TARGETS = [
    ("jpy",    "日本円",       ["JAPANESE YEN - CHICAGO"]),
    ("sp500",  "S&P500 (E-mini)", ["E-MINI S&P 500 - CHICAGO"]),
    ("nasdaq", "ナスダック100 (mini)", ["NASDAQ MINI - CHICAGO"]),
    ("nikkei", "日経平均先物(円建て)", ["NIKKEI STOCK AVERAGE YEN DENOM"]),
    ("gold",   "金",           ["GOLD - COMMODITY EXCHANGE"]),
    ("wti",    "WTI原油",      ["CRUDE OIL, LIGHT SWEET-WTI - ICE",
                                "CRUDE OIL, LIGHT SWEET - NEW YORK"]),
]


def fetch(prefixes):
    for p in prefixes:
        params = {
            "$where": f"market_and_exchange_names like '{p}%'",
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": "6",
            "$select": ("market_and_exchange_names,report_date_as_yyyy_mm_dd,"
                        "noncomm_positions_long_all,noncomm_positions_short_all,"
                        "open_interest_all"),
        }
        url = API + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kaburadar/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = json.load(r)
            if rows:
                return rows
        except Exception as e:
            print(f"取得失敗 {p}: {e}", file=sys.stderr)
    return []


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/cot.json"
    items = []
    report_date = None
    for key, label, prefixes in TARGETS:
        rows = fetch(prefixes)
        if not rows:
            print(f"データなし: {label}", file=sys.stderr)
            continue
        # 日付でグループ化(同日付が複数行になることは基本ないが念のため先頭を採用)
        by_date = {}
        for r in rows:
            d = r["report_date_as_yyyy_mm_dd"][:10]
            by_date.setdefault(d, r)
        dates = sorted(by_date.keys(), reverse=True)
        cur = by_date[dates[0]]
        prev = by_date[dates[1]] if len(dates) > 1 else None

        def net(row):
            return int(row["noncomm_positions_long_all"]) - int(row["noncomm_positions_short_all"])

        item = {
            "key": key,
            "label": label,
            "date": dates[0],
            "long": int(cur["noncomm_positions_long_all"]),
            "short": int(cur["noncomm_positions_short_all"]),
            "net": net(cur),
            "net_prev": net(prev) if prev else None,
            "change": (net(cur) - net(prev)) if prev else None,
            "open_interest": int(cur["open_interest_all"]),
        }
        items.append(item)
        if report_date is None or dates[0] > report_date:
            report_date = dates[0]

    if not items:
        print("全銘柄の取得に失敗", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "report_date": report_date,
        "source": "CFTC Commitments of Traders (legacy, futures only, non-commercial)",
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"cot.json 生成: report_date={report_date} items={len(items)}")
    for it in items:
        print(f"  {it['label']}: net {it['net']:+,} (前週比 {it['change']:+,})" if it["change"] is not None
              else f"  {it['label']}: net {it['net']:+,}")


if __name__ == "__main__":
    main()
