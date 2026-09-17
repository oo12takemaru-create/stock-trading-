# -*- coding: utf-8 -*-
"""機関別の空売りの時系列 → docs/karauri_hist.json

■ できること・できないこと（先に書く）
  docs/karauri_state.json は**今この瞬間のスナップショット**で、過去の水準は持っていない。
  karauri_events/events.json は**変化（新規・増加・減少・解消）の履歴**を持っている。
  したがって作れるのは「**残高がどちら向きに積み上がってきたか（変化の累積）**」であって、
  「その日の残高の絶対水準」ではない。ここを混ぜない。
  現在の水準だけは state から取り、終点として添える。

■ 期間は4か月しかない
  events.json は 2026-06-01 公表分から。長期の結論は出さない。JPXの一覧は約70営業日で消えるため、
  これ以上は遡れない（だから毎日貯めている）。

■ 変化の定義
  新規 = +ratio（それまで報告義務の外にいた）
  増加・減少 = ratio − prev
  解消 = ratio − prev（0.5%を割って報告対象から外れた。ratio はその時点の値）
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
EVENTS = HERE / "karauri_events" / "events.json"
STATE = HERE / "docs" / "karauri_state.json"
OUT = HERE / "docs" / "karauri_hist.json"
TOP = 20


def main():
    ev = json.loads(EVENTS.read_text(encoding="utf-8"))
    pubs = sorted({e["pub"] for e in ev})

    # 機関ごと・公表日ごとの変化（残高比率の合計の増減と、報告件数）
    chg = defaultdict(lambda: defaultdict(float))
    cnt = defaultdict(lambda: defaultdict(int))
    for e in ev:
        s, p = e["seller"], e["pub"]
        r = e.get("ratio")
        prev = e.get("prev")
        if r is None:
            continue
        delta = r - (prev if prev is not None else 0.0)
        chg[s][p] += delta
        cnt[s][p] += 1

    # 現在の水準（スナップショット）。時系列ではないので終点としてだけ使う
    level = defaultdict(lambda: {"stocks": 0, "ratio_sum": 0.0})
    asof = None
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        for v in (st.get("positions") or {}).values():
            level[v["s"]]["stocks"] += 1
            level[v["s"]]["ratio_sum"] += float(v.get("r") or 0)
            if asof is None or (v.get("d") or "") > asof:
                asof = v.get("d")
    except Exception as e:
        print(f"  karauri_state.json を読めない（水準は出さない）: {e}")

    # 上位は「現在の報告銘柄数」で決める（変化の大きさで並べると入れ替わりが激しい）
    order = sorted(level, key=lambda s: -level[s]["stocks"])
    order = [s for s in order if s in chg][:TOP]
    if len(order) < TOP:
        for s in sorted(chg, key=lambda s: -sum(cnt[s].values())):
            if s not in order:
                order.append(s)
            if len(order) >= TOP:
                break

    sellers = []
    for s in order:
        acc, series = 0.0, []
        for p in pubs:
            acc += chg[s].get(p, 0.0)
            series.append(round(acc * 100, 3))      # 比率(小数) → %
        sellers.append({
            "seller": s,
            "stocks_now": level[s]["stocks"],
            "ratio_sum_now": round(level[s]["ratio_sum"] * 100, 2),
            "reports": sum(cnt[s].values()),
            "net_change_pt": round(acc * 100, 2),
            "series": series,
        })
    sellers.sort(key=lambda x: -x["stocks_now"])

    out = {
        "updated": datetime.now(JST).isoformat(timespec="seconds"),
        "source": "JPX「空売り残高に関する情報」の報告履歴（当サイトが毎営業日蓄積）",
        "level_asof": asof,
        "pubs": pubs,
        "coverage": {"pubs": len(pubs), "from": pubs[0], "to": pubs[-1],
                     "months": round(len(pubs) / 20.0, 1)},
        "caveats": [
            f"蓄積は{pubs[0]}公表分からで、{len(pubs)}営業日ぶんしかありません。長期の結論は出せません",
            "折れ線は「残高の絶対水準」ではなく、起点からの<b>変化の累積</b>です",
            "JPXの一覧は約70営業日で消えるため、これ以前には遡れません",
            "報告対象は発行済株式の0.5%以上だけです。それ未満の空売りは含まれません",
        ],
        "sellers": sellers,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"公表日 {len(pubs)}日（{pubs[0]} 〜 {pubs[-1]}・約{out['coverage']['months']}か月）")
    print(f"{'機関':34s} {'現銘柄数':>7s} {'現残高計%':>9s} {'報告数':>6s} {'変化の累積pt':>12s}")
    for s in sellers:
        print(f"{s['seller'][:32]:34s} {s['stocks_now']:7d} {s['ratio_sum_now']:9.1f} "
              f"{s['reports']:6d} {s['net_change_pt']:+12.2f}")
    print(f"\n→ {OUT.name} ({OUT.stat().st_size/1024:.1f}KB)")


if __name__ == "__main__":
    main()
