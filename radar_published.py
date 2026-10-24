# -*- coding: utf-8 -*-
"""温度計の「当時公開した判定」の並びを返す（2026-10-24 ルール統一のあと用）

■ なぜ要るか
  2026-10-24 に温度計の判定ルールを検証エンジン（BT）と同じに統一した（stock-trading- PR #716）。
  docs/radar_history.json の 2026-08-02〜10-23 は新ルールで再計算した値に差し替えた（recalc:true）。
  当時サイトに出していた判定は docs/radar_history_published_v1.json に保存してある（以後書き換えない）。

  連続日数・帯・「◯月以降で最長」などの表示は新しい履歴（radar_history.json）から出す。
  一方で「その時点で出した判定」を記録・採点するもの（シグナル台帳・公開した判定との一致）は、
  当時公開した判定を使う。このモジュールはその並びを1か所で作る。

■ 並び
  published_v1 の全行 ＋ radar_history.json のうち再計算でない行（recalc が真でない・v1 より後の日）
"""
import json
from pathlib import Path

DOCS = Path(__file__).resolve().parent / "docs"
HIST = DOCS / "radar_history.json"
V1 = DOCS / "radar_history_published_v1.json"


def _items(p):
    try:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return []
    return list((d if isinstance(d, dict) else {"items": d}).get("items") or [])


def published_items(docs=None):
    """当時公開した判定の並び（日付順）。v1 が無い環境では radar_history.json をそのまま返す"""
    base = Path(docs) if docs else DOCS
    v1 = _items(base / V1.name)
    cur = _items(base / HIST.name)
    if not v1:
        return sorted(cur, key=lambda x: x.get("d", ""))
    last = max(x.get("d", "") for x in v1)
    rest = [x for x in cur if not x.get("recalc") and x.get("d", "") > last]
    return sorted(v1 + rest, key=lambda x: x.get("d", ""))
