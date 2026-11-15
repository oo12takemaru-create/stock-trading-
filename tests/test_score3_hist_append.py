# -*- coding: utf-8 -*-
"""score3_hist_append.py のテスト（AI朝刊の取り込みで 3軸スコアの履歴を足す・2026-11-15）

  1. 行の形が score3.py の履歴の行と同じ
  2. 同じ日は上書き（1日1件）・別の日は足す・日付順・最新1200日まで
  3. 取り込みの手順（ai-morning-ingest.yml と同じコマンド）を一時フォルダで回して、
     score3.json は1文字も変わらず、score3_hist.json に今日の行が入る
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import score3_hist_append as H  # noqa: E402

NG = 0


def ok(cond, label):
    global NG
    if not cond:
        NG += 1
        print("  NG", label)


def fake(day, total=-1, stance="lean_defense"):
    return {"updated": f"{day}T06:06:05+09:00", "trade_date": "2026-11-12", "total": total,
            "stance": stance, "inputs_ok": True,
            "axes": [{"key": "trend", "score": -2}, {"key": "risk", "score": 0}, {"key": "flow", "score": 1}]}


# 1. 行の形
e = H.entry_of(fake("2026-11-13"))
keys = {"d", "trade_date", "total", "stance", "trend", "risk", "flow", "inputs_ok"}
ok(set(e) == keys, f"行の形 {set(e)}")
src = (ROOT / "score3.py").read_text(encoding="utf-8")
block = src[src.index("entry = {"):src.index("}", src.index("entry = {"))]
ok(set(re.findall(r'"(\w+)":', block)) == keys, "score3.py の履歴の行と同じキー")
ok(e == {"d": "2026-11-13", "trade_date": "2026-11-12", "total": -1, "stance": "lean_defense",
         "trend": -2, "risk": 0, "flow": 1, "inputs_ok": True}, "値の写し")

# 2. 上書き・追加・順序・上限
h = H.upsert({"items": []}, [H.entry_of(fake("2026-11-13"))])
h = H.upsert(h, [H.entry_of(fake("2026-11-13", total=2, stance="lean_attack"))])
ok(len(h["items"]) == 1 and h["items"][0]["total"] == 2, "同じ日は上書き")
h = H.upsert(h, [H.entry_of(fake("2026-11-10"))])
ok([x["d"] for x in h["items"]] == ["2026-11-10", "2026-11-13"], "日付順")
many = [{"d": f"20{i:04d}"} for i in range(1300)]
ok(len(H.upsert({"items": many}, [])["items"]) == H.KEEP, "最新1200日まで")

# 3. 取り込みの手順を一時フォルダで（ai-morning-ingest.yml と同じコマンド）
with tempfile.TemporaryDirectory() as td:
    docs = Path(td) / "docs"
    docs.mkdir()
    s3 = docs / "score3.json"
    s3.write_text(json.dumps(fake("2026-11-16"), ensure_ascii=False, indent=1), encoding="utf-8")
    before = s3.read_bytes()
    hp = docs / "score3_hist.json"
    shutil.copy(ROOT / "docs" / "score3_hist.json", hp)
    n0 = len(json.loads(hp.read_text(encoding="utf-8"))["items"])
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "score3_hist_append.py"),
                        "docs/score3.json", "docs/score3_hist.json"], cwd=td, capture_output=True, text=True)
    ok(r.returncode == 0, f"コマンドが落ちた {r.stderr[-300:]}")
    ok(s3.read_bytes() == before, "score3.json を書き換えた")
    items = json.loads(hp.read_text(encoding="utf-8"))["items"]
    ok(len(items) == n0 + 1 and items[-1]["d"] == "2026-11-16", "今日の行が足された")
    # 履歴ファイルが無くても動く（初回）
    hp.unlink()
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "score3_hist_append.py"),
                        "docs/score3.json", "docs/score3_hist.json"], cwd=td, capture_output=True, text=True)
    ok(r.returncode == 0 and len(json.loads(hp.read_text(encoding="utf-8"))["items"]) == 1, "履歴が無い初回")

print("NG" if NG else "OK", f"(不一致 {NG})")
sys.exit(1 if NG else 0)
