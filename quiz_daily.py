# -*- coding: utf-8 -*-
"""今日のチャート当て: その日の5問を在庫から選んで docs/quiz/ に書く（毎朝 6:00 JST・2026-11-20）

■ 出すもの（GitHub Pages で公開。サイトの quiz.html が直接読む）
  docs/quiz/YYYY-MM-DD.json … その日の5問。問題の側（questions）と答えの側（answers）を別のキーに分ける。
                              問題の側は指数化した四本値と出来高だけ（銘柄名・コード・日付・業種は答えの側だけ）。
  docs/quiz/latest.json      … 今日の版への案内と、練習用の過去の日付（新しい順・最大60日）

■ 選び方（全員同じ・作り直しても同じ）
  - 乱数の種 = その日の日付。在庫（data/quiz/pool.jsonl.gz）の並び（id 順）も固定なので、同じ日は同じ5問
  - 上の数は 2 か 3（種で決める）→ 長い目で半々。残りは下
  - 答えの日（140本目）がその日から366日より前のものだけ（在庫を作った後に日がたっても直近1年は出ない）
  - その日より前に出した問題（docs/quiz/ の過去の日の分）は出さない。在庫が足りなくなったら古い順に再び使う
  - 5問はすべて別の銘柄。前の日に出た銘柄も出さない
  - その日のファイルがもうあれば書き換えない（2回走っても同じ。--force で作り直しても前の日が同じなら同じ中身）
  - 在庫で "drop":1 の印の問題（75日線を1本目から引けない）は選ばずに飛ばす（並びは変えないので、ほかの日の5問は変わらない）

■ 移動平均線（2026-11-21 追加）
  questions[] に ma25・ma75（各120点）、answers[] にその後20本ぶんの ma25・ma75 を入れる（在庫の140点を分けるだけ）。
  もうある日付のファイルに線が無ければ、在庫の同じ id の問題から線だけ足す（四本値・答えが在庫と一致するときだけ。
  ほかの値は1つも変えない）。マージ前に出た日・在庫を作り直す前の日のファイルも、次に走ったときに線が付く。

使い方:
  python -X utf8 quiz_daily.py [--date YYYY-MM-DD] [--pool data/quiz/pool.jsonl.gz] [--out docs/quiz] [--force]
"""
import argparse
import gzip
import hashlib
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POOL = ROOT / "data" / "quiz" / "pool.jsonl.gz"
OUT = ROOT / "docs" / "quiz"
JST = timezone(timedelta(hours=9))
N = 5
KEEP_DATES = 60
SEED = "kaburadar-quiz-daily-v1"
RECENT_DAYS = 366
REUSE_AFTER_DAYS = 540   # 在庫が足りないとき、これより前に出した問題は再び使ってよい

NOTE = ("過去5年の日足から、今も上場している銘柄だけで作っています（上場廃止・統合で今いない銘柄は入っていません）。"
        "直近1年のチャートは出しません。株価は1本目の終値を100とした指数、出来高は最大を100にしています。"
        "株価は Yahoo の値（分割は調整済み）。売買の参考にするものではなく、遊びです。")


def log(*a):
    print(*a, flush=True)


def load_pool(path=POOL):
    meta, items = {}, []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            x = json.loads(line)
            if "_meta" in x:
                meta = x["_meta"]
            else:
                items.append(x)
    items.sort(key=lambda x: x["id"])
    return meta, items


def past_files(out_dir, before):
    """before より前の日付の出題ファイル {date: data}（新しい順で使う）"""
    res = {}
    for p in Path(out_dir).glob("????-??-??.json"):
        d = p.stem
        if d >= before:
            continue
        try:
            res[d] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return res


def period_ja(d0, d1):
    a, b = d0[:7].split("-"), d1[:7].split("-")
    if a[0] == b[0]:
        return f"{int(a[0])}年{int(a[1])}月〜{int(b[1])}月"
    return f"{int(a[0])}年{int(a[1])}月〜{int(b[0])}年{int(b[1])}月"


def pick(items, day, past):
    """その日の5問を選ぶ（items は id 順・past は day より前の出題）"""
    dd = date.fromisoformat(day)
    limit = (dd - timedelta(days=RECENT_DAYS)).isoformat()
    used_at = {}
    for d, data in past.items():
        for a in data.get("answers", []):
            if a.get("id"):
                used_at[a["id"]] = max(used_at.get(a["id"], ""), d)
    prev_day = (dd - timedelta(days=1)).isoformat()
    prev_codes = {a.get("code") for a in past.get(prev_day, {}).get("answers", [])}
    ok = [x for x in items if x["d1"] < limit and x["code"] not in prev_codes]
    fresh = [x for x in ok if x["id"] not in used_at]
    reuse_before = (dd - timedelta(days=REUSE_AFTER_DAYS)).isoformat()
    rng = random.Random(hashlib.sha256(f"{SEED}|{day}".encode()).hexdigest())
    n_up = rng.choice([2, 3])
    want = {"up": n_up, "down": N - n_up}
    chosen, codes = [], set()
    for side in ("up", "down"):
        cand = [x for x in fresh if x["dir"] == side]
        if len(cand) < want[side] * 4:
            # 在庫が細ったら、ずっと前に出した問題も使う（古い順に）
            old = sorted((x for x in ok if x["dir"] == side and x["id"] in used_at
                          and used_at[x["id"]] < reuse_before), key=lambda x: (used_at[x["id"]], x["id"]))
            cand = cand + old
        order = list(range(len(cand)))
        rng.shuffle(order)
        n = 0
        for i in order:
            x = cand[i]
            if n >= want[side]:
                break
            if x.get("drop"):          # 在庫から外した問題の印（並びを保つために残してある）
                continue
            if x["code"] in codes:
                continue
            chosen.append(x)
            codes.add(x["code"])
            n += 1
    rng.shuffle(chosen)
    return chosen


