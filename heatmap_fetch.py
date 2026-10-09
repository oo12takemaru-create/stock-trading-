"""日本株ヒートマップ用データ → docs/heatmap.json (株レーダー用)

デイリースキャナーの監視銘柄(STOCKS・341銘柄)の当日騰落率と売買代金を
一括取得してJSON化する。TradingViewの日本株ヒートマップの代替(自前版)。

■ 2026-10-09 入口強化（起動文 A-1・A-3）で追加
  docs/heatmap_225.json     日経平均の構成225銘柄（data/n225.csv・日経の公表リスト）
  docs/heatmap_growth.json  グロース市場の売買代金上位300（前営業日の docs/prices.json で選ぶ）
  どちらも heatmap.json と同じ形・同じ回（ザラ場中5回＋引け後）に作る。業種は東証33業種。
  各銘柄に sr（信用倍率・JPX週次）と kd（次の決算発表予定日）を足す（タイルのツールチップ用）。
  ★追加分の取得に失敗しても heatmap.json は今までどおり出す（トップの数字を巻き込まない）★

使い方: python heatmap_fetch.py docs/heatmap.json
依存: yfinance, pandas
"""
import csv
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from stale_guard import keep_newest
from heatmap_abbr import abbr

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
GROWTH_TOP = 300


