# -*- coding: utf-8 -*-
"""予兆レーダー: 出来事の台帳（v2）の集計と公開用 JSON（検証台帳 設計書 v1 段階4 ＋ 6章の通知の材料・2026-11-18）

■ 入力（どれも data/ledger2/ の下・追記のみのファイル）
  events/ ＋ scores/            当日記録（origin=live）
  imported/                     既存のシグナル台帳（docs/ledger・2026-05〜11-17）を当時の記録として取り込んだもの（origin=live）
  backfill/                     遡及（origin=backfill。ledger2_backfill.py）
  温度計の段階: docs/thermo100_hist.json の ℃（後から同じ式で計算し直した値）を、出来事の日ごとに4段階へ
    総崩れ −20〜−9℃ / 弱気 −8〜11℃ / 中立 12〜27℃ / 強気 28〜60℃

■ 集計（設計書 5章）
  統計レコード = 出来事の種類 × 条件帯 × 窓（1・5・20・60営業日）。条件帯は「全期間（all）」と温度計の4段階
  （＋温度計・傾斜計の段階変化だけは「変わった先の段階」）。条件の重ねは今回しない。
  中身: 件数(n)・発生日数(days)・平均・中央値・プラスだった割合・対象期間・ステータス、live と backfill の内訳。
  値: 個別銘柄は対 TOPIX（1306.T）の超過リターン（ret）。市場全体（MKT）は日経平均の騰落（raw_ret）。

■ 検定（日付を1観測）
  同じ日の複数銘柄は、その日の平均にまとめてから検定する（銘柄数で数えると過大に出る）。
  窓が重なる（例: 20営業日の窓を毎日とる）と隣の日どうしが似るので、t 値の分散は「営業日の距離が窓より近い日の組」
  も数える Bartlett の重み（重なりの割合 = 1 − 距離÷窓）で直す（Newey–West と同じ考え方を、飛び飛びの日付に当てたもの）。
  p 値は t 分布（自由度 = 日数−1）の両側。
    confirmed    : p < 0.05、かつ 期間を前半・後半（日数で半分）に分けても、両方とも全体と同じ向き
    weak         : confirmed でなく p < 0.10
    rejected     : どちらにも当たらない（全件残す）
    insufficient : 発生日数 30日未満（温度計の段階別も同じ）
  live の印: live が20件以上あり、live の平均が全体と同じ向きか（live_ok）。

■ 出力
  data/ledger2/stats/stats.json   … 統計の全量（rejected・insufficient も全部）と仮説の表
  docs/yochou/stats.json          … 種類ごとの要約（サイトの成績ページ用。4つのステータスを全部）
  docs/yochou/today.json          … その日の出来事の件数と、基準を満たす予兆（上限30件）
  data/ledger2/signals/YYYY-MM.jsonl … 出した予兆を1件1行で追記（event_id で重複しない。外れも消さない）
  docs/yochou/record.json         … 出した予兆と、答えが出たものの結果（全件）
  JPX の生の値（信用残・空売り残高の数字）は docs/ に出さない。出すのは件数と派生した統計だけ。

■ 言葉（設計書 6章）
  文面は事実と過去の統計だけ。禁止語（BANNED）を含む文面は出さない（テストあり）。

使い方:
  python -X utf8 yochou.py                    # 毎営業日（ledger-daily.yml の最後）
  python -X utf8 yochou.py --out <dir> --docs-out <dir>   # 試験（本番の場所に書かない）
"""
import argparse
import json
import math
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import ledger2_detect as L

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "ledger2"
DOCS_OUT = HERE / "docs" / "yochou"
JST = timezone(timedelta(hours=9))
H = (1, 5, 20, 60)
WINDOW = 20                    # 予兆の文面と基準に使う窓（固定。窓を選び直して良い結果を拾わない）
MIN_DAYS = 30                  # これ未満は insufficient
SIGNAL_MIN_DAYS = 50           # 予兆に出す発生日数の下限（設計書 6章）
LIVE_MIN = 20                  # live の件数の基準（設計書 6章）
MAX_SIGNALS = 30
RECORD_SPLIT_BYTES = 2_000_000

STAGES = (("PANIC", "総崩れ", -20, -9), ("BEARISH", "弱気", -8, 11), ("NEUTRAL", "中立", 12, 27), ("BULLISH", "強気", 28, 60))
STAGE_JA = {k: ja for k, ja, _, _ in STAGES}

