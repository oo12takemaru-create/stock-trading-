# -*- coding: utf-8 -*-
"""今日のチャート当て: 出題の「在庫」を5年の日足から1回まとめて作る（2026-11-20）

■ 何をするか
  全銘柄の日足5年（yochou_px5y.py が作る .cache/px5y/chunk_*.csv.gz）から、
  「直前120営業日の日足（問題）＋その後20営業日の終値（答え）」の窓を切り出し、
  条件に合うものを上と下が半々になるよう選んで data/quiz/pool.jsonl.gz に保存する。
  毎日のジョブ（quiz_daily.py）は、この在庫から日付で決まる乱数で5問を選ぶだけ（5年分を毎日取り直さない）。

■ 1つの窓（銘柄ごとの日足の並びで連続した140本）
  - 1〜120本目 = 問題のチャート（始値・高値・安値・終値・出来高）
  - 121〜140本目 = 答え（終値）。騰落率 = 140本目の終値 ÷ 120本目の終値 − 1
  - 価格は1本目の終値を100とした指数、出来高は120本の最大を100とした数にする（水準を隠す）

■ 除くもの
  - 答えの最後の日が、データの最後の日から数えて直近250営業日に入る窓（今の銘柄への見立てに見えないように）
  - 20営業日後の騰落率が ±2% 以内（どっちつかず）
  - 売買がほとんど無い: 140本の中に出来高0の日がある・120本の売買代金の中央値が1億円未満・株価100円未満
  - 値が飛んでいる: 1日で ±35% を超える終値・始値の変化（配信の誤り・分割の取り違え）、四本値の矛盾、
    高値＝安値（値が付いて動かない）の日が2日続く・140本で3日以上（ストップ高安の張り付き）
  - 取引所の営業日の並びで欠けが3日以上ある窓（売買の無い日が続いた）
  - 業種の分からない銘柄（ETF・REIT など。tenbagger_universe.csv に業種のある内国株だけ）

■ 選び方
  銘柄ごとに、窓の開始を10本おきにずらして候補を集め（開始のずれは銘柄ごとに固定の乱数）、
  固定の種で上と下を同じ数だけ選ぶ（同じ銘柄は最大 PER_CODE 個・重ならない窓だけ）。
  id は「銘柄コードと1本目の日付」から作るので、作り直しても同じ窓は同じ id（使った問題の記録がずれない）。

■ 値の意味（メタ情報にも書く）
  取得日の時点で Yahoo が見せている値（分割は遡って調整済み・配当は未調整）。
  上場廃止・統合で今いない銘柄は入らない（生き残りの偏り）。

使い方:
  python -X utf8 quiz_pool.py --px5y .cache/px5y [--out data/quiz/pool.jsonl.gz] [--per-side 1800]
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import random
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "quiz" / "pool.jsonl.gz"
UNIVERSE = ROOT / "tenbagger_universe.csv"
JST = timezone(timedelta(hours=9))

BARS = 120          # 問題の本数
AHEAD = 20          # 答えの本数
WIN = BARS + AHEAD
RECENT = 250        # 直近250営業日の窓は出さない
MIN_MOVE = 2.0      # ±2% 以内は除く
MIN_VALUE = 1e8     # 売買代金の中央値（円）
MIN_PRICE = 100.0
MAX_JUMP = 0.35     # 1日の変化の上限
MAX_MISS = 2        # 営業日の欠け（窓の中）の上限
STRIDE = 10
PER_CODE = 3
PER_SIDE = 1800
SEED = "kaburadar-quiz-pool-v1"
CAL_CODE = "1306"   # 営業日の並びは TOPIX 連動ETF の日付で作る


def log(*a):
    print(*a, flush=True)


def qid(code, d0):
    return "q" + hashlib.sha1(f"{code}|{d0}".encode("utf-8")).hexdigest()[:10]


def r1(x):
    return round(float(x), 1)


def load_universe(path=UNIVERSE):
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            c = (r.get("code") or "").strip()
            s = (r.get("sector33") or "").strip()
            if not c or not s or s == "-":
                continue
            out[c] = {"name": unicodedata.normalize("NFKC", (r.get("name") or "").strip()), "sector": s}
    return out


def load_px5y(px_dir):
    """chunk_*.csv.gz → {code: [(date, o, h, l, c, v), ...]}（日付順）"""
    data = {}
    files = sorted(Path(px_dir).glob("chunk_*.csv.gz"))
    if not files:
        raise SystemExit(f"5年の日足が見つからない: {px_dir}")
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    row = (r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                           float(r["close"]), float(r["volume"] or 0))
                except (TypeError, ValueError):
                    continue
                data.setdefault(r["code"], []).append(row)
    for c in data:
        data[c].sort(key=lambda x: x[0])
        # 同じ日付が2行あれば後ろを捨てる
        ded, seen = [], set()
        for x in data[c]:
            if x[0] not in seen:
                seen.add(x[0])
                ded.append(x)
        data[c] = ded
    return data


def calendar_of(data):
    cal = [x[0] for x in data.get(CAL_CODE, [])]
    if len(cal) < 500:
        # 物差しが無いときは全銘柄の日付の和（件数の多い日だけ）
        cnt = {}
        for rows in data.values():
            for x in rows:
                cnt[x[0]] = cnt.get(x[0], 0) + 1
        top = max(cnt.values())
        cal = sorted(d for d, n in cnt.items() if n >= top * 0.5)
    return cal


def check_window(rows, s, cal_ix):
    """rows[s:s+WIN] が出題に使えるか。使えれば騰落率(%)、使えなければ理由の文字列を返す"""
    w = rows[s:s + WIN]
    if len(w) < WIN:
        return "short"
    a, b = cal_ix.get(w[0][0]), cal_ix.get(w[-1][0])
    if a is None or b is None or (b - a) - (WIN - 1) > MAX_MISS:
        return "gap"
    flat_run = flat_n = 0
    prev_c = None
    for (_, o, h, l, c, v) in w:
        if min(o, h, l, c) <= 0 or v <= 0:
            return "zero"
        if h < max(o, c) - 1e-6 or l > min(o, c) + 1e-6 or h < l:
            return "ohlc"
        if prev_c is not None:
            if abs(c / prev_c - 1) > MAX_JUMP or abs(o / prev_c - 1) > MAX_JUMP:
                return "jump"
        if h == l:
            flat_n += 1
            flat_run += 1
            if flat_run >= 2 or flat_n >= 3:
                return "flat"
        else:
            flat_run = 0
        prev_c = c
    q = w[:BARS]
    vals = sorted(x[4] * x[5] for x in q)
    if vals[len(vals) // 2] < MIN_VALUE:
        return "thin"
    if q[-1][4] < MIN_PRICE:
        return "price"
    ret = (w[-1][4] / q[-1][4] - 1) * 100
    if abs(ret) <= MIN_MOVE:
        return "flatret"
    return ret


def make_item(code, rows, s, ret, meta):
    """問題の側（指数化・日付なし）と答えの側（銘柄・期間・騰落率）を1行にまとめる"""
    w = rows[s:s + WIN]
    base = w[0][4]
    q = w[:BARS]
    vmax = max(x[5] for x in q) or 1
    return {
        "id": qid(code, w[0][0]),
        "code": code,
        "name": meta["name"],
        "sector": meta["sector"],
        "d0": w[0][0],                 # 1本目
        "dq": q[-1][0],                # 120本目（見立ての日）
        "d1": w[-1][0],                # 140本目（答えの日）
        "o": [r1(x[1] / base * 100) for x in q],
        "h": [r1(x[2] / base * 100) for x in q],
        "l": [r1(x[3] / base * 100) for x in q],
        "c": [r1(x[4] / base * 100) for x in q],
        "v": [int(round(x[5] / vmax * 100)) for x in q],
        "after": [r1(x[4] / base * 100) for x in w[BARS:]],
        "ret": round(ret, 2),
        "dir": "up" if ret > 0 else "down",
    }


def build(data, uni, per_side=PER_SIDE, per_code=PER_CODE, seed=SEED):
    cal = calendar_of(data)
    cal_ix = {d: i for i, d in enumerate(cal)}
    cutoff = cal[-RECENT] if len(cal) > RECENT else cal[0]   # これ以降に答えの日が来る窓は出さない
    stats = {}
    cand = {"up": [], "down": []}
    for code in sorted(data):
        meta = uni.get(code)
        if not meta:
            stats["no_sector"] = stats.get("no_sector", 0) + 1
            continue
        rows = data[code]
        off = int(hashlib.sha1(f"{seed}|{code}".encode()).hexdigest(), 16) % STRIDE
        for s in range(off, len(rows) - WIN + 1, STRIDE):
            if rows[s + WIN - 1][0] >= cutoff:
                stats["recent"] = stats.get("recent", 0) + 1
                break
            r = check_window(rows, s, cal_ix)
            if isinstance(r, str):
                stats[r] = stats.get(r, 0) + 1
                continue
            cand["up" if r > 0 else "down"].append((code, s, r))
    stats["cand_up"], stats["cand_down"] = len(cand["up"]), len(cand["down"])
    rng = random.Random(seed)
    picked, per = [], {}
    taken = {}   # code -> [s, ...]（同じ銘柄の窓は重ねない）
    for side in ("up", "down"):
        pool = sorted(cand[side], key=lambda x: (x[0], x[1]))
        rng.shuffle(pool)
        n = 0
        for code, s, r in pool:
            if n >= per_side:
                break
            if per.get(code, 0) >= per_code:
                continue
            if any(abs(s - t) < WIN for t in taken.get(code, [])):
                continue
            picked.append(make_item(code, data[code], s, r, uni[code]))
            per[code] = per.get(code, 0) + 1
            taken.setdefault(code, []).append(s)
            n += 1
    picked.sort(key=lambda x: x["id"])
    info = {"cutoff": cutoff, "data_end": cal[-1], "data_start": cal[0], "stats": stats,
            "n": len(picked), "n_up": sum(1 for x in picked if x["dir"] == "up"),
            "n_down": sum(1 for x in picked if x["dir"] == "down"),
            "codes": len(per)}
    return picked, info


def write_pool(items, info, out=OUT, adjusted_asof=None):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    head = {"_meta": {
        "schema": "kaburadar_quiz_pool/1",
        "built": datetime.now(JST).date().isoformat(),
        "adjusted_asof": adjusted_asof,
        "bars": BARS, "ahead": AHEAD, "recent_excluded": RECENT, "min_move": MIN_MOVE,
        "source": "Yahoo（yfinance・auto_adjust=False。分割は遡って調整済み・配当は未調整）",
        "survivorship": "上場廃止・統合で今いない銘柄は入っていない（今も上場している銘柄だけ）",
        **info}}
    buf.write(json.dumps(head, ensure_ascii=False, separators=(",", ":")) + "\n")
    for it in items:
        buf.write(json.dumps(it, ensure_ascii=False, separators=(",", ":")) + "\n")
    with open(out, "wb") as f:
        with gzip.GzipFile(filename="", mode="wb", fileobj=f, mtime=0, compresslevel=9) as gz:
            gz.write(buf.getvalue().encode("utf-8"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--px5y", default=str(ROOT / ".cache" / "px5y"))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--per-side", type=int, default=PER_SIDE)
    a = ap.parse_args()
    uni = load_universe()
    log(f"業種のある銘柄 {len(uni)}")
    data = load_px5y(a.px5y)
    log(f"日足 {len(data)}銘柄")
    asof = None
    mp = Path(a.px5y) / "meta.json"
    if mp.exists():
        try:
            asof = json.loads(mp.read_text(encoding="utf-8")).get("adjusted_asof")
        except Exception:
            pass
    items, info = build(data, uni, per_side=a.per_side)
    p = write_pool(items, info, a.out, adjusted_asof=asof)
    log(f"在庫 {info['n']}問（上 {info['n_up']}・下 {info['n_down']}・{info['codes']}銘柄）"
        f" 期間 {info['data_start']}〜{info['data_end']}・答えの日は {info['cutoff']} より前")
    log(f"除いた窓: {info['stats']}")
    log(f"→ {p}（{p.stat().st_size / 1e6:.2f}MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
