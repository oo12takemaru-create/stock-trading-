# -*- coding: utf-8 -*-
"""3軸スコアの縮約版 `score3_lite` を過去10年ぶん計算する。

■ なぜ作るか（引継ぎ.md §17-6 / 起動文 Phase 2-④ Step 1）
株レーダーの本番 `score3.py` は「今日の値」を各 JSON の最新から組み立てる。
過去に遡れないので、**ビルダーの地合いフィルタとして検証に使えない**。
そこで「過去も今後も同じ定義で計算できる」入力だけに絞った縮約版を作る。

  本番 score3（完全定義） … /dashboard・通知の表示専用
  score3_lite（この定義） … 検証と稼働。過去10年と今日が同じ物差し

■ 本番から落とした入力（＝縮約の中身）
  軸2 CME先物ギャップ  … 過去の寄り前スナップショットが残っていない
  軸3 大口空売り件数    … 同上（日次の件数集計が遡れない）
  軸3 信用買い残の前週比 … JPX に年別アーカイブが無く、過去分の URL が 404
                          （2016/2020/2024 の各週で実測）→ **軸3は2入力**

■ 生存バイアス（表示に必ず添えること）
340銘柄は**現在の**構成。10年前に上場していなかった・当時不振だった銘柄が
今の顔ぶれで揃っているので、幅の指標（25日線超の割合・新高値の数）は
当時の実感より良く出る。score3_lite を使った検証結果には必ず注記する。

■ 使い方
    python precompute/score3_hist.py --out score3_lite.csv        # 10年ぶんをCSVに
    python precompute/score3_hist.py --upsert                     # Supabase にも入れる
    python precompute/score3_hist.py --years 2 --days 5 --upsert   # 日次バッチはこれ
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

UA = {"User-Agent": "Mozilla/5.0 (compatible; ruletrade.jp/1.0)"}

# 合計 → 5段階。本番 score3.py の STAGES と同じ境目にしてある
STAGES = [
    (3,   "attack",       "強気"),
    (1,   "lean_attack",  "やや強気"),
    (0,   "neutral",      "中立"),
    (-2,  "lean_defense", "やや守り"),
    (-99, "defense",      "守り"),
]


def clamp(v):
    return int(max(-2, min(2, v)))


def stage_of(total):
    for lim, key, jp in STAGES:
        if total >= lim:
            return key, jp
    return STAGES[-1][1], STAGES[-1][2]


# ══════════════════════════════════════════════════════════
#  軸1 トレンド（日経の位置＋市場の広がり＋騰落レシオ）
# ══════════════════════════════════════════════════════════
def axis1(panel: pd.DataFrame, market: pd.DataFrame) -> pd.DataFrame:
    """本番 score3.py の axis_trend と同じ刻みで、過去も計算できる形にしたもの。

    本番は heatmap.json（当日ぶん）から広がりを読むが、ここは daily_metrics
    相当の panel から日次で数え直す。数え方は本番に合わせてある。
    """
    p = panel[["date", "ticker", "dev_25", "high_52w_ratio"]].copy()
    p["date"] = pd.to_datetime(p["date"])

    g = p.groupby("date")
    n_total = g["ticker"].count()
    # 25日線より上の割合。本番は heatmap の g25>=0 を数えている＝乖離率が0以上
    pct_ma25 = g["dev_25"].apply(lambda s: (s >= 0).sum()) / n_total * 100
    # 本番: hi>=0 が52週高値更新 / hi<=-50 が高値から半値以下。
    # high_52w_ratio は「52週高値に対する終値の%」なので 100 と 50 が境目
    n_high = g["high_52w_ratio"].apply(lambda s: (s >= 100).sum())
    n_half = g["high_52w_ratio"].apply(lambda s: (s <= 50).sum())

    m = market.copy()
    m["date"] = pd.to_datetime(m["date"])
    m = m.set_index("date")

    df = pd.DataFrame(index=m.index)
    df["n_total"] = n_total
    df["pct_ma25"] = pct_ma25
    df["n_high"] = n_high
    df["n_half"] = n_half
    # breadth_ratio は adv25/dec25*100 ＝ 騰落レシオそのもの（metrics.py で確認済み）
    df["adr"] = m.get("breadth_ratio")

    pts = pd.Series(0, index=df.index, dtype="int64")

    # 日経が各移動平均の上か下か（±1 ずつ）
    n = m.get("nikkei_close")
    for col in ("nikkei_ma_25", "nikkei_ma_75", "nikkei_ma_200"):
        ma = m.get(col)
        if n is None or ma is None:
            continue
        dev = n / ma - 1
        pts += np.where(dev.notna(), np.where(dev >= 0, 1, -1), 0)

    # 市場の広がり。銘柄数が少ない期間は本番と同じく判定に使わない
    ok = df["n_total"] >= 100
    pts += np.where(ok & (df["pct_ma25"] >= 60), 1, 0)
    pts += np.where(ok & (df["pct_ma25"] <= 40), -1, 0)
    pts += np.where(ok & (df["n_high"] - df["n_half"] >= 10), 1, 0)
    pts += np.where(ok & (df["n_half"] - df["n_high"] >= 10), -1, 0)

    # 騰落レシオ（過熱は減点・売られすぎは加点）
    adr = df["adr"]
    pts += np.where(adr.notna() & (adr >= 120), -1, 0)
    pts += np.where(adr.notna() & (adr <= 70), 1, 0)

    df["axis1"] = [clamp(v) for v in pts]
    return df


# ══════════════════════════════════════════════════════════
#  軸2 短期リスク（VIX・急な円高・着火判定・大型イベント）
# ══════════════════════════════════════════════════════════
def major_sq_and_payrolls(index) -> pd.Series:
    """規則で出せる大型イベントだけを日付集合にする。

    ★ここは本番と揃っていない（Fable 相談中）★
    本番 score3.py の EVENTS は FOMC・日銀・米CPI を含む**手書きの表**で、
    今後3〜4か月ぶんしか無い。過去10年ぶんの開催日は手元に無く、
    規則でも出せない（FOMC・日銀は年8回で日程が毎年ずれる）。
    そこでここでは**規則で確実に出せる2つだけ**を入れている。
      ・メジャーSQ … 3/6/9/12月の第2金曜
      ・米雇用統計 … 毎月第1金曜
    FOMC・日銀・CPI を足すかどうかは Fable の判断待ち。
    足りないぶん、軸2 は本番よりわずかに減点が少なく出る。
    """
    days = set()
    years = sorted({d.year for d in index})
    for y in years:
        for mth in range(1, 13):
            fridays = [dt.date(y, mth, d) for d in range(1, 32)
                       if _valid(y, mth, d) and dt.date(y, mth, d).weekday() == 4]
            if not fridays:
                continue
            days.add(fridays[0])                       # 米雇用統計（第1金曜）
            if mth in (3, 6, 9, 12) and len(fridays) >= 2:
                days.add(fridays[1])                   # メジャーSQ（第2金曜）
    return days


def _valid(y, m, d):
    try:
        dt.date(y, m, d)
        return True
    except ValueError:
        return False


def axis2(px: pd.DataFrame) -> pd.DataFrame:
    """crash_fetch の7フラグをそのまま使う（着火判定の定義を1つに保つ）。"""
    import crash_fetch as cf

    f = cf.features(px)
    F = cf.flag_matrix(f)
    score = F.sum(axis=1)               # 0〜7。NaN 行は sum で 0 になるので下でマスク
    valid = F.notna().all(axis=1)

    df = pd.DataFrame(index=px.index)
    df["vix"] = px["vix"]
    df["usdjpy_chg5"] = px["usdjpy"].pct_change(5) * 100
    df["crash_score"] = score.where(valid)

    pts = pd.Series(0, index=df.index, dtype="int64")

    v = df["vix"]
    pts += np.where(v.notna() & (v >= 25), -2, 0)
    pts += np.where(v.notna() & (v >= 20) & (v < 25), -1, 0)
    pts += np.where(v.notna() & (v <= 15), 1, 0)

    # 急な円高だけ減点する（円安は軸1のトレンドに出るので加点しない＝本番と同じ）
    u5 = df["usdjpy_chg5"]
    pts += np.where(u5.notna() & (u5 <= -2.0), -1, 0)

    # 着火判定: crash_fetch.STAGES は score<=4 平常 / <=5 警戒 / それ以上 危険
    cs = df["crash_score"]
    pts += np.where(cs.notna() & (cs >= 6), -2, 0)
    pts += np.where(cs.notna() & (cs == 5), -1, 0)

    ev = major_sq_and_payrolls(df.index)
    within5 = []
    for d in df.index:
        cur, n, hit = d.date(), 0, False
        while n <= 5:
            if cur in ev:
                hit = True
                break
            cur += dt.timedelta(days=1)
            if cur.weekday() < 5:
                n += 1
        within5.append(hit)
    df["event_5d"] = within5
    pts += np.where(df["event_5d"], -1, 0)

    df["axis2"] = [clamp(v) for v in pts]
    return df


# ══════════════════════════════════════════════════════════
#  軸3 需給（CFTC 日経先物・海外投資家の週次）
# ══════════════════════════════════════════════════════════
CFTC_API = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
JPX_ARCHIVE = ("https://www.jpx.co.jp/markets/statistics-equities/"
               "investor-type/00-00-archives-%02d.html")
JPX_LATEST = ("https://www.jpx.co.jp/markets/statistics-equities/"
              "investor-type/index.html")


def fetch_cftc(start="2015-01-01") -> pd.Series:
    """CFTC の投機筋・日経先物（円建て）の net(枚) 前週比。週次。"""
    params = {
        # ★ここは % 書式を使わない。SQL 側の LIKE ワイルドカードの % と衝突する
        "$where": ("market_and_exchange_names like 'NIKKEI STOCK AVERAGE YEN DENOM%' "
                   "AND report_date_as_yyyy_mm_dd >= '" + start + "T00:00:00.000'"),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "2000",
        "$select": ("report_date_as_yyyy_mm_dd,noncomm_positions_long_all,"
                    "noncomm_positions_short_all"),
    }
    url = CFTC_API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=90) as r:
        rows = json.load(r)
    if not rows:
        return pd.Series(dtype="float64")
    idx = pd.to_datetime([r["report_date_as_yyyy_mm_dd"][:10] for r in rows])
    net = pd.Series(
        [int(r["noncomm_positions_long_all"]) - int(r["noncomm_positions_short_all"])
         for r in rows], index=idx).sort_index()
    return net.diff()          # 前週比（枚）


def _jpx_xls_links(html: str):
    return sorted(set(re.findall(r'href="([^"]*stock_val_1_\d+\.xls)"', html)))


FLOW_CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cache", "foreign_flow_weekly.csv")


def fetch_foreign_flow(years, use_cache=True) -> pd.Series:
    """JPX 投資部門別売買状況から、海外投資家の差引（億円）を週次で。

    年別アーカイブ 00-00-archives-NN.html（NN = 2026 - 年）に週次の xls が並ぶ。
    2016〜2026 の各年で 34〜52 本あることを実測済み。

    ★キャッシュ必須★
    10年ぶんは xls が 550本ほどあり、毎回取り直すと30分以上かかる。
    一度取ったら CSV に貯め、**足りない週だけ**取りに行く。
    日次バッチでは当年の1〜2本を見るだけで済む。
    """
    import investor_flow as iflow

    cached = pd.Series(dtype="float64")
    if use_cache and os.path.exists(FLOW_CACHE):
        c = pd.read_csv(FLOW_CACHE)
        cached = pd.Series(c["net_oku"].values,
                           index=pd.to_datetime(c["week_end"])).sort_index()
        have = {d.date() for d in cached.index}
        print("  キャッシュ: %d 週（%s 〜 %s）"
              % (len(cached), cached.index[0].date(), cached.index[-1].date()),
              file=sys.stderr)
    else:
        have = set()

    out = {}
    for y in years:
        nn = 2026 - y
        # 当年はアーカイブに載る前の最新数週が index.html にしか無いので両方見る
        pages = [JPX_ARCHIVE % nn] + ([JPX_LATEST] if nn == 0 else [])
        links = []
        for url in pages:
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=60) as r:
                    links += _jpx_xls_links(r.read().decode("utf-8", "replace"))
            except Exception as e:
                print("  %d年 %s が取れません: %s" % (y, url.rsplit("/", 1)[-1], e),
                      file=sys.stderr)
        links = sorted(set(links))
        # 既に持っている週は取りに行かない。ファイル名の YYMMDD が週の頭を表す
        todo = [h for h in links if _week_start_of(h) not in _covered(have)]
        print("  %d年: %d 本中 %d 本を取得（残りはキャッシュ）"
              % (y, len(links), len(todo)), file=sys.stderr)
        for href in todo:
            full = "https://www.jpx.co.jp" + href if href.startswith("/") else href
            try:
                label, net = _parse_flow_xls(_get(full))
                d = _week_end_from_label(label)
                if d and net is not None:
                    out[d] = float(net)
            except Exception:
                continue          # 1本落ちても全体は続ける（欠けた週は前週値で埋まる）
    if out:
        s = pd.Series(out)
        s.index = pd.to_datetime(s.index)
        merged = pd.concat([cached, s.sort_index()])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = cached

    if len(merged) and use_cache:
        os.makedirs(os.path.dirname(FLOW_CACHE), exist_ok=True)
        pd.DataFrame({"week_end": merged.index.strftime("%Y-%m-%d"),
                      "net_oku": merged.values}).to_csv(FLOW_CACHE, index=False)
    return merged


def _week_start_of(href):
    """xls の名前 stock_val_1_YYMMDD.xls から週の頭の日付を取る。"""
    m = re.search(r"stock_val_1_(\d{6})\.xls", href)
    if not m:
        return None
    v = m.group(1)
    try:
        return dt.date(2000 + int(v[:2]), int(v[2:4]), int(v[4:6]))
    except ValueError:
        return None


def _covered(have):
    """持っている週末の日付から、その週の月曜〜金曜を集合にする（名前は週頭のため）。"""
    days = set()
    for d in have:
        for k in range(0, 7):
            days.add(d - dt.timedelta(days=k))
    return days


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _parse_flow_xls(raw):
    """海外投資家の差引（億円）を1本の xls から取る。

    ★シート名は市場区分の変更で変わる★
    2022年4月のプライム市場移行より前は "TSE 1st"、以後は "TSE Prime"。
    株レーダー本番の investor_flow.py は "TSE Prime" 固定なので、
    そのまま使うと2022年3月以前が丸ごと落ちる（実際に 2022-04 以降しか
    取れず、10年のうち6年が欠けた）。本番は触らずここで吸収する。
    """
    import io as _io
    import pandas as _pd
    import investor_flow as iflow

    xl = _pd.ExcelFile(_io.BytesIO(raw))
    sheet = next((s for s in ("TSE Prime", "TSE 1st") if s in xl.sheet_names), None)
    if sheet is None:
        return None, None
    df = _pd.read_excel(xl, sheet_name=sheet, header=None)

    label = None
    for i in range(min(8, len(df))):
        for v in df.iloc[i].tolist():
            s = str(v)
            if "週" in s and "年" in s:
                label = re.sub(r"\s+", " ", s).strip()
                break
        if label:
            break

    for i in range(len(df) - 1):
        head = str(df.iloc[i, 0]).replace("　", "").replace(" ", "")
        if head.startswith("海外投資家"):
            sell = iflow.first_number(df.iloc[i].tolist()[1:])
            buy = iflow.first_number(df.iloc[i + 1].tolist()[1:])
            if sell and buy:
                return label, round((buy - sell) / 100000)   # 千円 → 億円
    return label, None


def _week_end_from_label(label):
    """「2026年9月第1週(9/1〜9/5)」のような表記から週末の日付を取る。"""
    if not label:
        return None
    m = re.search(r"(\d{4})年", str(label))
    y = int(m.group(1)) if m else None
    m2 = re.findall(r"(\d{1,2})/(\d{1,2})", str(label))
    if not (y and m2):
        return None
    mm, dd = m2[-1]
    try:
        return dt.date(y, int(mm), int(dd))
    except ValueError:
        return None


def axis3(index, years) -> pd.DataFrame:
    """週次の2入力を日次に前方補完して合成する（信用買い残は取得不能＝2入力）。"""
    cot = fetch_cftc()
    flow = fetch_foreign_flow(years)

    df = pd.DataFrame(index=index)
    df["cot_change"] = cot.reindex(index, method="ffill") if len(cot) else np.nan
    df["foreign_oku"] = flow.reindex(index, method="ffill") if len(flow) else np.nan

    pts = pd.Series(0, index=index, dtype="int64")
    fo = df["foreign_oku"]
    pts += np.where(fo.notna() & (fo >= 2000), 1, 0)
    pts += np.where(fo.notna() & (fo <= -2000), -1, 0)
    ch = df["cot_change"]
    pts += np.where(ch.notna() & (ch >= 3000), 1, 0)
    pts += np.where(ch.notna() & (ch <= -3000), -1, 0)

    df["axis3"] = [clamp(v) for v in pts]
    df["axis3_inputs"] = (df["cot_change"].notna().astype(int)
                          + df["foreign_oku"].notna().astype(int))
    return df


# ══════════════════════════════════════════════════════════
def build(years_back=10, days=None, log=print):
    """全期間ぶんを計算して返す。days を指定すると末尾のN営業日だけに絞る。

    日次バッチは days=5 程度・years_back=2 で呼ぶ（毎日10年を回さない）。
    軸1の移動平均と52週高値は run_pipeline が1.2年ぶん余分に取ってから
    切り落とすので、years_back=2 でも直近の値は10年計算と一致する。
    """
    import crash_fetch as cf
    import pipeline

    log("[1/4] 個別株と相場環境を再計算（daily_metrics 相当）")
    panel, market, _ = pipeline.run_pipeline(years=years_back, log=lambda *a: None)

    log("[2/4] 軸1 トレンド")
    a1 = axis1(panel, market)

    log("[3/4] 軸2 短期リスク（着火7フラグは crash_fetch をそのまま使う）")
    px = cf.load()
    a2 = axis2(px)

    log("[4/4] 軸3 需給（CFTC＋JPX 投資部門別）")
    years = sorted({d.year for d in a1.index})
    a3 = axis3(a1.index, years)

    out = pd.DataFrame(index=a1.index)
    out["score3_axis1"] = a1["axis1"]
    out["score3_axis2"] = a2["axis2"].reindex(a1.index)
    out["score3_axis3"] = a3["axis3"]
    out["axis3_inputs"] = a3["axis3_inputs"]
    # ★内訳も必ず残す★
    # 点数だけだと「なぜこの日が守りなのか」を後から追えない。
    # 本番 score3 との食い違いを調べるとき、内訳が無いと計算を回し直す羽目になる
    # （実際に軸1の1点差で回し直した）。CSV には出し、DB には点数だけ入れる。
    for src, cols in ((a1, ("n_total", "pct_ma25", "n_high", "n_half", "adr")),
                      (a2, ("vix", "usdjpy_chg5", "crash_score", "event_5d")),
                      (a3, ("cot_change", "foreign_oku"))):
        for c in cols:
            if c in src.columns:
                out[c] = src[c].reindex(a1.index)
    # ★数が揃っていない日は出さない（黙って間違った値を入れない）★
    # ローカルの yfinance キャッシュが古いと直近数日だけ銘柄数が激減する
    # （実際に 336 → 22 になり、広がりの判定が丸ごと外れて軸1が別物になった）。
    # 広がりを計算できない日は「軸1が移動平均だけの値」になるので、そのまま
    # 保存すると**間違いだと分からない数字**が残る。行ごと落とす。
    # §19 相談⑥「壊れた数字は公開しない」と同じ考え方。
    MIN_TICKERS = 200
    short = out["n_total"] < MIN_TICKERS
    if short.any():
        bad = out.index[short]
        log("  ★銘柄数が足りない %d 日を除外（%s 〜 %s・最少 %d 銘柄）"
            % (int(short.sum()), bad.min().date(), bad.max().date(),
               int(out.loc[short, "n_total"].min())))
        log("    取得が追いついていません。CI は毎回取り直すので通常は出ません。")
        out = out[~short]

    # 軸2が取れない日（指数の欠損など）は合計を出さない＝黙って0にしない
    out = out.dropna(subset=["score3_axis2"])
    out["score3_axis2"] = out["score3_axis2"].astype(int)
    out["score3_lite"] = (out["score3_axis1"] + out["score3_axis2"]
                          + out["score3_axis3"]).astype(int)
    labels = [stage_of(v) for v in out["score3_lite"]]
    out["score3_key"] = [k for k, _ in labels]
    out["score3_label"] = [j for _, j in labels]
    if days:
        out = out.tail(int(days))
    return out.reset_index().rename(columns={"index": "date"})


def main():
    ap = argparse.ArgumentParser(description="score3_lite を過去10年ぶん計算する")
    ap.add_argument("--out", default="score3_lite.csv", help="書き出す CSV")
    ap.add_argument("--years", type=int, default=10,
                    help="何年ぶん計算するか。日次バッチは 2 で足りる")
    ap.add_argument("--days", type=int, default=None,
                    help="末尾のN営業日だけに絞る（日次バッチ用。例: --years 2 --days 5）")
    ap.add_argument("--upsert", action="store_true", help="market_condition にも入れる")
    args = ap.parse_args()

    df = build(years_back=args.years, days=args.days)
    df.to_csv(args.out, index=False, encoding="utf-8-sig")
    print()
    print("期間      : %s 〜 %s (%d 営業日)"
          % (df["date"].min().date(), df["date"].max().date(), len(df)))
    print("5段階の分布:")
    for k, n in df["score3_label"].value_counts().items():
        print("   %-6s %5d 日 (%.1f%%)" % (k, n, n / len(df) * 100))
    print("軸3の入力数: %s" % df["axis3_inputs"].value_counts().to_dict())
    print("→ %s に書きました" % args.out)

    if args.upsert:
        import supabase_io
        cols = ["date", "score3_lite", "score3_axis1", "score3_axis2",
                "score3_axis3", "score3_label"]
        recs = supabase_io.frame_to_records(df[cols])
        supabase_io.upsert("market_condition", recs, on_conflict="date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
