# -*- coding: utf-8 -*-
"""分類のテスト。Worker側(node tests/run.mjs)と同じ tests/classify_cases.json を使う。
片方だけ直すとどちらかが落ちる。"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from buyback_classify import classify  # noqa: E402

cases = json.loads((pathlib.Path(__file__).parent / "classify_cases.json").read_text(encoding="utf-8"))
ng = 0
for c in cases:
    ty, tos = classify(c["title"])
    if ty != c["type"] or tos != c["tostnet"]:
        ng += 1
        print(f'  NG 期待={c["type"]}/{c["tostnet"]} 実際={ty}/{tos}\n     {c["title"]}')
print(f"{len(cases)-ng}/{len(cases)} 一致" + ("" if ng else " ✓"))
sys.exit(1 if ng else 0)
