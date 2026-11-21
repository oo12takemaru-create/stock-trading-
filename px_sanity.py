# -*- coding: utf-8 -*-
"""終値の並びの検査（2026-11-21・株価の桁がおかしい銘柄の守り）

■ 何のため
  yfinance の終値（分割調整済み）には、ときどき画面を壊す並びが混じる。2026-11-21 に公開中の
  docs/px1y/ 全3,695銘柄を調べて見つけた形:
    - 株式併合（MBO のスクイーズアウト）を「分割」として取り込み、それより前の終値がすべて何十万倍
      （7523 アールビバン。2026-11-11 に比率 1/618,000 の記録。1,884円 → 1,164,312,064円）
    - 再上場より前の値が桁違いのまま残る（8303 SBI新生銀行。2025-12-18 の再上場より前が 55,319,998,464円）
    - 分割の調整が1日ずれて、権利落ちの前日に1/3の崖ができる（8227 しまむら。2026-02-18）
    - 上場前の日に同じ値が並ぶ（5537。2025-12-15 より前が 264.8円のまま → 1,755円）
  そういう並びを画面（チャート・高安）に出さないための、共通で使える検査と作り直し。
  まず myboard_px.py（docs/px1y/）の書き出しで使う。他の読み手（daily_bars・similar_weekly・quiz_pool 等）は今は使っていない。

■ 異常とみなす規則（数字は下の定数）
  1. 値が 0 以下 か MAX_PRICE（1億円）超。東証でいちばん高い株・REIT でも 1口 数百万円まで
  2. 1日で JUMP（2.5倍）以上・1/2.5 以下の飛び。ただし安い方の値が JUMP_MIN_PRICE（50円）未満の飛びは見ない。
     東証の値幅制限は 200円未満で50円・100円未満で30円（連続ストップ高で広がっても2倍）なので、
     50円以上の株が1日で2.5倍・0.4倍になることは、分割の取り込み漏れ・桁の誤り・上場前の仮の値でしか起きない。
     2026-11-21 の公開データで、この3つを除いた最大の1日の動きは 1.98倍（6072）。
     10倍・1/10（BIG_JUMP）は、高い方が50円以上なら安い方が50円未満でも見る（1306 の 383円→38円 のような桁の誤り）。
  3. 参考の終値（prices.json の同じ日の終値）があり、最後の終値と REF_TOL（3倍）以上違う

■ 作り直し（上から順に試し、作り直した並びがすべての規則を通ったものを使う）
  a. 極端な分割の記録（比率が 1/EXTREME_SPLIT 未満か EXTREME_SPLIT 倍超）＝株式併合によるスクイーズアウトの類
     （ふつうの分割・併合は 1:100〜10:1 程度）: その日より前の終値に比率を掛けて調整前の値に戻し
     （yfinance の Close は「前の値 ÷ 比率」で調整されている）、その日以後（廃止後の売買のない値）は捨てる
  b. 分割の調整漏れ: 飛びの日の前後 SPLIT_NEAR（3）日以内に分割の記録があり、飛びの倍率×比率が
     1 から SPLIT_TOL（±20%）以内（2→1/2 のように打ち消し合う）なら、飛びより前の終値を比率で割る
  c. 先頭だけ壊れている: 捨てる先頭に 0以下・1億円超の値があるか、先頭が2種類以下の同じ値の並び
     （上場前の仮の値）で、残りの後ろの区間が規則をすべて通り MIN_KEEP（20営業日）以上あるとき、後ろだけ使う
  ふつうの分割で、Yahoo の調整が合っている銘柄（飛びが無い）はそもそも引っかからないので触らない。

■ どれでも直らなければ使わない（書かない）。画面は「チャートなし」に落ちる。
"""
from datetime import date, timedelta

MAX_PRICE = 1e8          # 1億円
JUMP = 2.5               # 1日で2.5倍・0.4倍
JUMP_MIN_PRICE = 50.0    # 安い方がこれ未満の飛びは見ない（ただし BIG_JUMP は高い方がこれ以上なら見る）
BIG_JUMP = 10.0          # 10倍・1/10 は低位株でも起きない（100円未満の値幅は30円）
REF_TOL = 3.0            # 参考の終値との比
EXTREME_SPLIT = 1000.0   # 分割の比率がこれより極端なら株式併合（スクイーズアウト）の類
SPLIT_NEAR = 3           # 飛びの日と分割の記録の日のずれ（暦日）
SPLIT_TOL = 0.2          # 飛びの倍率×分割の比率 が 1±20% なら調整漏れ
MIN_KEEP = 20            # 先頭を切ったあとに残す最低の日数
BROKEN = ("huge", "nonpos", "nan")   # それだけで壊れていると言える種類


