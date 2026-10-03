# -*- coding: utf-8 -*-
"""全銘柄の日足 → prices.json / ±5%動いた銘柄に「その日あったこと」→ movers.json

企画書: kaburadar\\企画書_100万PV_銘柄エンジン3本_2026-10-03.md（§2エンジン③・§4の契約）

■ 出力
  docs/prices.json                 全銘柄の終値・騰落・52週・25日線・出来高倍率・売買代金
  docs/movers.json                 当日 ±5% 以上（売買代金1億円未満は除外）
  docs/movers_hist/YYYY-MM-DD.json 同じものを日付別に。**消さない**（銘柄ページの「大きく動いた日」用）
  docs/movers_hist/index.json      日付の一覧（静的サイトはディレクトリを列挙できないため）

■ ユニバース
  tenbagger_universe.csv ＋ JPX公式の上場銘柄一覧（月次 xlsx）で抜けている分を補う（load_jpx）。

■ facts の約束
  k は tdnet / karauri / shinyo / sector / volume / regime の6種だけ。
  文は数字から機械的に作る事実文のみ。「材料視」「失望売り」「理由は〜」のような推定・評価は書かない。

■ tdnet は後付け
  docs/tdnet_list.json（エンジン②が作る）が無ければスキップする。
  毎回、直近の movers_hist のうち tdnet を照合できていない日を見直し、
  一覧が届いていれば後から付ける（tdnet_checked で管理）。

■ yfinance の負荷
  チャンク200銘柄・間に2秒。失敗チャンクは1回だけ再試行。
  それでも欠けた銘柄は前回の prices.json の値を残して stale:true を付ける。
"""
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "docs"
UNIVERSE = ROOT / "tenbagger_universe.csv"
PRICES = DOCS / "prices.json"
MOVERS = DOCS / "movers.json"
HIST = DOCS / "movers_hist"

CHUNK = 200
WAIT = 2.0
THRESHOLD = 5.0        # ±5%
MIN_VALUE = 100.0      # 売買代金(百万円)。1億円未満は除外
TDNET_LOOKBACK = 14    # tdnet を後付けしに行く日数（暦日。TDnet一覧は直近10営業日ぶん）
KS = ("tdnet", "karauri", "shinyo", "sector", "volume", "regime")
REGIME_JP = {"BULLISH": "強気", "NEUTRAL": "中立", "BEARISH": "弱気", "PANIC": "パニック"}


def log(*a):
    print(*a, flush=True)


def rd(x, n=1):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except Exception:
        pass
    return round(float(x), n)


def load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(p)


def signed(x, unit="%", n=1):
    """+1.2% / −0.4%（マイナスは全角ダッシュでなく数学のマイナス記号で統一）"""
    s = f"{abs(x):.{n}f}{unit}"
    return ("+" if x > 0 else "−" if x < 0 else "±") + s


# ─────────────────────────────────────────────
#  1. ユニバース
# ─────────────────────────────────────────────
JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"


def load_jpx():
    """JPX公式の上場銘柄一覧（月次）。プライム/スタンダード/グロースの内国・外国株式。

    tenbagger_universe.csv は 2026-08-03 から更新されておらず、それ以降の新規上場
    （604A ビーエイブル等）が抜けていた。株探の値上がり率上位との突合で発覚（2026-10-03）。
    CSV は tenbagger_rank.py の入力なので触らず、こちらで毎回補う。取れなければ CSV だけで続行。
    """
    try:
        import io
        import urllib.request
        req = urllib.request.Request(JPX_LIST, headers={"User-Agent": "kaburadar.jp movers_daily"})
        raw = urllib.request.urlopen(req, timeout=60).read()
        df = pd.read_excel(io.BytesIO(raw), dtype=str)
        df = df[df["市場・商品区分"].str.contains("プライム|スタンダード|グロース", na=False)]
        out = []
        for _, r in df.iterrows():
            c = str(r["コード"]).strip()
            out.append({"t": f"{c}.T", "c": c, "n": str(r["銘柄名"]).strip(),
                        "s": str(r["33業種区分"]).strip()})
        log(f"JPX一覧: {len(out)}銘柄（{df['日付'].iloc[0]}版）")
        return out
    except Exception as e:
        log(f"JPX一覧の取得に失敗（CSVだけで続行）: {e}")
        return []