def load_shares():
    """時価総額用の発行済株式数(十倍株スキャナーのCSVを流用。無ければ時価総額を出さない)"""
    shares = {}
    try:
        with open("tenbagger_shares.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                code = (row.get("ticker") or row.get("code") or "").strip().replace(".T", "")
                try:
                    n = float(row.get("shares") or 0)
                except ValueError:
                    n = 0
                if code and n > 0:
                    shares[code] = n
        print(f"発行済株式数: {len(shares)}銘柄", file=sys.stderr)
    except Exception as e:
        print(f"株式数CSV読込スキップ: {e}", file=sys.stderr)
    return shares


def make_items(df, tickers, meta, shares, trade_dates):
    """yf.download の結果 → タイルの行。meta = {ticker: (name, sector)}"""
    import pandas as pd
    items = []
    for t in tickers:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            if len(sub) < 2:
                continue
            last = float(sub["Close"].iloc[-1])
            prev = float(sub["Close"].iloc[-2])
            vol = float(sub["Volume"].iloc[-1] or 0)
            if prev <= 0:
                continue
            change = (last / prev - 1) * 100
            turnover = last * vol  # 売買代金(円)
            name, sector = meta[t]
            # 価格データの取引日(最新行の日付)
            try:
                dstr = sub.index[-1].strftime("%Y-%m-%d")
                trade_dates[dstr] = trade_dates.get(dstr, 0) + 1
            except Exception:
                pass
            item = {
                "t": t.replace(".T", ""),
                "n": name,
                "a": abbr(t.replace(".T", ""), name),   # タイルが狭いときの略称（heatmap_abbr.py）
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
            sh = shares.get(t.replace(".T", ""))
            if sh:
                item["m"] = round(last * sh / 1e8)
            items.append(item)
        except Exception:
            continue
    return items


def load_facts():
    """タイルのツールチップに足す事実: 信用倍率(sr)と次の決算発表予定日(kd)"""
    sr, kd = {}, {}
    try:
        sh = json.loads((HERE / "docs" / "shinyo_meigara.json").read_text(encoding="utf-8"))
        for i in sh.get("items") or []:
            if i.get("s") and i.get("b") is not None:
                sr[str(i["c"])] = round(i["b"] / i["s"], 2)
    except Exception as e:
        print(f"信用残の読込スキップ: {e}", file=sys.stderr)
    try:
        k = json.loads((HERE / "docs" / "kessan.json").read_text(encoding="utf-8"))
        today = datetime.now(JST).strftime("%Y-%m-%d")
        for day in k.get("days") or []:
            if day.get("d", "") < today:
                continue
            for it in day.get("items") or []:
                kd.setdefault(str(it.get("c")), day["d"])
    except Exception as e:
        print(f"決算予定の読込スキップ: {e}", file=sys.stderr)
    return sr, kd


def enrich(items, sr, kd):
    for i in items:
        if i["t"] in sr:
            i["sr"] = sr[i["t"]]
        if i["t"] in kd:
            i["kd"] = kd[i["t"]]


def write_map(dst, items, trade_dates, label, floor):
    if len(items) < floor:
        print(f"{label}: 取得数が少なすぎる {len(items)}（{floor}未満）。書かない", file=sys.stderr)
        return False
    trade_date = max(trade_dates, key=trade_dates.get) if trade_dates else None
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "trade_date": trade_date, "count": len(items), "items": items}
    if not keep_newest(dst, out, "trade_date", label):
        return False
    up = sum(1 for i in items if i["c"] > 0)
    dn = sum(1 for i in items if i["c"] < 0)
    print(f"{Path(dst).name} 生成: {len(items)}銘柄 取引日={trade_date} (上昇{up}/下落{dn})")
    return True


def universe():
    """{code: (name, sector33, market)}（tenbagger_universe.csv・JPX公式一覧から毎週作り直し）"""
    u = {}
    with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            u[r["code"].strip()] = ((r.get("name") or "").strip(), (r.get("sector33") or "").strip(),
                                    (r.get("market") or "").strip())
    return u


def extra_maps(yf, shares, sr, kd):
    """日経225とグロース上位300（heatmap_225.json / heatmap_growth.json）"""
    import unicodedata
    u = universe()
    nk = []
    with open(HERE / "data" / "n225.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            nk.append(r["code"])
    # グロースは前営業日の売買代金で上位300を選ぶ（prices.json は全銘柄の終値・18時台に更新）
    pr = json.loads((HERE / "docs" / "prices.json").read_text(encoding="utf-8")).get("items") or {}
    gr = sorted((c for c, v in u.items() if v[2] == "グロース" and c in pr and (pr[c].get("v") or 0) > 0),
                key=lambda c: -(pr[c].get("v") or 0))[:GROWTH_TOP]

    def name(c, fallback=""):
        n = (u.get(c) or (fallback, "", ""))[0] or fallback
        return unicodedata.normalize("NFKC", n)

    meta = {}
    for c in nk + gr:
        meta[c + ".T"] = (name(c, c), (u.get(c) or ("", "その他", ""))[1] or "その他")
    tickers = sorted(meta)
    df = yf.download(tickers, period="400d", progress=False, auto_adjust=False, group_by="ticker", threads=True)
    for key, codes, dst, floor in (("日経225", nk, "docs/heatmap_225.json", 200),
                                   ("グロース", gr, "docs/heatmap_growth.json", 200)):
        td = {}
        items = make_items(df, [c + ".T" for c in codes], meta, shares, td)
        enrich(items, sr, kd)
        write_map(dst, items, td, f"ヒートマップ（{key}）", floor)


def main():
    dst = sys.argv[1] if len(sys.argv) > 1 else "docs/heatmap.json"
    import yfinance as yf
    from daily_scanner_v2_8_0 import STOCKS

    shares = load_shares()
    sr, kd = load_facts()
    tickers = list(STOCKS.keys())
    # 52週高値とMA200の計算に約1年分が必要（400暦日≒270営業日）
    df = yf.download(tickers, period="400d", progress=False, auto_adjust=False,
                     group_by="ticker", threads=True)
    trade_dates = {}
    items = make_items(df, tickers, STOCKS, shares, trade_dates)
    if len(items) < 200:
        print(f"取得数が少なすぎる: {len(items)}", file=sys.stderr)
        sys.exit(1)
    enrich(items, sr, kd)
    # 既に出ている取引日より古ければ書かない（stale_guard 参照）
    write_map(dst, items, trade_dates, "ヒートマップ", 200)

    # 追加分（日経225・グロース）。失敗しても上の heatmap.json には影響させない
    try:
        extra_maps(yf, shares, sr, kd)
    except Exception as e:
        print(f"::warning::日経225/グロースのヒートマップを作れなかった: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