def problems(pts, ref=None):
    """[(date, close)] → 見つけた異常の一覧 [(種類, 日付, 説明)]。空なら問題なし。
    ref: 最後の日の参考の終値（無ければ None）"""
    out = []
    prev = None
    for d, c in pts:
        try:
            c = float(c)
        except (TypeError, ValueError):
            out.append(("nan", d, "数でない"))
            prev = None
            continue
        if not (c > 0) or c != c:
            out.append(("nonpos", d, f"{c}"))
            prev = None
            continue
        if c > MAX_PRICE:
            out.append(("huge", d, f"{c:,.0f}"))
        if prev is not None:
            r = c / prev
            lo, hi = min(prev, c), max(prev, c)
            small = lo >= JUMP_MIN_PRICE and (r >= JUMP or r <= 1 / JUMP)
            big = hi >= JUMP_MIN_PRICE and (r >= BIG_JUMP or r <= 1 / BIG_JUMP)
            if small or big:
                out.append(("jump", d, f"{prev:,.1f}→{c:,.1f}（{r:.3g}倍）"))
        prev = c
    if ref and pts:
        try:
            r = float(pts[-1][1]) / float(ref)
            if r >= REF_TOL or r <= 1 / REF_TOL:
                out.append(("ref", pts[-1][0], f"最後の終値 {float(pts[-1][1]):,.1f} と参考 {float(ref):,.1f}（{r:.3g}倍）"))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def _splits(splits):
    out = []
    for d, r in (splits or {}).items():
        try:
            r = float(r)
            d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        except (TypeError, ValueError):
            continue
        if r > 0 and r != 1:
            out.append((d, r))
    return sorted(out)


def extreme_splits(splits):
    """{date: 比率} → 極端な比率のものだけ [(date, 比率)]（日付順）"""
    return [(d, r) for d, r in _splits(splits) if r < 1 / EXTREME_SPLIT or r > EXTREME_SPLIT]


def repair_extreme(pts, splits):
    """a. 極端な分割の記録で調整前に戻し、その日以後を捨てる。できなければ None"""
    ex = extreme_splits(splits)
    if not ex:
        return None
    first = ex[0][0]
    out = []
    for d, c in pts:
        if d >= first:
            break
        f = float(c)
        for sd, r in ex:
            if d < sd:
                f *= r
        out.append((d, f))
    return out if len(out) >= 2 else None


def repair_unadjusted(pts, splits):
    """b. 分割の調整漏れの飛びを、記録の比率で前の値を割って直す。直す飛びが無ければ None"""
    sp = _splits(splits)
    if not sp:
        return None
    vals = [float(c) for _, c in pts]
    done = False
    for i in range(1, len(pts)):
        a, b = vals[i - 1], vals[i]
        if not (a > 0 and b > 0):
            continue
        r = b / a
        if r < JUMP and r > 1 / JUMP:
            continue
        for sd, s in sp:
            if abs((pts[i][0] - sd).days) <= SPLIT_NEAR and abs(r * s - 1) <= SPLIT_TOL:
                for k in range(i):
                    vals[k] /= s
                done = True
                break
    if not done:
        return None
    return [(d, v) for (d, _), v in zip(pts, vals)]


def trim_head(pts, ref=None):
    """c. 先頭だけが壊れている並びの、後ろの正常な区間。切れなければ None"""
    k = len(pts)
    while k > 0 and not problems(pts[k - 1:], ref):
        k -= 1
    if k >= len(pts):
        return None
    tail, head = pts[k:], pts[:k]
    if len(tail) < MIN_KEEP:
        return None
    broken = any(x[0] in BROKEN for x in problems(head))
    placeholder = len({float(c) for _, c in head}) <= 2 and len(head) >= 3
    if not (broken or placeholder):
        return None
    return tail


def check(pts, splits=None, ref=None):
    """検査して、使える並びを返す。
    戻り値 (状態, 並び, 問題)。状態は "ok"（そのまま）/"fixed"（極端な分割の記録で作り直した）/
    "adjusted"（分割の調整漏れを直した）/"trimmed"（壊れた先頭を捨てた）/"bad"（使わない）。
    splits: {date or "YYYY-MM-DD": 比率}。ref は最後の日の参考値（a で最後の日がずれた並びには当てない）。"""
    p = problems(pts, ref)
    if not p:
        return "ok", pts, []
    fixed = repair_extreme(pts, splits)
    if fixed is not None and not problems(fixed):
        return "fixed", fixed, p
    adj = repair_unadjusted(pts, splits)
    if adj is not None and not problems(adj, ref):
        return "adjusted", adj, p
    tail = trim_head(pts, ref)
    if tail is not None:
        return "trimmed", tail, p
    return "bad", None, p