# ════════════════════════════════════════ 仮説（集計の前に書いた。向き +1=上がる / −1=下がる / 0=向きを決めない）
# 値は「知り得たあとの最初の始値」からの対 TOPIX の超過（市場全体は日経平均）。2026-11-18 に集計より先に書いた。
HYPOTHESES = {
    "volume_surge":        (-1, "出来高が急に増えた銘柄は、材料が翌日の始値までに織り込まれ、その後は対TOPIXでやや下がる"),
    "stop_high":           (-1, "終値でストップ高の銘柄は、翌日の始値から測ると行き過ぎの戻しで下がる"),
    "stop_low":            (+1, "終値でストップ安の銘柄は、翌日の始値から測ると売られすぎの戻りで上がる"),
    "stop_high_touch":     (-1, "一時ストップ高で引けで押し戻された銘柄は、上値が重く下がる"),
    "stop_low_touch":      (+1, "一時ストップ安から引けで戻した銘柄は、下げ止まって上がる"),
    "gap_up":              (-1, "5%以上高く寄った銘柄は、翌日の始値からは窓を埋める動きで下がる"),
    "gap_down":            (+1, "5%以上安く寄った銘柄は、翌日の始値からは戻りで上がる"),
    "high52_close":        (+1, "終値で52週高値の銘柄は、勢いが続いて上がる"),
    "low52_close":         (-1, "終値で52週安値の銘柄は、弱さが続いて下がる"),
    "ytd_high_close":      (+1, "終値で年初来高値の銘柄は、勢いが続いて上がる"),
    "ytd_low_close":       (-1, "終値で年初来安値の銘柄は、弱さが続いて下がる"),
    "earnings_revision_up":   (+1, "業績見通しの上方修正は、発表後もじわじわ上がる"),
    "earnings_revision_down": (-1, "業績見通しの下方修正は、発表後もじわじわ下がる"),
    "dividend_up":         (+1, "増配の発表のあとは上がる"),
    "dividend_down":       (-1, "減配の発表のあとは下がる"),
    "buyback_decision":    (+1, "自己株式の取得の決議のあとは上がる"),
    "stock_split":         (+1, "株式分割の発表のあとは上がる"),
    "yutai_new":           (+1, "株主優待の新設のあとは上がる"),
    "yutai_end":           (-1, "株主優待の廃止のあとは下がる"),
    "margin_buy_surge":    (-1, "信用買残が急に増えた銘柄は、将来の返済の売りが重く下がる"),
    "short_new_inst":      (-1, "空売り残高の報告に新しい機関が載った銘柄は、下がる"),
    "short_surge":         (-1, "機関の空売り残高が急に増えた銘柄は、下がる"),
    "thermo_stage_change": (0, "温度計の段階が変わったあとの日経平均。向きは段階の変わり方しだいなので、変わった先の段階ごとに見る"),
    "gauge_stage_up":      (-1, "暴落の傾斜計の段階が上がったあとは、日経平均が下がる"),
    "signal_ai":           (0, "AI朝刊の判定（サイトの判定。向きは決めない）"),
    "signal_score3":       (0, "3点スコアの判定（サイトの判定。向きは決めない）"),
    "signal_crash":        (-1, "暴落の判定のあとは日経平均が下がる"),
    "signal_free_scanner": (+1, "無料スキャナーの検出銘柄は上がる"),
    "signal_tenbagger":    (+1, "十倍株候補は上がる"),
    "signal_movers":       (0, "急騰・急落の一覧（向きの混ざった一覧なので向きは決めない）"),
    "signal_treasure_quiet":  (+1, "宝探し（静かな銘柄）は上がる"),
    "signal_treasure_contra": (+1, "宝探し（逆張り）は上がる"),
    "signal_treasure_card":   (+1, "宝探し（カード）は上がる"),
}

# 文面の言い回し（事実だけ）。{p} は event_params
PHRASE = {
    "volume_surge": "出来高が20日平均の3倍以上に",
    "stop_high": "終値でストップ高",
    "stop_low": "終値でストップ安",
    "stop_high_touch": "一時ストップ高（終値は上限に届かず）",
    "stop_low_touch": "一時ストップ安（終値は下限から戻した）",
    "gap_up": "前日終値より5%以上高く寄り付き",
    "gap_down": "前日終値より5%以上安く寄り付き",
    "high52_close": "終値で52週高値",
    "low52_close": "終値で52週安値",
    "ytd_high_close": "終値で年初来高値",
    "ytd_low_close": "終値で年初来安値",
    "earnings_revision_up": "業績見通しの上方修正を発表",
    "earnings_revision_down": "業績見通しの下方修正を発表",
    "dividend_up": "増配を発表",
    "dividend_down": "減配を発表",
    "buyback_decision": "自己株式の取得を決議",
    "stock_split": "株式分割を発表",
    "yutai_new": "株主優待の新設を発表",
    "yutai_end": "株主優待の廃止を発表",
    "margin_buy_surge": "信用買残が前回比30%以上増加",
    "short_new_inst": "空売り残高の報告に新しい機関（0.5%以上）",
    "short_surge": "機関の空売り残高割合が前回比30%以上増加",
    "gauge_stage_up": "暴落の傾斜計の段階が上がった",
}
LABEL_FIX = {"earnings_revision_up": "業績見通しの上方修正", "earnings_revision_down": "業績見通しの下方修正",
             "signal_ai": "AI朝刊の判定", "signal_score3": "3点スコアの判定", "signal_crash": "暴落の判定",
             "signal_free_scanner": "無料スキャナーの検出", "signal_tenbagger": "十倍株候補",
             "signal_movers": "急騰・急落の一覧", "signal_treasure_quiet": "宝探し（静か）",
             "signal_treasure_contra": "宝探し（逆張り）", "signal_treasure_card": "宝探し（カード）"}

