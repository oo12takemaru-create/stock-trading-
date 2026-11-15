# -*- coding: utf-8 -*-
"""docs/score3.json の1日分を docs/score3_hist.json に足す（3軸スコアの判定の履歴）

■ なぜ要るか（2026-11-15）
  score3_hist.json は score3.py が実行のたびに足している。ところが AI朝刊が routine（定期実行）
  ＋ ai-morning-ingest.yml の経路に移ってから、main に入る score3.json は routine のブランチから
  取り込んだもので、score3_hist.json は取り込まれていなかった。予備の ai-analysis.yml の
  「3軸スコアだけ更新」が走らなくなった 2026-10-29 以降、履歴が 10/28 で止まっていた。
  → ingest で取り込んだ score3.json から、score3.py と同じ形の1行をここで足す。

  スコアの計算はしない（score3.json の値を写すだけ）。行の形と「1日1件＝その日の最後で上書き」
  「最新1200日まで」は score3.py と同じ。score3.py 側を変えたらここも合わせる。

■ 使い方
  python score3_hist_append.py docs/score3.json [docs/score3_hist.json]
  python score3_hist_append.py --backfill-git [docs/score3_hist.json]   # 止まっていた期間を git の履歴から埋める
"""
import json
import subprocess
import sys
from pathlib import Path

KEEP = 1200


def entry_of(data: dict) -> dict:
    """score3.py の main() が履歴に書く1行と同じ形"""
    ax = {a.get("key"): a.get("score") for a in data.get("axes") or []}
    return {"d": data["updated"][:10], "trade_date": data.get("trade_date"),
            "total": data["total"], "stance": data["stance"],
            "trend": ax.get("trend"), "risk": ax.get("risk"), "flow": ax.get("flow"),
            "inputs_ok": data.get("inputs_ok")}


def upsert(hist: dict, entries: list, updated: str | None = None) -> dict:
    by_day = {x.get("d"): x for x in hist.get("items", [])}
    for e in entries:
        by_day[e["d"]] = e
    items = sorted(by_day.values(), key=lambda x: x["d"])[-KEEP:]
    out = {"updated": updated or hist.get("updated"), "items": items}
    return out


def load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"items": []}


def save(p: Path, hist: dict):
    p.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def git_versions(path="docs/score3.json"):
    """git の履歴にある score3.json の各版（古い順）"""
    revs = subprocess.run(["git", "log", "--reverse", "--format=%H", "--", path],
                          capture_output=True, text=True, check=True).stdout.split()
    for r in revs:
        try:
            yield json.loads(subprocess.run(["git", "show", f"{r}:{path}"], capture_output=True,
                                            check=True).stdout.decode("utf-8"))
        except Exception:
            continue


def main():
    args = sys.argv[1:]
    if args and args[0] == "--backfill-git":
        hp = Path(args[1] if len(args) > 1 else "docs/score3_hist.json")
        hist = load(hp)
        have = {x["d"] for x in hist.get("items", [])}
        last = max(have) if have else ""
        latest, upd = {}, None
        for v in git_versions():        # 同じ日の版は後のもの（その日の最後）で上書き
            try:
                e = entry_of(v)
            except Exception:
                continue
            if e["d"] > last:
                latest[e["d"]] = e
                upd = max(upd or "", v.get("updated") or "")
        new = [latest[d] for d in sorted(latest)]
        hist = upsert(hist, new, updated=upd)
        save(hp, hist)
        print(f"埋め戻し {len(new)}日: {[e['d'] for e in new]}")
        return
    src = Path(args[0] if args else "docs/score3.json")
    hp = Path(args[1] if len(args) > 1 else src.parent / "score3_hist.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    hist = upsert(load(hp), [entry_of(data)], updated=data["updated"])
    save(hp, hist)
    print(f"score3_hist.json 更新: {len(hist['items'])}日分（{hist['items'][-1]['d']}）")


if __name__ == "__main__":
    main()
