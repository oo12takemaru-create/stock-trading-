# -*- coding: utf-8 -*-
"""決算速報の履歴に、通期予想の EPS（fc_eps）だけを足す（1回きりの保守用・2026-10-29 Fable 判断）

■ 決まり
  既存の項目の値は1つも変えない。TDnet に残っている（約31日）短信の zip を読み直し、extract() の結果から
  fc_eps だけを取り出して、まだ fc_eps を持たない行に足す。値が取れない（予想なし・未定）行は何も足さない。
  四半期（q）が手元の行と食い違う zip は足さずに件数を出す。
  書いた後に、git の HEAD の版と比べて「fc_eps を除くと完全に同じ」ことを機械で確かめる。
  差分が出たファイルは HEAD の版に戻して報告する。

対象: docs/kessan_flash_hist/*.json と docs/kessan_flash.json

使い方:
  python -X utf8 kessan_fc_eps_backfill.py [--cache DIR]
"""
import json
import subprocess
import sys
from pathlib import Path

from kessan_flash import extract
from kessan_flash_reread import zip_of

HERE = Path(__file__).parent
FILES = sorted((HERE / "docs" / "kessan_flash_hist").glob("*.json")) + [HERE / "docs" / "kessan_flash.json"]


def strip(o):
    """fc_eps を取り除いた写し（比べる用）"""
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k != "fc_eps"}
    if isinstance(o, list):
        return [strip(v) for v in o]
    return o


def head_version(p):
    rel = p.relative_to(HERE).as_posix()
    r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=HERE, capture_output=True)
    return json.loads(r.stdout.decode("utf-8")) if r.returncode == 0 else None


def main():
    a = sys.argv[1:]
    cache = a[a.index("--cache") + 1] if "--cache" in a else None
    got = {}            # 文書ID -> fc_eps（None＝取れない）。月別と kessan_flash.json で同じ短信を2回読まない
    stat = {"行": 0, "既にあり": 0, "足した": 0, "予想なし": 0, "zipなし": 0, "q不一致": 0}
    for p in FILES:
        d = json.loads(p.read_text(encoding="utf-8"))
        changed = False
        for day in d.get("days", []):
            for it in day.get("items", []):
                stat["行"] += 1
                if "fc_eps" in it:
                    stat["既にあり"] += 1
                    continue
                fixed = it.get("flag") == "訂正" and it.get("fix_pdf")
                doc = it["fix_pdf"].rsplit("/", 1)[1][:-4] if fixed else it.get("id")
                if not doc:
                    continue
                if doc not in got:
                    b = zip_of(doc, cache)
                    if not b:
                        got[doc] = ("zip", None)
                    else:
                        try:
                            r = extract(b)
                        except Exception:
                            r = {"skip": "読めない"}
                        got[doc] = ("ok", r) if not r.get("skip") else ("zip", None)
                kind, r = got[doc]
                if kind == "zip":
                    stat["zipなし"] += 1
                    continue
                if r.get("q") != it.get("q"):
                    stat["q不一致"] += 1
                    continue
                if r.get("fc_eps") is None:
                    stat["予想なし"] += 1
                    continue
                it["fc_eps"] = r["fc_eps"]
                stat["足した"] += 1
                changed = True
        if changed:
            p.write_text(json.dumps(d, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # 確かめ: HEAD の版と、fc_eps を除いて同じか
    bad = []
    for p in FILES:
        h = head_version(p)
        if h is None:
            continue
        now = json.loads(p.read_text(encoding="utf-8"))
        if strip(now) != strip(h):
            bad.append(p.name)
            subprocess.run(["git", "checkout", "HEAD", "--", p.relative_to(HERE).as_posix()], cwd=HERE)
    print(json.dumps({"stat": stat, "fc_eps以外の差分で戻したファイル": bad}, ensure_ascii=False))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
