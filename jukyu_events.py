# -*- coding: utf-8 -*-
"""信用残・需給の検証（蓄積型・有料データなし）→ docs/jukyu_stats.json ／ docs/delisted.json

起動文: kaburadar\\起動文_実務_進化③_信用残需給の検証_蓄積型_2026-10-10.md
設計書: D:\\信用残・需給 検証設計書 v1.pdf（Q1〜Q7・しきい値はすべて設計書の初期値）

■ 何をするか
  自前で貯めたデータ（週次の信用残 8/7〜・機関の空売り 6/1〜）だけで、Q1〜Q7 を毎週集計する。
  過去5年・上場廃止を含む、は今は満たせない。満たせるところまでを今つくり、データが
  増えるたびに自動で厚くなる。足りない区分は数字を出さない。

■ 計算の決まり（設計書どおり）
  起点＝公表日の翌営業日の始値。終点＝5・20・60営業日後の終値。TOPIX比の超過（TOPIX の代わりに 1306.T）。
  株価は分割調整済み（yfinance auto_adjust）。集計は 件数・平均・中央値・上昇確率・期間中の最大下落率。
  Q3 は踏み上げ（20営業日以内に終値で+15%以上）の発生率も。
  重複: 同じ銘柄の同じイベントは初回だけ。次は20営業日以上空いたとき。
  30件未満の区分は「参考値」、10件未満は数字を出さない。

■ 公表日
  週次の信用残: 申込日（週の最終営業日）の2営業日後（JPX の週次公表のルール）。8/7〜9/18 は週次の公表、
    9/25 以降は日次の週末値を週次に直したもの。日次の公表は翌営業日なので、2営業日後は遅い側＝先読みしない。
  機関の空売り: JPX の公表日（karauri_events/events.json の pub）。

■ 生存者バイアス（今日から止める）
  JPX「上場廃止銘柄一覧」から廃止日・理由を docs/delisted.json に貯める（予定分も「予定」として残す）。
  上場銘柄一覧（月次）から消えた銘柄も拾う。廃止後に株価が取れない銘柄は、最終終値で打ち切って集計に含める。

使い方: python -X utf8 jukyu_events.py            # 本番
        python -X utf8 jukyu_events.py --test-drop 7203   # 上場一覧から1銘柄消したら delisted がどう動くか（書き込まない）
"""
import html as _html
import io
import json
import math
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from jp_bizday import next_bizday

HERE = Path(__file__).parent
DOCS = HERE / "docs"
OUT = DOCS / "jukyu_stats.json"
DELISTED = DOCS / "delisted.json"
LISTED_STATE = DOCS / "jpx_listed_codes.json"     # 前回見た上場一覧（消えた銘柄の検出用）
JST = timezone(timedelta(hours=9))
UA = {"User-Agent": "Mozilla/5.0 (compatible; kaburadar.jp/1.0; +https://kaburadar.jp)"}

H = (5, 20, 60)
DEDUP_GAP = 20
MIN_FULL, MIN_SHOW = 30, 10
TOPIX_PROXY = "1306.T"
Q1_BINS = [(0, 1, "1倍未満"), (1, 3, "1〜3倍"), (3, 10, "3〜10倍"), (10, math.inf, "10倍超")]
Q2_UP, Q2_LISTED_PCT = 0.20, 0.5          # 買い残 前週比+20%以上・上場株式数比0.5%以上（Fable 10/10）
Q3_UP, SQUEEZE = 0.30, 0.15               # 売り残 前週比+30%以上・踏み上げ=20営業日以内に+15%以上
Q7_VR = 2.0                               # 出来高が20日平均の2倍以上
JPX_LIST = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
JPX_DELISTED = "https://www.jpx.co.jp/listing/stocks/delisted/index.html"
MARKETS = ("プライム", "スタンダード", "グロース")


def log(*a):
    print(*a, file=sys.stderr)


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def http(url, tries=3):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(3 * (i + 1))
    raise last


def add_bizdays(d, n):
    for _ in range(n):
        d = next_bizday(d)
    return d


# ──────────────────────────────────────── 銘柄の属性（市場区分）
def jpx_list():
    """JPX 上場銘柄一覧（月次）→ {code: {"n","m"}}（内国株式のプライム/スタンダード/グロースだけ）"""
    import pandas as pd
    df = pd.read_excel(io.BytesIO(http(JPX_LIST)), dtype=str)
    out = {}
    for _, r in df.iterrows():
        seg = str(r["市場・商品区分"])
        m = next((x for x in MARKETS if x in seg), None)
        if not m or "内国" not in seg:
            continue
        out[str(r["コード"]).strip()] = {"n": str(r["銘柄名"]).strip(), "m": m}
    return out, str(df["日付"].iloc[0])


