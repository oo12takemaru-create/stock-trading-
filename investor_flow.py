"""JPX投資部門別売買状況(週間・東証プライム・金額) → docs/investor_flow.json (株レーダー用)

JPX公式の週間Excelから、海外投資家・個人・信託銀行などの売買代金と
ネット(買い越し/売り越し)を億円単位で抽出する。毎週木曜15時ごろ更新→木曜夕方に実行。

使い方: python investor_flow.py docs/investor_flow.json
依存: pandas, xlrd
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))
INDEX_URL = "https://www.jpx.co.jp/markets/statistics-equities/investor-type/index.html"
BASE = "https://www.jpx.co.jp"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# (Excel上のラベル前方一致, key, 表示名)
CATEGORIES = [
    ("海外投資家", "foreigners",  "海外投資家"),
    ("個",         "individuals", "個人投資家"),
    ("信託銀行",   "trust_banks", "信託銀行（年金など）"),
    ("投資信託",   "inv_trusts",  "投資信託"),
    ("事業法人",   "business",    "事業法人（自社株買いなど）"),
    ("自己",       "proprietary", "証券自己（プロップ）"),
]


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def find_latest_files():
    html = fetch(INDEX_URL).decode("utf-8", "replace")
    links = re.findall(r'href="([^"]*stock_val_1_\d+\.xls)"', html)
    if not links:
        raise RuntimeError("stock_val ファイルが見つからない")
    return [BASE + l for l in links[:2]]  # 最新週と前週


def first_number(row_vals):
    """行の中から最初の大きな数値(売買代金・千円)を返す。カンマ入り文字列にも対応"""
    for v in row_vals:
        x = None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            x = float(v)
        elif isinstance(v, str):
            s = v.replace(",", "").replace("△", "-").strip()
            if re.fullmatch(r"-?\d+(\.\d+)?", s):
                x = float(s)
        if x is not None and abs(x) > 100000:  # 比率(%)や小さい値を除外
            return x
    return None


def parse_file(url):
    import pandas as pd
    import io
    raw = fetch(url)
    df = pd.read_excel(io.BytesIO(raw), sheet_name="TSE Prime", header=None)

    # 週ラベル(例: 2026年7月第4週 2026/7 week4  ( 7/21 - 7/24 ))
    week_label = None
    for i in range(min(8, len(df))):
        for v in df.iloc[i].tolist():
            s = str(v)
            if "週" in s and "年" in s:
                week_label = re.sub(r"\s+", " ", s).strip()
                break
        if week_label:
            break

    result = {}
    n = len(df)
    for i in range(n - 1):
        label = str(df.iloc[i, 0]).replace("　", "").replace(" ", "")
        for prefix, key, disp in CATEGORIES:
            if key in result:
                continue
            if label.startswith(prefix.replace("　", "")):
                sell = first_number(df.iloc[i].tolist()[1:])
                buy = first_number(df.iloc[i + 1].tolist()[1:])
                if sell and buy:
                    net_oku = (buy - sell) / 100000  # 千円 → 億円
                    result[key] = {
                        "key": key, "label": disp,
                        "sell_oku": round(sell / 100000),
                        "buy_oku": round(buy / 100000),
                        "net_oku": round(net_oku),
                    }
    return week_label, result


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/investor_flow.json"
    urls = find_latest_files()
    week_label, latest = parse_file(urls[0])
    prev = {}
    if len(urls) > 1:
        try:
            _, prev = parse_file(urls[1])
        except Exception as e:
            print(f"前週の取得失敗(継続): {e}", file=sys.stderr)

    items = []
    for _, key, disp in CATEGORIES:
        if key not in latest:
            print(f"カテゴリ欠落: {disp}", file=sys.stderr)
            continue
        it = latest[key]
        it["prev_net_oku"] = prev.get(key, {}).get("net_oku")
        items.append(it)

    if len(items) < 4:
        print("抽出カテゴリが少なすぎるため中止", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "week": week_label,
        "market": "東証プライム(金額・委託と自己の合計)",
        "unit": "億円",
        "source": "JPX 投資部門別売買状況(週間)",
        "source_file": urls[0],
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"investor_flow.json 生成: {week_label}")
    for it in items:
        print(f"  {it['label']}: net {it['net_oku']:+,}億円")


if __name__ == "__main__":
    main()