# 出さない言葉（設計書 6章 ＋ 依頼）。「買残」「売残」は可。「空売り残高」は JPX の統計の名前なので可（それ以外の「売り」は不可）
BANNED = ("買い", "売り", "狙い目", "チャンス", "推奨", "予想", "警戒", "おすすめ", "必ず", "有望", "過熱", "底", "天井")


def banned_in(text):
    t = text.replace("買残", "").replace("売残", "").replace("空売り残高", "")
    return [w for w in BANNED if w in t]


def label_of(t, types=None):
    if t in LABEL_FIX:
        return LABEL_FIX[t]
    return ((types or L.TYPES).get(t) or {}).get("label") or t


# ──────────────────────────────────────── 統計の道具（scipy を使わない）
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 300, 3e-14, 1e-300
    qab, qap, qam = a + b, a + 1, a - 1
    c, d = 1.0, 1 - qab * x / qap
    d = 1 / (d if abs(d) > FPMIN else FPMIN)
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > FPMIN else FPMIN)
        c = 1 + aa / c if abs(1 + aa / c) > FPMIN else FPMIN
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d
        d = 1 / (d if abs(d) > FPMIN else FPMIN)
        c = 1 + aa / c if abs(1 + aa / c) > FPMIN else FPMIN
        de = d * c
        h *= de
        if abs(de - 1) < EPS:
            break
    return h


