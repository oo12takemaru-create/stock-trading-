"""signal.json から株レーダー(kaburadar.jp)公開用の docs/radar.json を生成する。

- 銘柄名・個別シグナルの中身は含めない(投資助言リスク回避のため件数のみ)
- 使い方: python make_radar_json.py signal.json docs/radar.json
"""
import json
import sys
from datetime import datetime, timezone, timedelta


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        d = json.load(f)

    jst = timezone(timedelta(hours=9))

    def _num(v):
        try:
            return round(float(v), 2)
        except (TypeError, ValueError):
            return None

    out = {
        "updated": datetime.now(jst).isoformat(timespec="seconds"),
        "scanner_timestamp": d.get("timestamp"),
        "regime": d.get("regime"),          # BULLISH / NEUTRAL / BEARISH / PANIC
        "is_halt": bool(d.get("is_halt")),
        "halt_reason": d.get("halt_reason") or "",
        "vix": _num(d.get("vix")),
        "n225": _num(d.get("n225")),
        "signal_count": len(d.get("signals") or []),
    }

    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"radar.json 生成: {out}")

    # ★地合い履歴の蓄積(docs/radar_history.json・1日1エントリ=その日の最後の実行で上書き)
    try:
        hist_path = "docs/radar_history.json"
        try:
            with open(hist_path, encoding="utf-8") as f:
                hist = json.load(f)
        except Exception:
            hist = {"items": []}
        today = out["updated"][:10]
        entry = {"d": today, "regime": out["regime"], "vix": out["vix"],
                 "n225": out["n225"], "sig": out["signal_count"],
                 "halt": out["is_halt"]}
        items = [x for x in hist.get("items", []) if x.get("d") != today]
        items.append(entry)
        items = sorted(items, key=lambda x: x["d"])[-400:]
        with open(hist_path, "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, separators=(",", ":"))
        print(f"radar_history.json 更新: {len(items)}日分")
    except Exception as e:
        print(f"履歴更新スキップ: {e}")


if __name__ == "__main__":
    main()
