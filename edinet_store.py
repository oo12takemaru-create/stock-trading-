# -*- coding: utf-8 -*-
"""EDINET 大量保有報告書の保存場所（読み書きはこのモジュールだけを通す）

■ 形（2026-10-09 から）
  docs/edinet/days/YYYY-MM-DD.jsonl.gz   提出日ごとに1ファイル。1行＝1書類（中身の形は edinet_daily.py の parse_doc）
  旧形式 docs/edinet/YYYY-MM.jsonl（〜2026-10-08）は移行済み。残っていても読めるようにしてある

■ なぜ日ごと・gzip か
  1年で約17,000件・生で60MB。うち74%は「60日間の取得・処分」の表（証券会社は1件に数百行ある）。
  月ごとのファイルに毎朝追記すると、数MBのファイルを毎日書き直すことになり git の履歴が膨らむ。
  日ごとなら書いたら二度と触らない（「情報を修正された過去の書類」が来た日だけ、その日のファイルを書き直す）。
  gzip で約10分の1（1年 約6MB、5年 約30MB）。期限を切らずに全部残す。

■ 使う側
  edinet_daily.py（書く・読む）、holders_build.py（積上⑧・読むだけ）
"""
import gzip
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDINET = HERE / "docs" / "edinet"
DAYS = EDINET / "days"

_ERA = {"令和": 2018, "平成": 1988, "昭和": 1925}


def iso_date(s):
    """「2026-01-09」「令和8年1月9日」「2026年1月9日」「2026/1/9」を ISO に。読めなければ元の文字列のまま
    （取得・処分の表は提出者ごとに書き方が違う。7,588行が和暦だった・積上⑧ #598 で発覚）"""
    if not s:
        return s
    t = str(s).strip()
    m = re.match(r"(令和|平成|昭和)\s*(\d+|元)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", t)
    if m:
        y = _ERA[m.group(1)] + (1 if m.group(2) == "元" else int(m.group(2)))
        return f"{y:04d}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
    m = re.match(r"(\d{4})\s*[年/\-.]\s*(\d{1,2})\s*[月/\-.]\s*(\d{1,2})", t)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return t


def _read(p):
    if p.suffix == ".gz":
        text = gzip.decompress(p.read_bytes()).decode("utf-8")
    else:
        text = p.read_text(encoding="utf-8")
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def day_ids(d):
    """その提出日のファイルに入っている docID"""
    p = DAYS / f"{d}.jsonl.gz"
    return {r["id"] for r in _read(p)} if p.exists() else set()


def write(recs):
    """提出日ごとに足す（同じ docID は上書きしない）。書いた日付の一覧を返す"""
    by = {}
    for r in recs:
        by.setdefault(r["d"], []).append(r)
    DAYS.mkdir(parents=True, exist_ok=True)
    done = []
    for d, rs in sorted(by.items()):
        p = DAYS / f"{d}.jsonl.gz"
        old = _read(p) if p.exists() else []
        have = {r["id"] for r in old}
        new = old + [r for r in rs if r["id"] not in have]
        if len(new) == len(old):
            continue
        body = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in new)
        # mtime=0: 中身が同じなら gzip のバイト列も同じ（git に差分を出さない）
        p.write_bytes(gzip.compress(body.encode("utf-8"), compresslevel=9, mtime=0))
        done.append(d)
    return done


def iter_records(since=None, until=None):
    """提出日順に全書類（since/until は YYYY-MM-DD・両端を含む）。docID で一意"""
    seen = set()
    files = sorted(DAYS.glob("????-??-??.jsonl.gz")) + sorted(EDINET.glob("????-??.jsonl"))
    out = []
    for p in files:
        stem = p.name[:10]
        if p.suffix == ".gz":
            if (since and stem < since) or (until and stem > until):
                continue
        for r in _read(p):
            if r["id"] in seen:
                continue
            if (since and r["d"] < since) or (until and r["d"] > until):
                continue
            seen.add(r["id"])
            out.append(r)
    return sorted(out, key=lambda r: (r["d"], r.get("tm") or "", r["id"]))