def betainc(a, b, x):
    """正則化不完全ベータ関数 I_x(a, b)"""
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    bt = math.exp(lbt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1 - bt * _betacf(b, a, 1 - x) / b


def t_pvalue(t, df):
    """t 分布の両側 p 値"""
    if df <= 0 or t is None or not math.isfinite(t):
        return None
    return betainc(df / 2, 0.5, df / (df + t * t))


def date_level_test(day_vals, day_pos, h):
    """日付ごとの平均 day_vals（日付の順）と、その日の営業日の通し番号 day_pos → (平均, t, p, 素朴な p)。
    分散は Bartlett の重み w = max(0, 1 − |営業日の距離| / h) で、窓の重なる日の組を数える（h=1 なら素朴な t 検定と同じ）。
    補正後の分散が素朴な分散より小さくなるときは素朴な方を使う（控えめな側）"""
    n = len(day_vals)
    if n < 2:
        return None, None, None, None
    m = sum(day_vals) / n
    e = [x - m for x in day_vals]
    s0 = sum(x * x for x in e)
    var_naive = s0 / (n - 1) / n
    lag = 0.0
    j0 = 0
    for i in range(n):
        while day_pos[i] - day_pos[j0] >= h:
            j0 += 1
        for j in range(j0, i):
            w = 1 - (day_pos[i] - day_pos[j]) / h
            if w > 0:
                lag += 2 * w * e[i] * e[j]
    var = (s0 + lag) / (n - 1) / n               # 重なりが無ければ（lag=0）素朴な t 検定の分散と同じ
    var = max(var, var_naive)                    # 補正で分散が小さくなるとき（負の相関）は素朴な分散を使う（控えめな側）
    t = m / math.sqrt(var) if var > 0 else None
    tn = m / math.sqrt(var_naive) if var_naive > 0 else None
    return m, t, t_pvalue(t, n - 1), t_pvalue(tn, n - 1)


def median(xs):
    s = sorted(xs)
    n = len(s)
    if not n:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def sign(x):
    return 0 if x is None or x == 0 else (1 if x > 0 else -1)


# ──────────────────────────────────────── 読み込み
def read_any(p):
    import gzip
    p = Path(p)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    return L.jsonl(p)


def load_all(data_dir=DATA):
    data_dir = Path(data_dir)
    events, scores = {}, {}
    for sub in ("backfill", "imported", ""):
        base = data_dir / sub if sub else data_dir
        for f in sorted((base / "events").glob("*.jsonl*")):
            for r in read_any(f):
                if r["event_id"] in events:
                    continue
                # 集計に要る欄だけ持つ（遡及は数十万件あるので）
                keep = {k: r.get(k) for k in ("event_id", "date", "code", "event_type", "origin", "imported_from", "base_date")}
                if r["event_type"] in ("thermo_stage_change", "gauge_stage_up"):
                    keep["event_params"] = r.get("event_params")
                events[r["event_id"]] = keep
        files = sorted((base / "scores").glob("*.jsonl*"))
        for f in files:
            for r in read_any(f):
                if r.get("h") is None:
                    continue
                k = (r["event_id"], r["h"])
                if k not in scores:
                    scores[k] = {"ret": r.get("ret"), "raw_ret": r.get("raw_ret"), "status": r.get("status"),
                                 "end_date": r.get("end_date")}
    return events, scores


def load_baseline(data_dir=DATA):
    """{h: {起点の日: (全銘柄の対TOPIX超過の平均, 中央値)}}（ledger2_backfill.write_baseline）。無ければ {}
    「同じ日の全銘柄との差」は 出来事の値 − その起点の日の全銘柄の平均（等ウエイト）"""
    p = Path(data_dir) / "backfill" / "baseline.json.gz"
    if not p.exists():
        return {}
    rows = read_any(p)
    return {int(h): {d: (v[0], v[2] if len(v) > 2 else None) for d, v in m.items()}
            for h, m in (rows[0].get("by_h") or {}).items()} if rows else {}


def thermo_stage_by_date(docs):
    th = (L.jload(Path(docs) / "thermo100_hist.json", {}) or {}).get("items") or []
    out = {}
    for x in th:
        tc = x.get("tc")
        if tc is None:
            continue
        for k, ja, lo, hi in STAGES:
            if lo <= tc <= hi:
                out[x["d"]] = k
                break
        else:
            out[x["d"]] = "PANIC" if tc < -9 else "BULLISH"
    return out


def biz_ordinal(dates):
    """日付 → 営業日の通し番号（jp_bizday）"""
    if not dates:
        return {}
    lo, hi = min(dates), max(dates)
    out, d, i = {}, lo, 0
    while d <= hi:
        out[d] = i
        nd = L.next_bizday(d)
        i += 1
        d = nd
    # 休日に出来事の日付があっても、次の営業日と同じ番号にする
    res = {}
    for x in dates:
        y = x
        while y not in out:
            y = L.next_bizday(y)
        res[x] = out[y]
    return res


# ──────────────────────────────────────── 集計
def metric_of(e, s):
    return s.get("raw_ret") if e["code"] == "MKT" else s.get("ret")


def band_keys(e, stage_by_date):
    keys = ["all"]
    st = stage_by_date.get(e["date"])
    if st:
        keys.append(f"regime:{st}")
    if e["event_type"] in ("thermo_stage_change", "gauge_stage_up"):
        to = (e.get("event_params") or {}).get("to")
        if to:
            keys.append(f"to:{to}")
    return keys


def stat_record(t, band, h, obs, ordinal):
    """obs: [(date, value, origin)] → 統計レコード"""
    vals = [v for _, v, _ in obs]
    n = len(vals)
    by_day = defaultdict(list)
    for d, v, _ in obs:
        by_day[d].append(v)
    days = sorted(by_day)
    day_vals = [sum(by_day[d]) / len(by_day[d]) for d in days]
    hyp_dir, hyp = HYPOTHESES.get(t, (0, ""))
    rec = {"event_type": t, "label": label_of(t), "band": band, "window": h,
           "metric": "日経平均の騰落（%）" if t in MKT_TYPES else "対TOPIX（1306.T）の超過（%）",
           "n": n, "days": len(days),
           "mean": round(sum(vals) / n, 3) if n else None, "median": round(median(vals), 3) if n else None,
           "pos_rate": round(sum(1 for v in vals if v > 0) / n * 100, 1) if n else None,
           "neg_rate": round(sum(1 for v in vals if v < 0) / n * 100, 1) if n else None,
           "period": [days[0], days[-1]] if days else None,
           "hypothesis": hyp, "hypothesis_dir": hyp_dir}
    live = [v for _, v, o in obs if o == "live"]
    rec["live_n"] = len(live)
    rec["backfill_n"] = n - len(live)
    rec["live_days"] = len({d for d, _, o in obs if o == "live"})
    rec["live_mean"] = round(sum(live) / len(live), 3) if live else None
    if len(days) < 2:
        rec.update({"day_mean": None, "t": None, "p_value": None, "p_naive": None, "half1_mean": None, "half2_mean": None,
                    "direction": 0, "status": "insufficient", "live_ok": False, "hypothesis_match": None})
        return rec
    m, tt, p, pn = date_level_test(day_vals, [ordinal[d] for d in days], h)
    half = len(days) // 2
    h1 = sum(day_vals[:half]) / half if half else None
    h2 = sum(day_vals[half:]) / (len(days) - half)
    dr = sign(m)
    both = dr != 0 and sign(h1) == dr and sign(h2) == dr
    if len(days) < MIN_DAYS:
        st = "insufficient"
    elif p is not None and p < 0.05 and both:
        st = "confirmed"
    elif p is not None and p < 0.10:
        st = "weak"
    else:
        st = "rejected"
    rec.update({"day_mean": round(m, 3), "t": round(tt, 3) if tt is not None else None,
                "p_value": round(p, 5) if p is not None else None, "p_naive": round(pn, 5) if pn is not None else None,
                "half1_mean": round(h1, 3) if h1 is not None else None, "half2_mean": round(h2, 3),
                "direction": dr, "status": st,
                "live_ok": len(live) >= LIVE_MIN and sign(rec["live_mean"]) == dr,
                "hypothesis_match": (None if hyp_dir == 0 or st in ("insufficient",) else (dr == hyp_dir))})
    return rec


MKT_TYPES = {"thermo_stage_change", "gauge_stage_up", "signal_ai", "signal_score3", "signal_crash"}


def build_stats(events, scores, stage_by_date, baseline=None):
    baseline = baseline or {}
    groups = defaultdict(list)          # (type, band, h) → [(date, value, origin)]
    for (eid, h), s in scores.items():
        e = events.get(eid)
        if not e or s.get("status") == "invalid":
            continue
        v = metric_of(e, s)
        if v is None:
            continue
        if e["code"] == "MKT":
            MKT_TYPES.add(e["event_type"])
        org = "live" if e.get("origin") == "live" else "backfill"
        for b in band_keys(e, stage_by_date):
            groups[(e["event_type"], b, h)].append((e["date"], v, org, e.get("base_date")))
    ordinal = biz_ordinal(sorted({o[0] for obs in groups.values() for o in obs}))
    recs = []
    for (t, b, h), obs in sorted(groups.items()):
        rec = stat_record(t, b, h, [o[:3] for o in obs], ordinal)
        rec["status_topix"] = rec["status"]          # 対TOPIX の検定の結果（status と同じ。意味を明示する欄）
        if t not in MKT_TYPES and baseline.get(h):
            rec.update(vs_all(obs, baseline[h], ordinal, h))
        # 予兆に出せるか（統括 2026-11-18）: 対TOPIX と 同じ日の全銘柄との差 の両方で confirmed かつ同じ向き。
        # 市場全体（日経平均で測る種類）は全銘柄との差が無いので、日経平均の検定だけで決める
        if t in MKT_TYPES:
            rec["signal_ok"] = rec["status"] == "confirmed"
        else:
            rec["signal_ok"] = (rec["status"] == "confirmed" and rec.get("vs_all_status") == "confirmed"
                                and rec.get("vs_all_direction") == rec["direction"] != 0)
        recs.append(rec)
    return recs


def vs_all(obs, base, ordinal, h):
    """同じ起点の日の「全銘柄の平均」を引いた値（同じ日の全銘柄との差）で、同じ日付単位の検定をする
    （小型株の地合いなど、出来事に関係なく出る分を除いた見方。統括 2026-11-18: 予兆はこちらも confirmed のものだけ）"""
    by_day = defaultdict(list)
    diffs, bmeds = [], []
    for d, v, _, b in obs:
        if b in base:
            dv = v - base[b][0]
            by_day[d].append(dv)
            diffs.append(dv)
            if base[b][1] is not None:
                bmeds.append(base[b][1])
    days = sorted(by_day)
    extra = {"vs_all_n": len(diffs),
             "median_vs_all": round(median(diffs), 3) if diffs else None,
             "pos_rate_vs_all": round(sum(1 for x in diffs if x > 0) / len(diffs) * 100, 1) if diffs else None,
             "neg_rate_vs_all": round(sum(1 for x in diffs if x < 0) / len(diffs) * 100, 1) if diffs else None,
             # 同じ期間の全銘柄の対TOPIX 中央値（出来事ごとの起点の日の、全銘柄の中央値の中央値）
             "base_median": round(median(bmeds), 3) if bmeds else None}
    if len(days) < 2:
        return {"vs_all_days": len(days), "vs_all_day_mean": None, "vs_all_p": None, "vs_all_status": "insufficient",
                "vs_all_direction": 0, **extra}
    vals = [sum(by_day[d]) / len(by_day[d]) for d in days]
    m, t, p, _ = date_level_test(vals, [ordinal[d] for d in days], h)
    half = len(days) // 2
    h1, h2 = sum(vals[:half]) / half, sum(vals[half:]) / (len(vals) - half)
    dr = sign(m)
    if len(days) < MIN_DAYS:
        st = "insufficient"
    elif p is not None and p < 0.05 and dr != 0 and sign(h1) == dr and sign(h2) == dr:
        st = "confirmed"
    elif p is not None and p < 0.10:
        st = "weak"
    else:
        st = "rejected"
    return {"vs_all_days": len(days), "vs_all_day_mean": round(m, 3), "vs_all_p": round(p, 5) if p is not None else None,
            "vs_all_status": st, "vs_all_direction": dr, **extra}


# ──────────────────────────────────────── 文面
def period_label(period):
    if not period:
        return ""
    d0, d1 = date.fromisoformat(period[0]), date.fromisoformat(period[1])
    yrs = (d1 - d0).days / 365.25
    if yrs >= 1.5:
        return f"過去{round(yrs)}年"
    if yrs >= 0.9:
        return "過去1年"
    return f"過去{max(1, round((d1 - d0).days / 30.4))}か月"


def phrase_of(e):
    t = e["event_type"]
    if t == "thermo_stage_change":
        p = e.get("event_params") or {}
        return f"相場の温度計が「{STAGE_JA.get(p.get('from'), p.get('from'))}」から「{STAGE_JA.get(p.get('to'), p.get('to'))}」に"
    return PHRASE.get(t) or label_of(t)


def make_text(e, rec):
    who = "市場全体" if e["code"] == "MKT" else e["code"]
    bench = "日経平均" if e["code"] == "MKT" else "対TOPIX"
    med = rec["median"]
    if med is not None and med < 0:
        share = f"マイナスだった割合{rec['neg_rate']:.0f}%"
    else:
        share = f"プラスだった割合{rec['pos_rate']:.0f}%"
    va = (f"（同じ日の全銘柄との差は中央値{rec['median_vs_all']:+.1f}%）"
          if e["code"] != "MKT" and rec.get("median_vs_all") is not None else "")
    txt = (f"【予兆】{who} {phrase_of(e)}。{period_label(rec['period'])}で同じ条件は{rec['n']:,}回（{rec['days']:,}日）。"
           f"{rec['window']}営業日後の{bench}は中央値{med:+.1f}%{va}、{share}。")
    return txt


# ──────────────────────────────────────── 出力
def write_json(p, obj, indent=None):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":")),
                   encoding="utf-8")
    tmp.replace(p)


