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

    # 時価総額用の発行済株式数(十倍株スキャナーのCSVを流用。無ければ時価総額を出さない)
    SHARES = {}
    try:
        import csv
        with open("tenbagger_shares.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                code = (row.get("ticker") or row.get("code") or "").strip().replace(".T", "")
                try:
                    n = float(row.get("shares") or 0)
                except ValueError:
                    n = 0
                if code and n > 0:
                    SHARES[code] = n
        print(f"発行済株式数: {len(SHARES)}銘柄", file=sys.stderr)
    except Exception as e:
        print(f"株式数CSV読込スキップ: {e}", file=sys.stderr)

    tickers = list(STOCKS.keys())
    # 52週高値とMA200の計算に約1年分が必要（400暦日≒270営業日）
    df = yf.download(tickers, period="400d", progress=False, auto_adjust=False,
                     group_by="ticker", threads=True)

    items = []
    trade_dates = {}
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
            # 価格データの取引日(最新行の日付)
            try:
                d0 = sub.index[-1]
                dstr = d0.strftime("%Y-%m-%d")
                trade_dates[dstr] = trade_dates.get(dstr, 0) + 1
            except Exception:
                pass
            item = {
                "t": t.replace(".T", ""),
                "n": name,
                "s": sector,
                "c": round(change, 2),
                "p": round(last, 1),
                "v": round(turnover / 1e8, 1),  # 億円
            }
            # 出来高倍率(当日出来高 ÷ 直近20日平均・当日除く) = 資金流入のサイン
            vols = sub["Volume"].iloc[-21:-1].dropna()
            if len(vols) >= 10:
                avg = float(vols.mean())
                if avg > 0:
                    item["r"] = round(vol / avg, 2)
            # 期間別騰落率(1週=5営業日 / 1ヶ月=20 / 3ヶ月=60)
            for key, back in (("c5", 5), ("c20", 20), ("c60", 60)):
                if len(sub) >= back + 1:
                    pb = float(sub["Close"].iloc[-(back + 1)])
                    if pb > 0:
                        item[key] = round((last / pb - 1) * 100, 1)
            # 52週高値からの位置と移動平均線との乖離。
            # 定義は十倍株スキャナー(tenbagger_rank.py)と同じ終値ベースに揃える。
            closes = sub["Close"]
            hi52 = float(closes.iloc[-252:].max())
            if hi52 > 0:
                item["hi"] = round((last / hi52 - 1) * 100, 1)   # 0=今日が52週高値
            if len(closes) >= 25:
                ma25 = float(closes.iloc[-25:].mean())
                if ma25 > 0:
                    item["g25"] = round((last / ma25 - 1) * 100, 1)
            if len(closes) >= 200:   # 上場1年未満はMA200を出さない(誤解のもと)
                ma200 = float(closes.iloc[-200:].mean())
                if ma200 > 0:
                    item["g200"] = round((last / ma200 - 1) * 100, 1)
            # 時価総額(億円) = 終値 × 発行済株式数
            sh = SHARES.get(t.replace(".T", ""))
            if sh:
                item["m"] = round(last * sh / 1e8)
            items.append(item)
        except Exception:
            continue

    if len(items) < 200:
        print(f"取得数が少なすぎる: {len(items)}", file=sys.stderr)
        sys.exit(1)

    # 最頻値=市場全体の最新取引日
    trade_date = max(trade_dates, key=trade_dates.get) if trade_dates else None
    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "count": len(items),
        "items": items,
    }
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    up = sum(1 for i in items if i["c"] > 0)
    print(f"heatmap.json 生成: {len(items)}銘柄 取引日={trade_date} (上昇{up}/下落{len(items)-up-sum(1 for i in items if i['c']==0)})")


if __name__ == "__main__":
    main()
