# -*- coding: utf-8 -*-
"""AI朝刊・地合い判定の成績表 → docs/ai_record.json（株レーダー kaburadar.jp）

「予想の成績を機械採点で公開しているサイトは他にない」を実現する。

■ 採点ルール（AIは採点に一切関与しない。すべて機械照合）
  ・AI朝刊のスタンス（attack/neutral/defense）は朝6:30＝寄り付き前に書かれる
    → その日の日経平均の騰落率（当日終値÷前営業日終値）と照合する
  ・地合い判定（BULLISH等）は日中〜引けにかけて更新される
    → 先読みを避けるため「翌営業日」の騰落率と照合する
  ・方向一致: attack/BULLISH→上昇なら一致、defense/BEARISH→下落なら一致。
    neutral/NEUTRALは方向を持たないので一致率の分母に入れない（平均騰落率のみ示す）

■ スタンスの復元
  公開JSONは直近7日しか持たないが、gitのコミット履歴に全バージョンが残っている。
  git log → git show で ai_analysis.json の全日付ぶんを復元する（このスクリプトは
  リポジトリ内・full checkout で動かす前提）。

■ サンプルが少ないうちは「蓄積中」と明示する
  10日やそこらの平均に意味はない。件数を必ず出し、30日未満は参考値と表示させる。
"""
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
OUT = HERE / "docs" / "ai_record.json"
AI_PATH = "docs/ai_analysis.json"


def git(*args):
    r = subprocess.run(["git"] + list(args), capture_output=True, cwd=HERE)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    return r.stdout.decode("utf-8", "replace")


def collect_ai_stances():
    """日付→スタンス。git履歴の全バージョン＋現行ファイルのhistoryから復元"""
    stances = {}

    def eat(doc):
        for entry in [doc.get("latest")] + list(doc.get("history") or []):
            if entry and entry.get("date") and entry.get("stance"):
                stances.setdefault(entry["date"], {
                    "stance": entry["stance"],
                    "headline": entry.get("headline", "")[:60],
                })

    try:
        eat(json.loads((HERE / AI_PATH).read_text(encoding="utf-8")))
    except Exception as e:
        print(f"現行ai_analysis.json読めず: {e}", file=sys.stderr)

    try:
        hashes = git("log", "--format=%H", "--", AI_PATH).split()
    except Exception as e:
        print(f"git log失敗: {e}", file=sys.stderr)
        hashes = []
    for h in hashes:
        try:
            eat(json.loads(git("show", f"{h}:{AI_PATH}")))
        except Exception:
            continue
    return stances


def n225_closes():
    import yfinance as yf
    import pandas as pd
    d = yf.download("^N225", period="6mo", progress=False, auto_adjust=False)["Close"].dropna()
    if isinstance(d, pd.DataFrame):
        d = d.iloc[:, 0]
    return {i.strftime("%Y-%m-%d"): float(v) for i, v in d.items()}


def build_returns(closes):
    """日付→(当日リターン%, 翌営業日リターン%)"""
    days = sorted(closes)
    same, nxt = {}, {}
    for i in range(1, len(days)):
        same[days[i]] = (closes[days[i]] / closes[days[i - 1]] - 1) * 100
    for i in range(len(days) - 1):
        nxt[days[i]] = (closes[days[i + 1]] / closes[days[i]] - 1) * 100
    return same, nxt


def aggregate(rows, up_keys, down_keys):
    """スタンス別の 件数・平均リターン・方向一致率"""
    out = {}
    for r in rows:
        s = r["s"]
        g = out.setdefault(s, {"n": 0, "sum": 0.0, "hit": 0, "judged": 0})
        g["n"] += 1
        g["sum"] += r["ret"]
        if s in up_keys or s in down_keys:
            g["judged"] += 1
            if (s in up_keys and r["ret"] > 0) or (s in down_keys and r["ret"] < 0):
                g["hit"] += 1
    for s, g in out.items():
        g["avg"] = round(g["sum"] / g["n"], 3) if g["n"] else None
        g["win"] = round(g["hit"] / g["judged"] * 100, 1) if g["judged"] else None
        del g["sum"]
    return out


def main():
    closes = n225_closes()
    same, nxt = build_returns(closes)

    # ── AI朝刊（当日リターンで採点）──
    stances = collect_ai_stances()
    ai_rows = []
    for d in sorted(stances):
        if d in same:
            ai_rows.append({"d": d, "s": stances[d]["stance"],
                            "h": stances[d]["headline"], "ret": round(same[d], 2)})
    ai_stats = aggregate(ai_rows, up_keys={"attack"}, down_keys={"defense"})

    # ── 地合い判定（翌営業日リターンで採点。当日だと先読みになるため）──
    reg_rows = []
    try:
        rh = json.loads((HERE / "docs" / "radar_history.json").read_text(encoding="utf-8"))
        items = rh if isinstance(rh, list) else rh.get("items", rh.get("history", []))
        for it in items:
            d = it.get("d")
            if d and it.get("regime") and d in nxt:
                reg_rows.append({"d": d, "s": it["regime"], "ret": round(nxt[d], 2)})
    except Exception as e:
        print(f"radar_history読めず: {e}", file=sys.stderr)
    reg_stats = aggregate(reg_rows, up_keys={"BULLISH"}, down_keys={"BEARISH", "PANIC"})

    data = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "method": {
            "ai": "AI朝刊は寄り付き前（6:30）に公開されるため、その日の日経平均の騰落率（終値÷前営業日終値）と機械照合。採点にAIは関与しません。",
            "regime": "地合い判定は日中に更新されるため、先読みを避けて翌営業日の騰落率と照合。",
            "win": "方向一致率＝attack/BULLISHの日に上昇、defense/BEARISHの日に下落した割合。中立は方向を持たないため一致率の対象外。",
            "min_n": 30,
        },
        "ai": {"n": len(ai_rows), "stats": ai_stats, "rows": ai_rows[-40:]},
        "regime": {"n": len(reg_rows), "stats": reg_stats, "rows": reg_rows[-40:]},
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK ai_record.json: AI {len(ai_rows)}日 / 地合い {len(reg_rows)}日")
    for name, st in (("AI", ai_stats), ("地合い", reg_stats)):
        for s, g in sorted(st.items()):
            print(f"  {name} {s:8s} n={g['n']:>3} 平均{g['avg']:+.2f}% 一致率{g['win'] if g['win'] is not None else '—'}")


if __name__ == "__main__":
    main()
