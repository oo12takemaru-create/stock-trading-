# -*- coding: utf-8 -*-
"""米国株ヒートマップ用データ → docs/heatmap_us.json（株レーダー・2026-10-24）

■ 何を出すか（日本株の heatmap.json と同じ列の形。サイトの描画 JS は1つのまま）
  対象: S&P500 の構成銘柄（data/sp500.csv・約503）＋ ナスダック100（data/ndx100.csv）。
        重複は1つにまとめ、x で所属を持つ（1=S&P500だけ / 2=ナスダック100だけ / 3=両方）。サイトの切替で絞る。
  t  ティッカー（Yahoo の表記。BRK-B など）  n  日本語名（data/us_jp_links.json にあれば）か英語名
  en 英語名（n が日本語のときだけ）           s  GICS 11業種の日本語名
  c  前日比%  c5 / c20 / c60  5・20・60営業日前比%  r  出来高倍率（当日÷直近20日平均）
  p  終値（ドル）  hi 52週高値からの位置%
  v  売買代金（億円・その回のドル円で換算）  vu 売買代金（億ドル）
  m  時価総額（億円）  mu 時価総額（億ドル）  ※株数は yfinance の fast_info（取れない銘柄は出さない）
  jp 関連する日本株 [[コード, 名前, 根拠1行], ...]（data/us_jp_links.json。推奨ではなく関係の事実だけ）
  先頭: trade_date（米国東部の取引日）・session（close=引け後の確定値 / open=場中の途中経過）・
        fx（ドル円）・idx（NYダウ・ナスダック総合・S&P500・SOX指数）・sectors_jp（業種ごとの関連する日本株）

■ 日付アーカイブ（確定値の回だけ）: docs/heatmap_us_days/YYYY-MM-DD.json と index.json
  サイトの heatmap-us/YYYY-MM-DD.html はこれから焼く（gen_heatmaps.py）。過去分は作らない（今日から積む）。

■ データの出所は日本株と同じ yfinance（日足・前日比・出来高）。ドル円も既存と同じ JPY=X。新しい有料データは使わない。
■ 「リアルタイム」とは書かない。場中の回は「途中経過」。

使い方: python -X utf8 heatmap_us.py   （heatmap-us.yml・cron-worker が米国の場中2回＋引け後2回起こす）
依存: yfinance, pandas
"""
import csv
import json
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stale_guard import keep_newest
from us_bizday import ET, now_et, session_closed

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
DAYS = DOCS / "heatmap_us_days"
DST = DOCS / "heatmap_us.json"
FLOOR = 450          # S&P500 のうちこれ未満しか取れなければ書かない

SECTOR_JA = {
    # GICS（S&P500）
    "Information Technology": "情報技術", "Health Care": "ヘルスケア", "Financials": "金融",
    "Consumer Discretionary": "一般消費財", "Communication Services": "コミュニケーション",
    "Industrials": "資本財", "Consumer Staples": "生活必需品", "Energy": "エネルギー",
    "Utilities": "公益", "Real Estate": "不動産", "Materials": "素材",
    # ICB（ナスダック100 だけの銘柄）
    "Technology": "情報技術", "Telecommunications": "コミュニケーション", "Basic Materials": "素材",
}
INDEXES = [("dji", "NYダウ", "^DJI"), ("ixic", "ナスダック総合", "^IXIC"),
           ("spx", "S&P500", "^GSPC"), ("sox", "SOX指数（半導体）", "^SOX")]