# ──────────────────────────────────────── 上場廃止（生存者バイアスを今日から止める）
def jpx_delisted_page():
    """JPX「上場廃止銘柄一覧」→ [{d, code, n, m, reason}]（予定日のものも含む）"""
    s = http(JPX_DELISTED).decode("utf-8", "replace")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", s, re.S):
        cells = [_html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) >= 5 and re.match(r"\d{4}/\d{2}/\d{2}", cells[0]):
            out.append({"d": cells[0].replace("/", "-"), "code": cells[2], "n": cells[1],
                        "m": cells[3], "reason": cells[4]})
    return out


def update_delisted(listed, list_date, prices_latest, test_drop=None, write=True):
    """delisted.json を更新する。
    1) JPX の上場廃止銘柄一覧（廃止日・理由つき）。廃止日が過ぎたものは status=delisted、先のものは scheduled
    2) 上場一覧（月次）から消えた銘柄（1 に無ければ reason 不明で記録）
    最終終値は、記録した時点の prices.json（前営業日の終値）を使う"""
    today = datetime.now(JST).date().isoformat()
    cur = jload(DELISTED, {}) or {}
    items = cur.get("items", {})
    page = []
    try:
        page = jpx_delisted_page()
    except Exception as e:
        log(f"上場廃止銘柄一覧を取得できず（一覧の差分だけで続ける）: {e}")
    for r in page:
        it = items.setdefault(r["code"], {"code": r["code"], "n": r["n"], "m": r["m"]})
        it.update({"d": r["d"], "reason": r["reason"], "source": "JPX 上場廃止銘柄一覧",
                   "status": "delisted" if r["d"] <= today else "scheduled"})
        if it["status"] == "delisted" and it.get("last_close") is None:
            p = (prices_latest.get("items") or {}).get(r["code"]) or {}
            if p.get("p") is not None:
                it["last_close"] = p["p"]
                it["last_close_date"] = prices_latest.get("trade_date")
    prev = jload(LISTED_STATE, {}) or {}
    prev_codes = set(prev.get("codes", []))
    now_codes = set(listed) - ({test_drop} if test_drop else set())
    gone = sorted(prev_codes - now_codes) if prev_codes else []
    for c in gone:
        if c in items:
            continue
        p = (prices_latest.get("items") or {}).get(c) or {}
        items[c] = {"code": c, "n": (prev.get("names") or {}).get(c), "d": None, "reason": None,
                    "status": "delisted", "source": f"上場一覧（{list_date}版）から消えた",
                    "last_close": p.get("p"), "last_close_date": prices_latest.get("trade_date")}
    out = {"updated": datetime.now(JST).isoformat(timespec="seconds"),
           "note": "今日から先の上場廃止を取りこぼさないための記録。廃止後の集計は最終終値で打ち切る。"
                   "JPX の上場廃止銘柄一覧に載っている分（約1年）は記録したが、それより前の廃止は取れていない"
                   "（過去の集計は生存者バイアスを含む）。株価が取れない廃止銘柄は集計から外れる",
           "items": dict(sorted(items.items()))}
    if write:
        DELISTED.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        LISTED_STATE.write_text(json.dumps({"date": list_date, "codes": sorted(now_codes),
                                            "names": {c: listed[c]["n"] for c in sorted(now_codes) if c in listed}},
                                           ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return out, gone


# ──────────────────────────────────────── 元データ
def weekly():
    w = jload(DOCS / "shinyo_weekly.json", {}) or {}
    weeks = w.get("weeks", [])
    pubs = [add_bizdays(date.fromisoformat(x), 2).isoformat() for x in weeks]
    return weeks, pubs, w.get("items", {})


def listed_shares():
    """上場株式数（日次PDFの「上場比」から割り戻した値。shinmei_fetch.py が保存）"""
    return ((jload(DOCS / "shinyo_listed.json", {}) or {}).get("items")) or {}


def regime_series():
    items = sorted(((jload(DOCS / "radar_history.json", {}) or {}).get("items") or []), key=lambda x: x["d"])
    return [(x["d"], x.get("regime")) for x in items if x.get("regime")]


def regime_on(series, d):
    r = None
    for dd, rg in series:
        if dd > d:
            break
        r = rg
    return r or "不明（温度計の記録前）"


def karauri_snapshots():
    """docs/karauri.json の過去の版 → {公表日: {code: 合計%}}（同じ公表日は新しい版を採る）"""
    try:
        shas = subprocess.run(["git", "log", "--format=%H", "--", "docs/karauri.json"], cwd=HERE,
                              capture_output=True, text=True, check=True).stdout.split()
    except Exception as e:
        log(f"git log 失敗: {e}")
        return {}
    snaps = {}
    for sha in shas:                         # 新しい順。最初に見た版を採る
        try:
            d = json.loads(subprocess.run(["git", "show", f"{sha}:docs/karauri.json"], cwd=HERE,
                                          capture_output=True, check=True).stdout.decode("utf-8"))
        except Exception:
            continue
        rep = d.get("report_date")
        if rep and rep not in snaps:
            snaps[rep] = {s["c"]: s.get("total") for s in d.get("stocks", [])}
    return snaps


# ──────────────────────────────────────── イベント抽出
def ev(q, group, code, pub, **extra):
    return {"q": q, "g": group, "c": code, "pub": pub, **extra}


def q1_q2_q3(weeks, pubs, items, shares, universe):
    out = []
    for c, rows in items.items():
        if c not in universe:
            continue
        for i, r in enumerate(rows):
            if not r:
                continue
            s, b = r
            ratio = (b / s) if s else math.inf
            # 売り残0は倍率が無限大＝10倍超に入れる
            lab = (next((l for lo, hi, l in Q1_BINS if lo <= ratio < hi), Q1_BINS[-1][2])
                   if (b or s) else None)
            if lab:
                out.append(ev("Q1", lab, c, pubs[i], week=weeks[i], ratio=None if ratio == math.inf else round(ratio, 2)))
            prev = rows[i - 1] if i >= 1 else None
            if not prev:
                continue
            ps, pb = prev
            if pb and b >= pb * (1 + Q2_UP):
                sh = shares.get(c)
                if sh:
                    pct = b / sh * 100
                    if pct >= Q2_LISTED_PCT:
                        out.append(ev("Q2", "買い残 前週比+20%以上", c, pubs[i], week=weeks[i],
                                      chg=round(b / pb - 1, 3), listed_pct=round(pct, 2)))
            if ps and s >= ps * (1 + Q3_UP):
                out.append(ev("Q3", "売り残 前週比+30%以上", c, pubs[i], week=weeks[i], chg=round(s / ps - 1, 3)))
    return out


def q6(universe):
    """機関の空売り（銘柄の合計）。新規報告・増加への転換・減少への転換は events.json（6/1〜）、
    報告の消滅は karauri.json の過去の版（8/11〜）"""
    out = []
    evs = jload(HERE / "karauri_events" / "events.json", []) or []
    by_day = defaultdict(lambda: defaultdict(list))
    for e in evs:
        c = e["code"][:4] if len(e["code"]) == 5 and e["code"].endswith("0") else e["code"]
        by_day[e["pub"]][c].append(e)
    last_dir = {}
    for pub in sorted(by_day):
        for c, rows in by_day[pub].items():
            if c not in universe:
                continue
            if any(r["event"] == "新規" and r.get("prev") is None for r in rows):
                out.append(ev("Q6", "新規報告", c, pub))
            net = sum((r.get("ratio") or 0) - (r.get("prev") or 0) for r in rows)
            if abs(net) < 1e-9:
                continue
            d = 1 if net > 0 else -1
            if c in last_dir and last_dir[c] != d:
                out.append(ev("Q6", "増加に転換" if d > 0 else "減少に転換", c, pub, net_pt=round(net * 100, 2)))
            last_dir[c] = d
    snaps = karauri_snapshots()
    days = sorted(snaps)
    for a, b in zip(days, days[1:]):
        for c in set(snaps[a]) - set(snaps[b]):
            if c in universe:
                out.append(ev("Q6", "報告消滅", c, b))
    return out, (min(by_day) if by_day else None), (days[0] if days else None)


def q7(q1_events, q6_events, vol_ratio):
    """Q6 のイベントの日に、出来高が20日平均の2倍以上だったもの × その時点の信用倍率の区分"""
    latest_q1 = defaultdict(list)
    for e in q1_events:
        latest_q1[e["c"]].append((e["pub"], e["g"]))
    for v in latest_q1.values():
        v.sort()
    out = []
    for e in q6_events:
        vr = vol_ratio(e["c"], e["pub"])
        if vr is None or vr < Q7_VR:
            continue
        bucket = None
        for pub, g in latest_q1.get(e["c"], []):
            if pub <= e["pub"]:
                bucket = g
        if bucket:
            out.append(ev("Q7", f"{e['g']}×信用倍率{bucket}", e["c"], e["pub"], vr=round(vr, 1)))
    return out


# ──────────────────────────────────────── 株価
def fetch_prices(codes, start):
    import pandas as pd
    import yfinance as yf
    px = {}
    tks = sorted({c + ".T" for c in codes} | {TOPIX_PROXY})
    for i in range(0, len(tks), 60):
        chunk = tks[i:i + 60]
        try:
            df = yf.download(" ".join(chunk), start=start, progress=False, auto_adjust=True,
                             threads=False, group_by="ticker")
        except Exception as e:
            log(f"  価格取得失敗: {e}")
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) < 2:
                    continue
                px[t] = {k.strftime("%Y-%m-%d"): (float(r["Open"]), float(r["Close"]), float(r["Volume"] or 0))
                         for k, r in sub.iterrows()}
            except Exception:
                continue
        time.sleep(1.0)
    return px


def measure(e, px, cal, delisted):
    """起点＝公表日の翌営業日の始値。5・20・60営業日後の終値まで。TOPIX比・最大下落率・踏み上げ"""
    bars = px.get(e["c"] + ".T")
    tp = px.get(TOPIX_PROXY)
    if not bars or not tp:
        return None
    after = [d for d in cal if d > e["pub"]]
    if not after or after[0] not in bars:
        return None
    base = after[0]
    o = bars[base][0]
    if not o:
        return None
    to = tp[base][0]
    last_bar = max(bars)
    dl = delisted.get(e["c"]) or {}
    res = {"base": base, "open": round(o, 2)}
    for h in H:
        if len(after) <= h:
            continue                      # まだ日がたっていない
        end = after[h]
        if end in bars:
            c = bars[end][1]
        elif dl.get("status") == "delisted" and end > last_bar:
            c = bars[last_bar][1]         # 上場廃止: 最終終値で打ち切って含める
            res["truncated"] = True
        else:
            continue
        r = c / o - 1
        rt = tp[end][1] / to - 1 if end in tp else None
        res[f"r{h}"] = round(r * 100, 3)
        if rt is not None:
            res[f"x{h}"] = round((r - rt) * 100, 3)
        if h == 20:
            win = [bars[d][1] for d in after[: h + 1] if d in bars]
            if win:
                res["dd20"] = round((min(win) / o - 1) * 100, 3)
                res["up20max"] = round((max(win) / o - 1) * 100, 3)
    return res


def vol_ratio_fn(px):
    def f(code, d):
        bars = px.get(code + ".T")
        if not bars or d not in bars:
            return None
        ds = sorted(x for x in bars if x < d)[-20:]
        vs = [bars[x][2] for x in ds if bars[x][2]]
        if len(vs) < 10 or not bars[d][2]:
            return None
        return bars[d][2] / (sum(vs) / len(vs))
    return f


def value_tier_fn(px):
    """規模＝売買代金の3層（直近20営業日の平均売買代金。今の全銘柄の三分位で切る）"""
    avg = {}
    for t, bars in px.items():
        if t == TOPIX_PROXY:
            continue
        ds = sorted(bars)[-20:]
        v = [bars[d][1] * bars[d][2] for d in ds]
        if v:
            avg[t[:-2]] = sum(v) / len(v)
    vals = sorted(avg.values())
    if len(vals) < 30:
        return lambda c: "不明", None
    lo, hi = vals[len(vals) // 3], vals[2 * len(vals) // 3]

    def f(c):
        v = avg.get(c)
        if v is None:
            return "不明"
        return "大型" if v >= hi else ("中型" if v >= lo else "小型")
    return f, {"小型と中型の境（円/日）": round(lo), "中型と大型の境（円/日）": round(hi)}


# ──────────────────────────────────────── 集計
def dedup(events, cal):
    """同じ銘柄・同じ問い・同じ区分は初回だけ。次は20営業日以上空いたとき"""
    pos = {d: i for i, d in enumerate(cal)}
    out, last = [], {}
    for e in sorted(events, key=lambda x: x["pub"]):
        k = (e["q"], e["g"], e["c"])
        i = pos.get(e["pub"]) if e["pub"] in pos else sum(1 for d in cal if d <= e["pub"])
        if k in last and i - last[k] < DEDUP_GAP:
            continue
        last[k] = i
        out.append(e)
    return out


def summarize(rows, squeeze=False):
    n_any = len(rows)
    out = {"n": n_any}
    for h in H:
        v = [r[f"r{h}"] for r in rows if r.get(f"r{h}") is not None]
        x = [r[f"x{h}"] for r in rows if r.get(f"x{h}") is not None]
        k = len(v)
        blk = {"n": k, "label": "通常" if k >= MIN_FULL else ("参考値" if k >= MIN_SHOW else "数字なし")}
        if k >= MIN_SHOW:
            blk.update({"mean": round(float(np.mean(v)), 2), "median": round(float(np.median(v)), 2),
                        "up_rate": round(sum(1 for a in v if a > 0) / k * 100, 1),
                        "excess_mean": round(float(np.mean(x)), 2) if x else None})
            if h == 20:
                dd = [r["dd20"] for r in rows if r.get("dd20") is not None]
                blk["max_drawdown_mean"] = round(float(np.mean(dd)), 2) if dd else None
                if squeeze:
                    sq = [r for r in rows if r.get("up20max") is not None]
                    blk["squeeze_rate"] = round(sum(1 for r in sq if r["up20max"] >= SQUEEZE * 100) / len(sq) * 100, 1) if sq else None
        out[f"h{h}"] = blk
    return out


def aggregate(events, squeeze=False):
    groups = defaultdict(list)
    for e in events:
        if e.get("m"):
            groups[e["g"]].append(e)
    res = {}
    for g, rows in sorted(groups.items()):
        res[g] = {"all": summarize(rows, squeeze)}
        for dim in ("regime", "size", "market"):
            sub = defaultdict(list)
            for r in rows:
                sub[r.get(dim) or "不明"].append(r)
            res[g]["by_" + dim] = {k: summarize(v, squeeze) for k, v in sorted(sub.items())}
    return res


def flat(e):
    """集計用に、測った数字をイベントの上に広げる"""
    m = e.get("m") or {}
    return {**e, **{k: v for k, v in m.items()}}


def export_tables(stats, path_prefix):
    """本・note 用の書き出し（CSV。openpyxl があれば xlsx も）。件数が足りないうちは呼ばない（起動文）"""
    import csv
    rows = []
    for q, qd in stats.get("questions", {}).items():
        for g, gd in (qd.get("groups") or {}).items():
            for h in H:
                b = gd["all"].get(f"h{h}", {})
                rows.append([q, g, h, b.get("n"), b.get("label"), b.get("mean"), b.get("median"),
                             b.get("up_rate"), b.get("excess_mean")])
    with open(f"{path_prefix}.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["問い", "区分", "営業日", "件数", "扱い", "平均%", "中央値%", "上昇確率%", "TOPIX比%"])
        w.writerows(rows)


# ──────────────────────────────────────── 本体
def main():
    test_drop = None
    if "--test-drop" in sys.argv:
        test_drop = sys.argv[sys.argv.index("--test-drop") + 1]
    listed, list_date = jpx_list()
    prices_latest = jload(DOCS / "prices.json", {}) or {}
    dl, gone = update_delisted(listed, list_date, prices_latest, test_drop=test_drop, write=not test_drop)
    if test_drop:
        print(json.dumps({"gone": gone, "record": dl["items"].get(test_drop)}, ensure_ascii=False, indent=1))
        return 0
    delisted = dl["items"]
    universe = set(listed) | {c for c, it in delisted.items() if it.get("status") == "delisted"}
    meta = {c: listed.get(c, {}).get("m") or (delisted.get(c) or {}).get("m") for c in universe}

    weeks, pubs, items = weekly()
    shares = listed_shares()
    e123 = q1_q2_q3(weeks, pubs, items, shares, universe)
    e6, q6_from, q6_gone_from = q6(universe)
    codes = {e["c"] for e in e123 + e6}
    start = (date.fromisoformat(min(e["pub"] for e in e123 + e6)) - timedelta(days=45)).isoformat()
    log(f"イベント候補 Q1-3:{len(e123)} Q6:{len(e6)} / 株価を取る銘柄 {len(codes)}")
    px = fetch_prices(codes, start)
    cal = sorted(px.get(TOPIX_PROXY, {}))
    if len(cal) < 30:
        sys.exit("::error::TOPIX の代わり（1306.T）の日足が取れない")
    vr = vol_ratio_fn(px)
    e7 = q7([e for e in e123 if e["q"] == "Q1"], e6, vr)   # 信用倍率の区分（Q1）だけを掛け合わせる
    tier, tier_cut = value_tier_fn(px)
    rs = regime_series()

    allev = dedup(e123 + e6 + e7, cal)
    for e in allev:
        e["m"] = measure(e, px, cal, delisted)
        e["regime"] = regime_on(rs, e["pub"])
        e["size"] = tier(e["c"])
        e["market"] = meta.get(e["c"]) or "不明"
    ev_f = [flat(e) for e in allev]

    def qs(q):
        return [e for e in ev_f if e["q"] == q]

    first_week = weeks[0] if weeks else None
    stats = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "method": {
            "start": "公表日の翌営業日の始値", "end": "5・20・60営業日後の終値", "excess": f"TOPIX の代わりに {TOPIX_PROXY}",
            "dedup": "同じ銘柄の同じイベントは初回だけ（次は20営業日以上空いたとき）",
            "labels": f"{MIN_FULL}件未満は参考値、{MIN_SHOW}件未満は数字を出さない",
            "weekly_pub": "週次の信用残は申込日の2営業日後を公表日とした（先読みしない側）",
            "size": "規模＝直近20営業日の平均売買代金を、今の全銘柄の三分位で大型・中型・小型に分けた",
            "size_cut": tier_cut, "regime": "温度計（radar_history.json・2026-08-02〜）。それより前は「不明」",
            "prices": "yfinance（分割調整済み）", "survivorship": "2026-10-10 から上場廃止を記録（delisted.json）。それ以前の廃止銘柄は含まれない",
        },
        "data_since": {"信用残（週次）": first_week, "機関の空売り（転換・新規）": q6_from,
                       "機関の空売り（消滅）": q6_gone_from, "温度計": (rs[0][0] if rs else None)},
        "questions": {
            "Q1": {"title": "信用倍率の水準別", "status": "集計中", "groups": aggregate(qs("Q1"))},
            "Q2": {"title": "買い残の急増（前週比+20%以上・上場株式数比0.5%以上）", "status": "集計中",
                   "groups": aggregate(qs("Q2")),
                   "note": "上場株式数は日次PDFの上場比から割り戻した最新の値を全週に当てた。上場比が取れない銘柄は対象外"},
            "Q3": {"title": "売り残の急増→踏み上げ（前週比+30%以上・20営業日以内に+15%以上）", "status": "集計中",
                   "groups": aggregate(qs("Q3"), squeeze=True)},
            "Q4": {"title": "買い残の2年レンジ内の位置", "status": "蓄積待ち",
                   "available_from": (f"{int(first_week[:4]) + 2}{first_week[4:7]}（104週たまったら）" if first_week else None),
                   "note": "2年（104週）分の週次が必要。短い期間で代用しない"},
            "Q5": {"title": "6か月前の買い膨らみ→期日通過", "status": "蓄積待ち",
                   "available_from": "2029-02（設計書どおり：26週前の時点で2年レンジが要る）",
                   "note": "26週前の買い残だけで見る簡易版なら2027-02から出せるが、2年レンジの代用になるのでやらない"},
            "Q6": {"title": "機関の空売り（合計）の新規報告・増加/減少への転換・報告消滅", "status": "集計中",
                   "groups": aggregate(qs("Q6"))},
            "Q7": {"title": "信用倍率×機関の空売り×出来高急増（20日平均の2倍以上）", "status": "集計中",
                   "groups": aggregate(qs("Q7"))},
        },
        "counts": {q: len(qs(q)) for q in ("Q1", "Q2", "Q3", "Q6", "Q7")},
    }
    OUT.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    # 手計算の照合用に、イベントの明細も残す（大きいので docs には置かない）
    (HERE / "jukyu_events_detail.json").write_text(json.dumps(ev_f, ensure_ascii=False), encoding="utf-8")
    log(f"jukyu_stats.json: " + " ".join(f"{k}={v}" for k, v in stats["counts"].items()))
    for g, gd in stats["questions"]["Q1"]["groups"].items():
        b = gd["all"]["h20"]
        log(f"  Q1 {g}: 20日 n={b['n']} {b['label']} 平均{b.get('mean')} TOPIX比{b.get('excess_mean')} 上昇{b.get('up_rate')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