def names(docs):
    out = {}
    try:
        import csv
        with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("code"):
                    out[r["code"].strip()] = unicodedata.normalize("NFKC", r.get("name") or "").strip()
    except FileNotFoundError:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(DATA), help="data/ledger2（読む）")
    ap.add_argument("--out", default=None, help="stats と signals の書き先（既定: --data と同じ）")
    ap.add_argument("--docs-out", default=str(DOCS_OUT))
    ap.add_argument("--docs", default=str(HERE / "docs"))
    ap.add_argument("--date", help="today.json の日付（既定: 当日記録の最新日）")
    a = ap.parse_args()
    data_dir, out = Path(a.data), Path(a.out or a.data)
    docs_out = Path(a.docs_out)
    now = datetime.now(JST).isoformat(timespec="seconds")

    events, scores = load_all(data_dir)
    stage_by_date = thermo_stage_by_date(a.docs)
    recs = build_stats(events, scores, stage_by_date, load_baseline(data_dir))
    idx = {(r["event_type"], r["band"], r["window"]): r for r in recs}
    bf_meta = L.jload(data_dir / "backfill" / "meta.json", {}) or {}
    cnt_st = Counter(r["status"] for r in recs if r["band"] == "all")
    print(f"出来事 {len(events)}件・答え {len(scores)}行 → 統計レコード {len(recs)}件（全期間のうち {dict(cnt_st)}）")

    # ── 統計の全量（data/ 側）
    write_json(out / "stats" / "stats.json", {
        "schema": "yochou_stats/1", "updated": now,
        "method": {"unit": "出来事の種類 × 条件帯 × 窓", "windows": list(H),
                   "metric": "個別銘柄=対TOPIX（1306.T）の超過・市場全体=日経平均の騰落。起点は知り得たあとの最初の始値",
                   "test": "日付を1観測（同じ日の銘柄は平均）。分散は窓の重なりを Bartlett の重みで補正。t 分布の両側 p",
                   "status": {"confirmed": "p<0.05 かつ前半・後半とも同じ向き", "weak": "p<0.10", "rejected": "それ以外",
                              "insufficient": f"発生日数{MIN_DAYS}日未満"},
                   "vs_all": "同じ起点の日の全銘柄（等ウエイト）の対TOPIX超過の平均を引いた値（同じ日の全銘柄との差）で同じ検定（vs_all_*）。"
                             "status / status_topix は対TOPIX の結果、vs_all_status は全銘柄との差の結果。予兆は両方 confirmed かつ同じ向き（signal_ok）",
                   "bands": "all ＝全期間 / regime:<段階> ＝温度計の段階別（thermo100_hist の℃・後から計算し直した値）/ to:<段階> ＝変わった先の段階"},
        "hypotheses": {k: {"dir": v[0], "text": v[1]} for k, v in HYPOTHESES.items()},
        "backfill": {k: bf_meta.get(k) for k in ("price_period", "adjusted_asof", "survivorship", "thermo", "note")},
        "records": recs}, indent=None)

    # ── 公開: 統計の要約
    keep = ("event_type", "label", "band", "window", "metric", "n", "days", "median", "mean", "pos_rate", "p_value",
            "status", "period", "live_n", "backfill_n", "live_ok", "hypothesis_dir", "hypothesis_match",
            "day_mean", "status_topix", "vs_all_day_mean", "vs_all_p", "vs_all_status", "median_vs_all",
            "pos_rate_vs_all", "base_median", "signal_ok")
    by_type = defaultdict(list)
    for r in recs:
        by_type[r["event_type"]].append({k: r.get(k) for k in keep if k not in ("event_type", "label", "metric")})
    note = (bf_meta.get("note") or "") + " 統計は日付を1観測として検定したもの。過去の統計であり、この先の値動きを示すものではない。"
    write_json(docs_out / "stats.json", {
        "v": 1, "updated": now, "windows": list(H),
        "signal_rule": "予兆に出すのは、対TOPIX（status_topix）と 同じ日の全銘柄との差（vs_all_status）の両方で confirmed かつ同じ向き"
                       "（signal_ok）・発生日数50日以上・20営業日の窓。市場全体は日経平均の検定だけ",
        "status_rule": {"confirmed": "日付単位の検定で p<0.05、かつ期間の前半・後半とも同じ向き", "weak": "p<0.10",
                        "rejected": "どちらにも当たらない", "insufficient": f"発生日数{MIN_DAYS}日未満"},
        "summary": dict(Counter(r["status"] for r in recs)),
        "types": [{"type": t, "label": label_of(t), "metric": "日経平均の騰落" if t in MKT_TYPES else "対TOPIXの超過",
                   "hypothesis": HYPOTHESES.get(t, (0, ""))[1], "rows": rows} for t, rows in sorted(by_type.items())],
        "note": note})

    # ── 今日
    live_dates = sorted({e["date"] for e in events.values() if e.get("origin") == "live" and not e.get("imported_from")})
    day = a.date or (live_dates[-1] if live_dates else None)
    today_ev = [e for e in events.values() if e["date"] == day and e.get("origin") == "live" and not e.get("imported_from")]
    counts = Counter(e["event_type"] for e in today_ev if not e["event_type"].startswith("signal_"))
    stage_today = stage_by_date.get(day)
    nm = names(a.docs)
    sigs = []
    for e in today_ev:
        t = e["event_type"]
        if t.startswith("signal_"):
            continue                              # サイト自身の判定は予兆に重ねない（統計は stats.json にある）
        rec = idx.get((t, "all", WINDOW))
        if not rec or not rec.get("signal_ok") or rec["days"] < SIGNAL_MIN_DAYS:
            continue
        reg = idx.get((t, f"regime:{stage_today}", WINDOW)) if stage_today else None
        text = make_text(e, rec)
        if banned_in(text):
            print(f"::warning::禁止語を含むので出さない: {text}")
            continue
        basis = ("live" if rec["backfill_n"] == 0 else "live+backfill" if rec["live_ok"] else "backfill")
        sigs.append({"event_id": e["event_id"], "code": "" if e["code"] == "MKT" else e["code"],
                     "name": "" if e["code"] == "MKT" else nm.get(e["code"], ""),
                     "type": t, "label": label_of(t), "text": text,
                     "n": rec["n"], "days": rec["days"], "window": WINDOW, "median": round(rec["median"], 1),
                     "pos_rate": round(rec["pos_rate"]), "period": f"{rec['period'][0][:7]}〜{rec['period'][1][:7]}",
                     "status": rec["status"], "basis": basis, "live_n": rec["live_n"],
                     "side": "up" if rec["direction"] > 0 else "down",      # up=その後強かった側・down=弱かった側（2026-11-19）
                     # 参考: 同じ起点の日の全銘柄の平均を引いた見方（小型株の地合いの分を除く）。サイトで出すかは未定
                     "median_vs_all": round(rec["median_vs_all"], 1) if rec.get("median_vs_all") is not None else None,
                     "pos_rate_vs_all": round(rec["pos_rate_vs_all"]) if rec.get("pos_rate_vs_all") is not None else None,
                     "base_median": round(rec["base_median"], 1) if rec.get("base_median") is not None else None,
                     "status_vs_all": rec.get("vs_all_status"),
                     "vs_all": ({"day_mean": round(rec["vs_all_day_mean"], 1), "status": rec["vs_all_status"]}
                                if rec.get("vs_all_day_mean") is not None else None),
                     "regime": ({"label": STAGE_JA[stage_today], "days": reg["days"], "median": round(reg["median"], 1),
                                 "pos_rate": round(reg["pos_rate"])}
                                if reg and reg["status"] != "insufficient" else None)})
    # 並び: 種類を発生日数の多い順に並べ、各種類から1件ずつ順に取る（同じ種類の中はコード順）。上限 MAX_SIGNALS。
    # 1つの種類（出来高の急増は1日に100件近い）で30件が埋まらないようにするため。値の大きさでは選ばない
    by_t = defaultdict(list)
    for x in sorted(sigs, key=lambda s: s["code"]):
        by_t[x["type"]].append(x)
    order = sorted(by_t, key=lambda t: (-by_t[t][0]["days"], t))
    picked = []
    while len(picked) < MAX_SIGNALS and any(by_t[t] for t in order):
        for t in order:
            if by_t[t] and len(picked) < MAX_SIGNALS:
                picked.append(by_t[t].pop(0))
    n_all = len(sigs)
    sigs = picked
    live_scored = sum(1 for (eid, h), s in scores.items() if h == WINDOW and (events.get(eid) or {}).get("origin") == "live"
                      and not events[eid].get("imported_from") and not events[eid]["event_type"].startswith("signal_"))
    write_json(docs_out / "today.json", {
        "v": 1, "updated": now, "date": day,
        "regime": STAGE_JA.get(stage_today) if stage_today else None,
        "counts": [{"type": t, "label": label_of(t), "n": n} for t, n in sorted(counts.items(), key=lambda x: (-x[1], x[0]))],
        "signals": sigs,
        "rule": f"その日の出来事のうち、{WINDOW}営業日の統計が「対TOPIX」と「同じ日の全銘柄との差」の両方で confirmed（日付単位の検定）"
                f"かつ同じ向きで、発生日数{SIGNAL_MIN_DAYS}日以上のもの。"
                f"並びは、種類を発生日数の多い順に並べて各種類から1件ずつ順に（同じ種類の中はコード順）・上限{MAX_SIGNALS}件",
        "n_matched": n_all,
        "note": f"{note} 当日記録（2026-11-18〜）で{WINDOW}営業日の答えが出た出来事はまだ{live_scored}件。"})

    # ── 出した予兆を残す（追記のみ・event_id で重複しない）
    sig_dir = out / "signals"
    sig_dir.mkdir(parents=True, exist_ok=True)
    logged = {}
    for f in sorted(sig_dir.glob("*.jsonl")):
        for r in L.jsonl(f):
            logged[r["event_id"]] = r
    new = []
    for s in sigs:
        if s["event_id"] in logged:
            continue
        r = {"event_id": s["event_id"], "date": day, "published_at": now, **{k: v for k, v in s.items() if k != "event_id"}}
        new.append(r)
        logged[s["event_id"]] = r
    if new:
        with open(sig_dir / f"{day[:7]}.jsonl", "a", encoding="utf-8", newline="\n") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    # ── 的中記録（出したものすべて＋答え。外れも消さない）
    rec_rows = []
    for eid, r in sorted(logged.items(), key=lambda x: (x[1]["date"], x[1].get("type"), x[1].get("code"))):
        e = events.get(eid) or {}
        res = {}
        for h in H:
            s = scores.get((eid, h))
            if s and s.get("status") != "invalid":
                v = metric_of(e or {"code": r.get("code") or "MKT"}, s)
                res[str(h)] = {"ret": round(v, 2) if v is not None else None, "end_date": s.get("end_date")}
        main_r = res.get(str(r.get("window", WINDOW)))
        rec_rows.append({"event_id": eid, "date": r["date"], "code": r.get("code"), "name": r.get("name"),
                         "type": r.get("type"), "label": r.get("label"), "text": r.get("text"),
                         "median": r.get("median"), "window": r.get("window"), "result": res,
                         "same_dir": (None if not main_r or main_r["ret"] is None or not r.get("median")
                                      else sign(main_r["ret"]) == sign(r["median"]))})
    answered = [x for x in rec_rows if x["same_dir"] is not None]
    rec_obj = {"v": 1, "updated": now, "n": len(rec_rows), "answered": len(answered),
               "same_dir": sum(1 for x in answered if x["same_dir"]),
               "note": "出した予兆をすべて残している（外れも消さない）。same_dir は、窓の満了時の値が出したときの中央値と同じ向きだったか。",
               "items": rec_rows}
    body = json.dumps(rec_obj, ensure_ascii=False, separators=(",", ":"))
    if len(body.encode("utf-8")) > RECORD_SPLIT_BYTES:
        months = defaultdict(list)
        for x in rec_rows:
            months[x["date"][:7]].append(x)
        for ym, xs in months.items():
            write_json(docs_out / "record" / f"{ym}.json", {"v": 1, "updated": now, "month": ym, "items": xs})
        rec_obj = {k: v for k, v in rec_obj.items() if k != "items"}
        rec_obj["months"] = sorted(months)
        rec_obj["items"] = [x for x in rec_rows if x["date"][:7] == max(months)]
    write_json(docs_out / "record.json", rec_obj)
    print(f"今日（{day}）: 出来事の種類 {len(counts)}・予兆 {len(sigs)}件（新しく記録 {len(new)}件）・的中記録 {len(rec_rows)}件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
