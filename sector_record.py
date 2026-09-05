# -*- coding: utf-8 -*-
"""セクターの風向きの答え合わせ → docs/sector_record.json（株レーダー kaburadar.jp）

AI朝刊は毎朝「このセクターは追い風／向かい風」と書く。それを言いっぱなしにせず、
その日の実際の値動きと突き合わせて○×をつける。採点にAIは関与しない。

■ 何と照合するか
  AI自身が根拠にしているのと同じデータ、つまり heatmap.json（出来高が急増した
  銘柄のスクリーニング結果）の当日騰落率を使う。
  **これは東証の業種別指数ではない。**出来高が急増した銘柄だけを集めた母集団の
  業種平均であり、業種によっては2〜3銘柄しかない。ページにもそう書くこと。

■ 「追い風」を当たりとする条件
  絶対値でプラスかどうかでは採点しない。それだと相場全体が上がった日は
  何を挙げても当たりになってしまう。
  その日の母集団全体の平均より強かったかどうか（相対）で採点する。

■ セクター名の対応づけ
  AIは「銀行・証券・その他金融」のように複数業種をまとめた名前を書く。
  ・2026-09-06以降: AIが match（heatmapの業種名そのもの）を出すのでそれを使う
  ・それ以前: 名前を区切り文字で割って業種名と突き合わせる（機械的・再現可能）
  どの業種に対応づけたかは必ず出力に残す。読む側が検算できるようにするため。

■ タイミング
  朝刊は寄り付き前に出て「今日はこうなりやすい」と書くので、同じ日の騰落率で採点する。

使い方: python sector_record.py [出力パス]
"""
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "docs" / "sector_record.json"
AI_PATH = "docs/ai_analysis.json"
HM_PATH = "docs/heatmap.json"

# 母集団が小さすぎる業種は採点しない（1〜2銘柄の平均は業種の風向きとは言えない）
MIN_STOCKS = 3
SPLIT = re.compile(r"[・／/、,･]|など|関連|セクター|その他")


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    return r.stdout.decode("utf-8", "replace")


def commits(path, since="120 days ago"):
    return git("log", f"--since={since}", "--format=%H", "--", path).split()


def collect_ai_sectors():
    """日付 → [{name, bias, match}]。git履歴の全バージョンから復元"""
    out = {}

    def eat(doc):
        for e in [doc.get("latest")] + list(doc.get("history") or []):
            if not (e and e.get("date") and e.get("sectors")):
                continue
            out.setdefault(e["date"], [
                {"name": s.get("name", ""), "bias": s.get("bias", ""),
                 "match": s.get("match") or []}
                for s in e["sectors"] if s.get("name")
            ])

    try:
        eat(json.loads((HERE / AI_PATH).read_text(encoding="utf-8")))
    except Exception as e:
        print(f"現行ai_analysis.json読めず: {e}", file=sys.stderr)
    for h in commits(AI_PATH):
        try:
            eat(json.loads(git("show", f"{h}:{AI_PATH}")))
        except Exception:
            continue
    return out


def collect_heatmap():
    """trade_date → {業種: {mean, n}} と市場平均。同じ日の複数版は最後の1つを使う"""
    days = {}
    seen = set()

    def eat(doc):
        d = doc.get("trade_date")
        items = doc.get("items")
        if not d or not items or d in seen:
            return
        seen.add(d)
        buckets = defaultdict(list)
        allc = []
        for it in items:
            c, s = it.get("c"), it.get("s")
            if isinstance(c, (int, float)):
                allc.append(c)
                if s:
                    buckets[s].append(c)
        if not allc:
            return
        days[d] = {
            "market": sum(allc) / len(allc),
            "n_all": len(allc),
            "sec": {k: {"mean": sum(v) / len(v), "n": len(v)} for k, v in buckets.items()},
        }

    # 新しいコミットから順に見て、日付ごとに最初に出会った版（=その日の最終版）を採る
    try:
        eat(json.loads((HERE / HM_PATH).read_text(encoding="utf-8")))
    except Exception as e:
        print(f"現行heatmap.json読めず: {e}", file=sys.stderr)
    for h in commits(HM_PATH):
        try:
            eat(json.loads(git("show", f"{h}:{HM_PATH}")))
        except Exception:
            continue
    return days