JPX_NEW = "https://www.jpx.co.jp/listing/stocks/new/index.html"


def load_jpx_new():
    """JPXの新規上場ページ。月次一覧（load_jpx）に載るまでの約1か月の穴を埋める。

    646A クラサスケミカル（9/29上場）が売買代金100億円で−8.6%動いたのに漏れていた（2026-10-03 突合）。
    業種はこのページに無いので空（sector の事実は付かない）。上場日が今日より後の行は入れない。
    """
    import re
    import urllib.request
    try:
        req = urllib.request.Request(JPX_NEW, headers={"User-Agent": "kaburadar.jp movers_daily"})
        h = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as e:
        log(f"JPX新規上場ページの取得に失敗（スキップ）: {e}")
        return []
    today = datetime.now(JST).date()
    out = []
    for m in re.finditer(r'<span id="([0-9][0-9A-Z]{3})"></span>', h):
        before = h[max(0, m.start() - 3000):m.start()]
        ds = re.findall(r"(\d{4})/(\d{2})/(\d{2})<br", before)
        nm = re.findall(r'issuename-word-break"[^>]*>\s*(?:<a[^>]*>)?([^<]+)', before)
        if not ds or not nm:
            continue
        y, mo, d = map(int, ds[-1])
        if date(y, mo, d) > today:
            continue
        c = m.group(1)
        out.append({"t": f"{c}.T", "c": c, "n": nm[-1].replace("（株）", "").strip(), "s": ""})
    log(f"JPX新規上場: {len(out)}銘柄（上場済み）")
    return out


