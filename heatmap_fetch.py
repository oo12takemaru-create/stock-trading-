"""日本株ヒートマップ用データ → docs/heatmap.json (株レーダー用)

デイリースキャナーの監視銘柄(STOCKS・341銘柄)の当日騰落率と売買代金を
一括取得してJSON化する。TradingViewの日本株ヒートマップの代替(自前版)。

使い方: python heatmap_fetch.py docs/heatmap.json
依存: yfinance, pandas
"""
import json
import sys
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/heatmap.json"
    import yfinance as yf
    from daily_scanner_v2_8_0 import STOCKS

    tickers = list(STOCKS.keys())
    df = yf.download(tickers, period="7d", progress=False, auto_adjust=False,
                     group_by="ticker", threads=True)

    items = []
    for t in tickers:
        try:
            sub = df[t].dropna(subset=["Close"])
            if len(sub) < 2:
                continue
            last = float(sub["Close"].iloc[-1])
            prev = float(sub["Close"].iloc[-2])
            vol = float(sub["Volume"].iloc[-1] or 0)
            if prev <= 0:
                continue
            change = (last / prev - 1) * 100
            turnover = last * vol  # 売買代金(円)
            name, sector = STOCKS[t]
            items.append({
                "t": t.replace(".T", ""),
                "n": name,
                "s": sector,
                "c": round(change, 2),
                "p": round(last, 1),
                "v": round(turnover / 1e8, 1),  # 億円
            })
        except Exception:
            continue

    if len(items) < 200:
        print(f"取得数が少なすぎる: {len(items)}", file=sys.stderr)
        sys.exit(1)

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "count": len(items),
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    up = sum(1 for i in items if i["c"] > 0)
    print(f"heatmap.json 生成: {len(items)}銘柄 (上昇{up}/下落{len(items)-up-sum(1 for i in items if i['c']==0)})")


if __name__ == "__main__":
    main()