def resolve(name, match, available):
    """AIのセクター名 → heatmapの業種名リスト"""
    # AIが明示した対応づけを最優先（2026-09-06以降）
    hit = [m for m in (match or []) if m in available]
    if hit:
        return hit, "declared"
    if name in available:
        return [name], "exact"
    # 名前を区切って部分一致。「銀行・証券・その他金融」→ 銀行 / 証券
    found = []
    for tok in SPLIT.split(name):
        tok = tok.strip()
        if len(tok) < 2:
            continue
        if tok in available:
            found.append(tok)
            continue
        for a in available:
            if (tok in a or a in tok) and a not in found:
                found.append(a)
    return found, ("token" if found else "none")


def main():
    ai = collect_ai_sectors()
    hm = collect_heatmap()
    print(f"AI: {len(ai)}日ぶん / heatmap: {len(hm)}日ぶん", file=sys.stderr)

    rows = []
    for d in sorted(ai):
        day = hm.get(d)
        if not day:
            continue
        market = day["market"]
        avail = day["sec"]
        calls = []
        for s in ai[d]:
            names, how = resolve(s["name"], s["match"], avail)
            names = [n for n in names if avail[n]["n"] >= MIN_STOCKS]
            if not names:
                calls.append({"name": s["name"], "bias": s["bias"], "matched": [],
                              "how": "none", "judged": False})
                continue
            tot = sum(avail[n]["mean"] * avail[n]["n"] for n in names)
            cnt = sum(avail[n]["n"] for n in names)
            mean = tot / cnt
            rel = mean - market
            judged = s["bias"] in ("up", "down")
            hit = None
            if judged:
                hit = rel > 0 if s["bias"] == "up" else rel < 0
            calls.append({
                "name": s["name"], "bias": s["bias"], "matched": names, "how": how,
                "n": cnt, "ret": round(mean, 2), "rel": round(rel, 2),
                "judged": judged, "hit": hit,
            })
        if calls:
            rows.append({"d": d, "market": round(market, 2), "calls": calls})

    flat = [c for r in rows for c in r["calls"]]
    judged = [c for c in flat if c["judged"] and c["hit"] is not None]
    hits = [c for c in judged if c["hit"]]
    by_bias = {}
    for b in ("up", "down"):
        g = [c for c in judged if c["bias"] == b]
        by_bias[b] = {
            "judged": len(g), "hits": sum(1 for c in g if c["hit"]),
            "win": round(sum(1 for c in g if c["hit"]) / len(g) * 100, 1) if g else None,
            "avg_rel": round(sum(c["rel"] for c in g) / len(g), 2) if g else None,
        }

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "method": (
            "AI朝刊が挙げたセクターを、その日の実際の値動きと機械照合しています。"
            "照合先はAI自身が根拠にしているのと同じ出来高急増スクリーニング（heatmap）の当日騰落率で、"
            "東証の業種別指数ではありません。銘柄数が少ない業種もあります（3銘柄未満は採点対象外）。"
            "「追い風」は絶対値の上昇ではなく、その日の母集団全体の平均より強かったかで採点します。"
            "相場全体が上がった日に何を挙げても当たりになってしまうのを避けるためです。"
            "「注視」は方向を持たないので採点対象外です。"
        ),
        "days": len(rows),
        "calls": len(flat),
        "judged": len(judged),
        "hits": len(hits),
        "win": round(len(hits) / len(judged) * 100, 1) if judged else None,
        "by_bias": by_bias,
        "rows": rows[-25:],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK {OUT.name}: {len(rows)}日 / 発言{len(flat)}件 / 採点{len(judged)}件 "
          f"/ 的中{len(hits)}件" + (f" (勝率{data['win']}%)" if data["win"] is not None else ""))
    for b, g in by_bias.items():
        print(f"  {b:5s} 採点{g['judged']:>3}件 的中{g['hits']:>3}件 "
              f"勝率{g['win']} 平均で市場比{g['avg_rel']}%")
    unresolved = [c["name"] for c in flat if c["how"] == "none"]
    if unresolved:
        print(f"  対応づけできず（採点外）: {sorted(set(unresolved))[:12]}")


if __name__ == "__main__":
    main()