def load_universe():
    rows = []
    with open(UNIVERSE, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r.get("ticker") and r.get("code"):
                rows.append({"t": r["ticker"].strip(), "c": r["code"].strip(),
                             "n": (r.get("name") or "").strip(),
                             "s": (r.get("sector33") or "").strip()})
    have = {r["c"] for r in rows}
    add = []
    for r in load_jpx() + load_jpx_new():
        if r["c"] not in have:
            have.add(r["c"])
            add.append(r)
    if add:
        log(f"JPX一覧で補った銘柄: {len(add)}（例 {', '.join(r['c'] for r in add[:5])}）")
    return rows + add


# ─────────────────────────────────────────────
#  2. 日足の取得
# ─────────────────────────────────────────────
def fetch_chunk(tickers):
    df = yf.download(tickers, period="1y", interval="1d", progress=False,
                     auto_adjust=False, group_by="ticker", threads=True)
    out = {}
    if df is None or df.empty:
        return out
    for t in tickers:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            sub = sub.dropna(subset=["Close"])
            if len(sub) >= 2:
                out[t] = sub
        except Exception:
            continue
    return out


def fetch_all(tickers):
    data, failed = {}, []
    chunks = [tickers[i:i + CHUNK] for i in range(0, len(tickers), CHUNK)]
    for i, ch in enumerate(chunks, 1):
        try:
            got = fetch_chunk(ch)
        except Exception as e:
            log(f"  チャンク{i}/{len(chunks)} 失敗: {e}")
            got = {}
        data.update(got)
        miss = [t for t in ch if t not in got]
        log(f"  チャンク{i}/{len(chunks)}: {len(got)}/{len(ch)}")
        if len(got) == 0:
            failed.append(ch)          # チャンクごと落ちた
        elif miss:
            failed.append(miss)        # 一部だけ欠けた
        time.sleep(WAIT)
    # 1回だけ再試行
    retry = [t for ch in failed for t in ch]
    if retry:
        # 欠けの多くは YFRateLimitError。すぐ叩き直しても同じなので少し空ける
        log(f"  再試行: {len(retry)}銘柄（30秒待ってから）")
        time.sleep(30)
        for i in range(0, len(retry), CHUNK):
            ch = retry[i:i + CHUNK]
            try:
                data.update(fetch_chunk(ch))
            except Exception as e:
                log(f"  再試行チャンク失敗: {e}")
            time.sleep(WAIT)
    return data


def metrics(sub):
    close = sub["Close"].astype(float)
    vol = sub["Volume"].astype(float).fillna(0)
    high = sub["High"].astype(float) if "High" in sub else close
    p = float(close.iloc[-1])

    def chg(back):
        if len(close) > back:
            b = float(close.iloc[-(back + 1)])
            return (p / b - 1) * 100 if b > 0 else None
        return None

    v20 = vol.iloc[-21:-1]
    v20 = v20[v20 > 0]
    vr = float(vol.iloc[-1]) / float(v20.mean()) if len(v20) >= 5 and v20.mean() > 0 else None
    hi = float(high.iloc[-250:].max())
    hi52 = (p / hi - 1) * 100 if hi > 0 else None
    g25 = (p / float(close.iloc[-25:].mean()) - 1) * 100 if len(close) >= 25 else None
    return {
        "p": rd(p, 1), "c": rd(chg(1), 2), "c5": rd(chg(5), 1), "c20": rd(chg(20), 1),
        "hi52": rd(hi52, 1), "g25": rd(g25, 1), "vr": rd(vr, 1),
        "v": rd(p * float(vol.iloc[-1]) / 1e6, 0),
    }


# ─────────────────────────────────────────────
#  3. facts の材料
# ─────────────────────────────────────────────
def load_tdnet():
    """docs/tdnet_list.json を {code: [{d,time,t,u}]} に。無ければ None（＝照合できない）。

    ②の形式が確定する前なので、次のどれでも読めるようにしておく:
      {"days":[{"d":"YYYY-MM-DD","items":[{code,name,time,title,pdf_url}]}]}
      {"items":[{date|d, code|c, time, title|t, pdf_url|u}]}
      [ {...同上...} ]
    """
    raw = load_json(DOCS / "tdnet_list.json")
    if raw is None:
        return None
    rows = []

    def take(it, d=None):
        c = str(it.get("code") or it.get("c") or "").strip()
        if len(c) == 5 and c.endswith("0"):   # TDnetの5桁コード（末尾0）→4桁
            c = c[:4]
        t = it.get("title") or it.get("t") or ""
        dd = it.get("date") or it.get("d") or d or ""
        if c and t and dd:
            rows.append((c, {"d": str(dd)[:10], "time": it.get("time") or "",
                             "t": t, "u": it.get("pdf_url") or it.get("u") or it.get("pdf") or ""}))

    if isinstance(raw, dict) and isinstance(raw.get("days"), list):
        for day in raw["days"]:
            for it in day.get("items") or []:
                take(it, day.get("d") or day.get("date"))
    else:
        items = raw.get("items") if isinstance(raw, dict) else raw
        for it in items or []:
            if isinstance(it, dict):
                take(it)
    idx = defaultdict(list)
    for c, r in rows:
        idx[c].append(r)
    dates = sorted({r["d"] for _, r in rows})
    return {"idx": idx, "dates": set(dates)}


def tdnet_facts(td, code, d_prev, d_today):
    out = []
    for r in td["idx"].get(code, []):
        if r["d"] in (d_prev, d_today):
            out.append(r)
    out.sort(key=lambda r: (r["d"], r["time"]))
    facts = []
    for r in out[:6]:
        f = {"k": "tdnet", "d": r["d"], "t": r["t"], "u": r["u"]}
        if r["time"]:
            f["tm"] = r["time"]
        facts.append(f)
    return facts


def tdnet_covers(td, d_prev, d_today):
    return td is not None and d_prev in td["dates"] and d_today in td["dates"]


def load_karauri():
    raw = load_json(DOCS / "karauri.json", {}) or {}
    rep = raw.get("report_date") or ""
    m = {}
    for s in raw.get("stocks") or []:
        m[str(s.get("c"))] = s
    return m, rep


def karauri_fact(k, rep, code):
    s = k.get(code)
    if not s:
        return None
    total = s.get("total")
    d1 = s.get("d1")
    if total is None:
        return None
    t = f"大口空売り残高の合計は{total:.2f}%（{s.get('cnt', 0)}社・JPX公表 {rep}）"
    if d1:
        t += f"、前回公表から{signed(d1, 'pt', 2)}"
    return {"k": "karauri", "t": t}


def load_shinyo():
    raw = load_json(DOCS / "shinyo_meigara.json", {}) or {}
    return {str(i.get("c")): i for i in raw.get("items") or []}, raw.get("asof") or ""


def shinyo_fact(sh, asof, code):
    i = sh.get(code)
    if not i:
        return None
    parts = []
    for key, dkey, label in (("b", "bd", "信用買い残"), ("s", "sd", "信用売り残")):
        cur, d = i.get(key), i.get(dkey)
        if cur is None or d is None:
            continue
        prev = cur - d
        if prev > 0:
            parts.append(f"{label}は前週比{signed(d / prev * 100, '%', 0)}")
    if not parts:
        return None
    b, s = i.get("b") or 0, i.get("s") or 0
    tail = f"（{asof}時点" + (f"・信用倍率{b / s:.1f}倍）" if s > 0 else "）")
    return {"k": "shinyo", "t": "、".join(parts) + tail}


def load_regime():
    r = load_json(DOCS / "radar.json", {}) or {}
    reg = r.get("regime")
    if reg not in REGIME_JP:
        return None
    return {"k": "regime", "t": f"この日の地合い判定は{REGIME_JP[reg]}（{reg}・{(r.get('updated') or '')[:10]}）"}


# ─────────────────────────────────────────────
#  4. 本体
# ─────────────────────────────────────────────
def main():
    t0 = time.time()
    uni = load_universe()
    tickers = [u["t"] for u in uni]
    log(f"ユニバース: {len(tickers)}銘柄")

    data = fetch_all(tickers)
    t_fetch = time.time() - t0
    log(f"取得: {len(data)}/{len(tickers)}銘柄  {t_fetch:.0f}秒")

    # 取引日 = 最終行の日付の最頻値。そこに届いていない銘柄は当日の足が無い（停止・取得漏れ）
    last_dates = Counter(sub.index[-1].date() for sub in data.values())
    if not last_dates:
        log("::error::1銘柄も取れなかった。前回の prices.json を残して終了")
        sys.exit(1)
    trade_date = last_dates.most_common(1)[0][0]
    # 前営業日 = 当日足を持つ銘柄の1本前の日付の最頻値
    prev_dates = Counter(sub.index[-2].date() for sub in data.values()
                         if sub.index[-1].date() == trade_date and len(sub) >= 2)
    d_prev = prev_dates.most_common(1)[0][0].isoformat() if prev_dates else ""
    d_today = trade_date.isoformat()
    log(f"取引日: {d_today}（前営業日 {d_prev}）")

    old = (load_json(PRICES, {}) or {}).get("items") or {}
    items, stale, missing = {}, 0, 0
    for u in uni:
        sub = data.get(u["t"])
        if sub is not None and sub.index[-1].date() == trade_date:
            try:
                items[u["c"]] = metrics(sub)
                continue
            except Exception as e:
                log(f"  {u['c']} 計算失敗: {e}")
        if u["c"] in old:
            o = dict(old[u["c"]])
            o["stale"] = True
            items[u["c"]] = o
            stale += 1
        else:
            missing += 1
    fresh = len(items) - stale
    log(f"prices: {len(items)}件（新しい値 {fresh} / 前回値 stale {stale} / 欠損 {missing}）")

    if fresh < len(tickers) * 0.5:
        log("::error::新しい値が半分に届かない。取得側の障害とみなし、書き出さずに終了")
        sys.exit(1)

    now = datetime.now(JST).isoformat(timespec="seconds")
    write_json(PRICES, {"updated": now, "trade_date": d_today, "count": len(items),
                        "fresh": fresh, "stale": stale, "missing": missing,
                        "items": items})

    # ── movers ──
    meta = {u["c"]: u for u in uni}
    sec_sum = defaultdict(list)
    for c, it in items.items():
        if not it.get("stale") and it.get("c") is not None and meta[c]["s"]:
            sec_sum[meta[c]["s"]].append(it["c"])
    sec_avg = {s: (sum(v) / len(v), len(v)) for s, v in sec_sum.items() if v}

    td = load_tdnet()
    td_ok = tdnet_covers(td, d_prev, d_today)
    kr, kr_rep = load_karauri()
    sh, sh_asof = load_shinyo()
    regime = load_regime()

    up, down = [], []
    for c, it in items.items():
        if it.get("stale") or it.get("c") is None:
            continue
        if abs(it["c"]) < THRESHOLD or (it.get("v") or 0) < MIN_VALUE:
            continue
        m = meta[c]
        facts = []
        if td_ok:
            facts += tdnet_facts(td, c, d_prev, d_today)
        f = karauri_fact(kr, kr_rep, c)
        if f:
            facts.append(f)
        f = shinyo_fact(sh, sh_asof, c)
        if f:
            facts.append(f)
        if m["s"] in sec_avg:
            avg, n = sec_avg[m["s"]]
            facts.append({"k": "sector", "t": f"同業種（{m['s']}・{n}銘柄）の平均は{signed(avg, '%', 1)}"})
        if it.get("vr") is not None:
            facts.append({"k": "volume", "t": f"出来高は20日平均の{it['vr']:.1f}倍"})
        if regime:
            facts.append(dict(regime))
        row = {"c": c, "n": m["n"], "s": m["s"], "chg": rd(it["c"], 1), "vr": it.get("vr"),
               "p": it["p"], "v": it.get("v"), "facts": facts}
        (up if it["c"] > 0 else down).append(row)
    up.sort(key=lambda r: -r["chg"])
    down.sort(key=lambda r: r["chg"])

    mv = {"updated": now, "trade_date": d_today, "prev_date": d_prev, "threshold": THRESHOLD,
          "min_value": MIN_VALUE, "tdnet_checked": td_ok,
          "count": {"up": len(up), "down": len(down)}, "up": up, "down": down}
    write_json(MOVERS, mv)
    write_json(HIST / f"{d_today}.json", mv)
    log(f"movers: 急騰 {len(up)} / 急落 {len(down)}  tdnet照合={'あり' if td_ok else 'なし（後付け）'}")

    # ── 過去日に tdnet を後付け ──
    refilled = backfill_tdnet(td, trade_date)
    if refilled:
        log(f"tdnet 後付け: {', '.join(refilled)}")

    # ── 日付一覧 ──
    dates = sorted((p.stem for p in HIST.glob("????-??-??.json")), reverse=True)
    write_json(HIST / "index.json", {"updated": now, "dates": dates})

    log(f"所要時間: {time.time() - t0:.0f}秒（取得 {t_fetch:.0f}秒）")


def backfill_tdnet(td, trade_date):
    if td is None:
        return []
    done = []
    since = trade_date - timedelta(days=TDNET_LOOKBACK)
    for p in sorted(HIST.glob("????-??-??.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if d < since or d >= trade_date:
            continue
        mv = load_json(p)
        if not mv or mv.get("tdnet_checked"):
            continue
        d_today, d_prev = mv.get("trade_date"), mv.get("prev_date")
        if not tdnet_covers(td, d_prev, d_today):
            continue
        for side in ("up", "down"):
            for r in mv.get(side) or []:
                rest = [f for f in r.get("facts") or [] if f.get("k") != "tdnet"]
                r["facts"] = tdnet_facts(td, r["c"], d_prev, d_today) + rest
        mv["tdnet_checked"] = True
        write_json(p, mv)
        done.append(p.stem)
    return done


if __name__ == "__main__":
    main()