def read_list(name):
    with open(HERE / "data" / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def universe():
    """{ticker: {"en", "s", "x"}}"""
    u = {}
    for r in read_list("sp500.csv"):
        u[r["ticker"]] = {"en": r["name"], "s": SECTOR_JA.get(r["sector"], "その他"), "x": 1}
    for r in read_list("ndx100.csv"):
        t = r["ticker"]
        if t in u:
            u[t]["x"] = 3
        else:
            u[t] = {"en": r["name"], "s": SECTOR_JA.get(r["sector"], "その他"), "x": 2}
    return u


def jp_names():
    out = {}
    try:
        with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                out[r["code"].strip()] = unicodedata.normalize("NFKC", (r.get("name") or "").strip())
    except Exception as e:
        print(f"日本株の名前を読めない: {e}", file=sys.stderr)
    return out


def links(names):
    """data/us_jp_links.json → ({ticker: (ja, [[c, n, w]])}, {業種: [[c, n, w]]})"""
    d = json.loads((HERE / "data" / "us_jp_links.json").read_text(encoding="utf-8"))
    bad = []

    def row(c, w):
        c = str(c).strip().upper()
        if c not in names:
            bad.append(c)
            return None
        return [c, names[c], w]
    st = {}
    for t, v in (d.get("stocks") or {}).items():
        rows = [x for x in (row(c, w) for c, w in v.get("jp") or []) if x]
        st[t] = (v.get("ja"), rows)
    sr = d.get("sector_reason") or "日本の同じ業種の主な銘柄"
    sec = {s: [x for x in (row(c, sr) for c in cs) if x] for s, cs in (d.get("sectors") or {}).items()}
    if bad:
        print(f"::warning::us_jp_links.json に東証の一覧に無いコード: {sorted(set(bad))}（落とした）", file=sys.stderr)
    return st, sec


def shares_of(tickers):
    """発行済株式数（時価総額用）。fast_info は1銘柄ずつなので並列で。取れなければその銘柄は時価総額なし"""
    import yfinance as yf

    def one(t):
        try:
            n = yf.Ticker(t).fast_info["shares"]
            return t, float(n) if n else None
        except Exception:
            return t, None
    with ThreadPoolExecutor(16) as ex:
        got = dict(ex.map(one, tickers))
    ok = sum(1 for v in got.values() if v)
    print(f"株数: {ok}/{len(tickers)}銘柄", file=sys.stderr)
    return got


def last_close(df, t):
    try:
        s = df[t]["Close"].dropna()
        if len(s) < 2:
            return None
        return s
    except Exception:
        return None


def make_items(df, u, fx, shares, st, trade_dates):
    import pandas as pd
    items = []
    for t, meta in u.items():
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            if len(sub) < 2:
                continue
            last = float(sub["Close"].iloc[-1])
            prev = float(sub["Close"].iloc[-2])
            vol = float(sub["Volume"].iloc[-1] or 0)
            if prev <= 0 or last <= 0:
                continue
            dstr = sub.index[-1].strftime("%Y-%m-%d")
            trade_dates[dstr] = trade_dates.get(dstr, 0) + 1
            ja, jp = st.get(t, (None, []))
            usd = last * vol
            it = {"t": t, "n": ja or meta["en"], "s": meta["s"], "x": meta["x"],
                  "c": round((last / prev - 1) * 100, 2), "p": round(last, 2),
                  "v": round(usd * fx / 1e8, 1), "vu": round(usd / 1e8, 2)}
            if ja:
                it["en"] = meta["en"]
            vols = sub["Volume"].iloc[-21:-1].dropna()
            if len(vols) >= 10 and float(vols.mean()) > 0:
                it["r"] = round(vol / float(vols.mean()), 2)
            for key, back in (("c5", 5), ("c20", 20), ("c60", 60)):
                if len(sub) >= back + 1:
                    pb = float(sub["Close"].iloc[-(back + 1)])
                    if pb > 0:
                        it[key] = round((last / pb - 1) * 100, 1)
            hi52 = float(sub["Close"].iloc[-252:].max())
            if hi52 > 0:
                it["hi"] = round((last / hi52 - 1) * 100, 1)
            sh = shares.get(t)
            if sh:
                it["mu"] = round(last * sh / 1e8)
                it["m"] = round(last * sh * fx / 1e8)
            if jp:
                it["jp"] = jp
            items.append(it)
        except Exception:
            continue
    return items


def index_rows(df):
    out = []
    for k, name, tk in INDEXES:
        s = last_close(df, tk)
        if s is None:
            continue
        last, prev = float(s.iloc[-1]), float(s.iloc[-2])
        out.append({"k": k, "n": name, "p": round(last, 2), "c": round((last / prev - 1) * 100, 2),
                    "d": s.index[-1].strftime("%Y-%m-%d")})
    return out


def day_record(out):
    """日付アーカイブ（確定値の回だけ）。S&P500 の銘柄で数える"""
    sp = [i for i in out["items"] if i["x"] & 1]
    up = sum(1 for i in sp if i["c"] > 0)
    dn = sum(1 for i in sp if i["c"] < 0)
    keep = ("t", "n", "s", "c", "v", "vu", "p")
    sec = {}
    for i in sp:
        x = sec.setdefault(i["s"], {"s": i["s"], "n": 0, "sum": 0.0, "up": 0, "dn": 0, "v": 0.0})
        x["n"] += 1
        x["sum"] += i["c"]
        x["up"] += i["c"] > 0
        x["dn"] += i["c"] < 0
        x["v"] += i["v"]
    sectors = sorted(({"s": x["s"], "n": x["n"], "avg": round(x["sum"] / x["n"], 2), "up": x["up"], "dn": x["dn"],
                       "v": round(x["v"])} for x in sec.values()), key=lambda x: -x["avg"])
    return {"date": out["trade_date"], "updated": out["updated"], "fx": out.get("fx"), "idx": out.get("idx") or [],
            "count": len(sp), "up": up, "down": dn, "flat": len(sp) - up - dn, "sectors": sectors,
            "top_up": [{k: i[k] for k in keep} for i in sorted(sp, key=lambda i: -i["c"])[:10]],
            "top_down": [{k: i[k] for k in keep} for i in sorted(sp, key=lambda i: i["c"])[:10]],
            "big": [{k: i[k] for k in keep} for i in sorted(sp, key=lambda i: -i["v"])[:10]],
            "jp": [{"t": i["t"], "n": i["n"], "c": i["c"], "jp": i["jp"]}
                   for i in sorted((i for i in sp if i.get("jp")), key=lambda i: -i["v"])[:15]],
            "sectors_jp": out.get("sectors_jp") or {}}


def save_day(rec):
    DAYS.mkdir(parents=True, exist_ok=True)
    (DAYS / f"{rec['date']}.json").write_text(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ix_p = DAYS / "index.json"
    try:
        ix = json.loads(ix_p.read_text(encoding="utf-8"))
    except Exception:
        ix = {"dates": []}
    ds = sorted(set(ix.get("dates") or []) | {rec["date"]})
    ix_p.write_text(json.dumps({"updated": rec["updated"], "dates": ds}, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")
    print(f"アーカイブ: heatmap_us_days/{rec['date']}.json（{len(ds)}日分）")


def main():
    import yfinance as yf
    u = universe()
    names = jp_names()
    st, sec_links = links(names)
    tickers = sorted(u)
    extra = ["JPY=X"] + [tk for _, _, tk in INDEXES]
    # 52週高値に約1年分が要る（400暦日≒275営業日）
    df = yf.download(tickers + extra, period="400d", progress=False, auto_adjust=False,
                     group_by="ticker", threads=True)
    fxs = last_close(df, "JPY=X")
    if fxs is None:
        print("ドル円（JPY=X）が取れない。円換算できないので書かない", file=sys.stderr)
        sys.exit(1)
    fx = float(fxs.iloc[-1])
    shares = shares_of(tickers)
    td = {}
    items = make_items(df, u, fx, shares, st, td)
    n_sp = sum(1 for i in items if i["x"] & 1)
    if n_sp < FLOOR:
        print(f"S&P500 の取得数が少なすぎる: {n_sp}（{FLOOR}未満）。書かない", file=sys.stderr)
        sys.exit(1)
    trade_date = max(td, key=td.get)
    closed = session_closed(trade_date)
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "et": now_et().isoformat(timespec="minutes"),
           "trade_date": trade_date, "session": "close" if closed else "open",
           "fx": round(fx, 2), "fx_date": fxs.index[-1].strftime("%Y-%m-%d"),
           "count": len(items), "sp": n_sp, "ndx": sum(1 for i in items if i["x"] & 2),
           "idx": index_rows(df), "sectors_jp": sec_links, "items": items}
    if not keep_newest(DST, out, "trade_date", "米国株ヒートマップ"):
        sys.exit(0)
    up = sum(1 for i in items if i["c"] > 0)
    print(f"heatmap_us.json: {len(items)}銘柄（S&P500 {n_sp}）取引日={trade_date} "
          f"{'確定値' if closed else '場中の途中経過'} ドル円={fx:.2f} 上昇{up}")
    if closed:
        save_day(day_record(out))


if __name__ == "__main__":
    main()
