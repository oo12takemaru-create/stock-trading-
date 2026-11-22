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
SPIKE_MAX_LEN = 3        # d. 単発の桁とび: 外れている日数の上限
SPIKE_NEAR = 0.10        # d. 戻った先が、外れる前の値のこの幅の中
FLAT_MIN = 60            # e. 終値が1円も動かない日がこれだけ続いたら、値の付いていない区間とみなす


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


def drop_spikes(pts):
    """d. 単発の桁とび（2026-11-21 追加・長い並び用）: 規則2の飛びで外れて、SPIKE_MAX_LEN（3）日以内に
    外れる前の値の ±SPIKE_NEAR（10%）へ戻る点を捨てる（1306 の 2026-03-30・31 が 1/10 のような配信の誤り）。
    戻り値 (並び, 捨てた日付の一覧)。外れた点が戻らないもの（本当の崖・調整漏れ）は触らない"""
    vals = []
    for _, c in pts:
        try:
            vals.append(float(c))
        except (TypeError, ValueError):
            vals.append(float("nan"))
    drop = set()
    i = 1
    while i < len(pts):
        a = vals[i - 1]
        if not (a > 0) or i - 1 in drop:
            i += 1
            continue
        if not _is_jump(a, vals[i]):
            i += 1
            continue
        hit = 0
        for k in range(1, SPIKE_MAX_LEN + 1):
            if i + k >= len(pts):
                break
            b = vals[i + k]
            if b > 0 and abs(b / a - 1) <= SPIKE_NEAR and all(_is_jump(a, vals[i + m]) for m in range(k)):
                hit = k
                break
        if hit:
            drop.update(range(i, i + hit))
            i += hit
        i += 1
    if not drop:
        return pts, []
    return [p for n, p in enumerate(pts) if n not in drop], [pts[n][0] for n in sorted(drop)]


def _is_jump(a, b):
    """規則2（problems と同じ線）で a→b が飛びか。数でない・0以下も飛び扱い"""
    if not (b > 0) or b != b:
        return True
    r = b / a
    lo, hi = min(a, b), max(a, b)
    return (lo >= JUMP_MIN_PRICE and (r >= JUMP or r <= 1 / JUMP)) or \
           (hi >= JUMP_MIN_PRICE and (r >= BIG_JUMP or r <= 1 / BIG_JUMP))


def flat_runs(pts):
    """e. 値の付いていない区間（2026-11-21 追加・長い並び用）: 終値が FLAT_MIN（60）営業日以上 1円も動かない区間
    [(始まりの番号, 終わりの番号＋1)]。5年の日足では、上場・再上場の前に Yahoo が仮の値を並べた区間
    （5858・6228・4197・5537・7075・9225・9204。どれも並びの先頭）と、1年近く売買の無かった区間（4365）だけ。
    売買の少ない銘柄でも、ふつうは長くて 1〜2か月（最長 33営業日）で値が動く"""
    out, start = [], 0
    for i in range(1, len(pts) + 1):
        if i == len(pts) or float(pts[i][1]) != float(pts[i - 1][1]):
            if i - start >= FLAT_MIN:
                out.append((start, i))
            start = i
    return out