MA_KEYS = ("ma25", "ma75")


def put_ma(q, a, x):
    """在庫の140点の線を、問題の側（120点）と答えの側（その後20点）に分けて足す（どちらも末尾に）"""
    bars = len(x["c"])
    for k in MA_KEYS:
        if isinstance(x.get(k), list) and len(x[k]) == bars + len(x["after"]):
            q[k] = x[k][:bars]
            a[k] = x[k][bars:]


def add_ma_to_day(data, by_id):
    """もうある日付のファイルに線だけ足す。足した問題の数を返す（ほかの値は変えない）"""
    n = 0
    qs = {q.get("no"): q for q in data.get("questions", [])}
    for a in data.get("answers", []):
        q = qs.get(a.get("no"))
        x = by_id.get(a.get("id"))
        if q is None or x is None or x.get("drop"):
            continue
        if all(k in q and k in a for k in MA_KEYS):
            continue
        # 同じ問題か（四本値・出来高・答え）を確かめてから足す。在庫を新しい値で作り直して食い違うときは足さない
        if any(q.get(k) != x.get(k) for k in ("o", "h", "l", "c", "v")) or a.get("after") != x.get("after"):
            log(f"  {data.get('date')} 問{a.get('no')}: 在庫と値が違うので線を足さない（{a.get('id')}）")
            continue
        qq, aa = {}, {}
        put_ma(qq, aa, x)
        if set(qq) != set(MA_KEYS):
            continue
        for k in MA_KEYS:
            q[k] = qq[k]
            a[k] = aa[k]
        n += 1
    return n


def backfill_ma(out_dir, items):
    """docs/quiz/ の日付ファイルのうち線の無いものに線を足す（書いたファイルの日付を返す）"""
    by_id = {x["id"]: x for x in items}
    done = []
    for p in sorted(Path(out_dir).glob("????-??-??.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if add_ma_to_day(data, by_id):
            if write_if_changed(p, dump(data)):
                done.append(p.stem)
    return done


def make_day(day, chosen, meta):
    qs, ans = [], []
    for i, x in enumerate(chosen, 1):
        q = {"no": i, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x["v"]}
        a = {"no": i, "id": x["id"], "code": x["code"], "name": x["name"], "sector": x["sector"],
             "period": period_ja(x["d0"], x["d1"]), "from": x["d0"], "q_end": x["dq"], "to": x["d1"],
             "after": x["after"], "ret": x["ret"], "dir": x["dir"]}
        put_ma(q, a, x)
        qs.append(q)
        ans.append(a)
    return {
        "schema": "kaburadar_quiz_day/1",
        "date": day,
        "n": len(qs), "bars": meta.get("bars", 120), "ahead": meta.get("ahead", 20),
        "n_up": sum(1 for a in ans if a["dir"] == "up"),
        "meta": {"note": NOTE, "pool_built": meta.get("built"), "pool_n": meta.get("n"),
                 "adjusted_asof": meta.get("adjusted_asof"),
                 "survivorship": meta.get("survivorship", "上場廃止・統合で今いない銘柄は入っていない"),
                 "base": "1本目の終値=100", "volume": "120本の最大=100",
                 "rule": "答え＝120本目の終値から20営業日後の終値までの騰落率。±2%以内は出さない"},
        "questions": qs,
        "answers": ans,
    }


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def write_if_changed(p, text):
    p = Path(p)
    if p.exists() and p.read_text(encoding="utf-8") == text:
        return False
    p.write_text(text, encoding="utf-8")
    return True


def write_latest(out_dir, today):
    days = sorted((p.stem for p in Path(out_dir).glob("????-??-??.json") if p.stem <= today), reverse=True)
    if not days:
        return None
    lt = {"schema": "kaburadar_quiz_latest/1", "date": days[0], "file": f"{days[0]}.json",
          "dates": days[:KEEP_DATES]}
    write_if_changed(Path(out_dir) / "latest.json", dump(lt))
    return lt


def run(day, pool=POOL, out=OUT, force=False):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"{day}.json"
    meta, items = load_pool(pool)
    filled = backfill_ma(out, items)
    if filled:
        log(f"線を足した日: {'・'.join(filled)}")
    if p.exists() and not force:
        log(f"{day} はもうある（書き換えない）")
    else:
        chosen = pick(items, day, past_files(out, day))
        if len(chosen) < N:
            raise SystemExit(f"5問そろわない（{len(chosen)}問）。在庫を作り直すこと")
        data = make_day(day, chosen, meta)
        changed = write_if_changed(p, dump(data))
        log(f"{day}: {'書いた' if changed else '同じ中身（変更なし）'}"
            f" 上{data['n_up']}・下{N - data['n_up']} / " + "・".join(a["code"] for a in data["answers"]))
    lt = write_latest(out, day)
    return lt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(JST).date().isoformat())
    ap.add_argument("--pool", default=str(POOL))
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    run(a.date, a.pool, a.out, a.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