def clean(pts, splits=None):
    """長い並び（予兆レーダーの5年の日足など）用の検査と作り直し（2026-11-21）。check() は変えずに、その前後に足す:
      1. e（値の付いていない区間）を捨てる
      2. d（単発の桁とび）を捨てる
      3. check()（a 極端な分割の記録 → b 分割の調整漏れ → c 壊れた先頭を捨てる）
      4. c で先頭を切るか、どれでも直らないときは f（0以下・1億円超の点の塊だけを捨てる）を試し、
         残りが規則をすべて通り、c より多くの日が残るならそちらを使う（8303 の再上場前の値の塊。旧上場の期間は残す）
      5. それでも直らなければ b のあとに c（調整漏れと上場前の仮の値が両方ある並び）
    戻り値 (状態, 並び, 問題, 捨てた日付)。状態は使った手順を "+" でつないだもの（例 "flat+trimmed"）。
    "bad" は使わない（捨てた日付は全部）。問題には e の区間も ("flat", 始まりの日, 説明) で入る"""
    fr = flat_runs(pts)
    p0 = [("flat", pts[s][0], f"{pts[s][0]}〜{pts[e - 1][0]} の {e - s}日 {float(pts[s][1]):,.1f} のまま") for s, e in fr]
    p0 += problems(pts)
    if not p0:
        return "ok", pts, [], []
    steps, dropped = [], []
    cur = pts
    if fr:
        gone = {n for s, e in fr for n in range(s, e)}
        dropped += [pts[n][0] for n in sorted(gone)]
        cur = [p for n, p in enumerate(pts) if n not in gone]
        steps.append("flat")
    cur, sd = drop_spikes(cur)
    if sd:
        dropped += sd
        steps.append("spikes")
    if len(cur) < 2:
        return "bad", None, p0, [d for d, _ in pts]
    if not problems(cur):
        return "+".join(steps), cur, p0, sorted(dropped)
    st, new, _ = check(cur, splits)
    if st in ("trimmed", "bad"):
        nb = [(d, c) for d, c in cur if 0 < float(c) <= MAX_PRICE]
        if len(nb) < len(cur) and len(nb) >= MIN_KEEP and not problems(nb) and (new is None or len(nb) > len(new)):
            st, new = "blocks", nb
    if st == "bad":
        adj = repair_unadjusted(cur, splits)
        tail = trim_head(adj) if adj is not None else None
        if tail is not None:
            st, new = "adjusted+trimmed", tail
    if st == "bad":
        return "bad", None, p0, [d for d, _ in pts]
    steps.append(st)
    keep = {d for d, _ in new}
    dropped = sorted(set(dropped) | {d for d, _ in cur if d not in keep})
    return "+".join(steps), new, p0, dropped


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


def clean_columns(dates, C, codes, splits=None):
    """行列（日付×銘柄の終値）の銘柄ごとに clean() を当てる（similar_weekly などの読み手用・2026-11-21）。
    dates: ["YYYY-MM-DD"]、C: numpy の2次元（NaN は足が無い）、splits: {code: {ex: 比率}}。
    戻り値 {列の番号: (状態, 係数, 問題)}。引っかかった列だけ。係数は日付ごとの「掛ける数」（1.0＝そのまま・NaN＝捨てる）"""
    import numpy as np
    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        for j in range(C.shape[1]):
            idx = np.flatnonzero(np.isfinite(C[:, j]))
            if len(idx) < 2:
                continue
            c = C[idx, j]
            r = c[1:] / c[:-1]
            lo, hi = np.minimum(c[1:], c[:-1]), np.maximum(c[1:], c[:-1])
            jump = ((lo >= JUMP_MIN_PRICE) & ((r >= JUMP) | (r <= 1 / JUMP))) | \
                   ((hi >= JUMP_MIN_PRICE) & ((r >= BIG_JUMP) | (r <= 1 / BIG_JUMP)))
            br = np.flatnonzero(np.r_[True, c[1:] != c[:-1], True])
            flat = int(np.diff(br).max()) >= FLAT_MIN
            if not (jump.any() or flat or (c > MAX_PRICE).any() or (c <= 0).any()):
                continue
            pts = [(date.fromisoformat(dates[i]), float(C[i, j])) for i in idx]
            st, new, prob, _ = clean(pts, (splits or {}).get(codes[j]))
            fac = np.full(C.shape[0], np.nan)
            fac[~np.isfinite(C[:, j])] = 1.0
            keep = {d: v for d, v in (new or [])}
            for i in idx:
                v = keep.get(date.fromisoformat(dates[i]))
                if v is not None:
                    fac[i] = v / C[i, j]
            out[j] = (st, fac, prob)
    return out
