# -*- coding: utf-8 -*-
"""トップページ用の要約1本 → docs/board.json

■ なぜ作るか
  トップは11本のJSONを個別に fetch していて、合計 約550KB あった。
  そのうち karauri.json が339KB・kessan.json が69KB・ai_analysis.json が48KB で、
  トップが使うのは「4つの数字」「次の1日」「見出し1行」だけ。
  回線が細いと「読み込み中…」が長く、タイルが順に埋まってガタつく。

■ 形
  {"v":1,"updated":"...","files":{"radar.json":{...},"gauge.json":{...},...}}
  **元のJSONと同じ形のまま、要る値だけ**にして詰める。
  こうしておくと index.html 側は j(url) を bundle から返すだけで済み、
  11個の描画コードを1行も書き換えなくてよい（書き換えると必ず壊す）。
  bundle に無いファイルは今まで通り個別に fetch される（自動フォールバック）。

■ heatmap だけ別扱い
  タイルが使うのは「上昇/下落の数」「出来高2倍以上の数」「最大の1銘柄」だけなのに、
  元ファイルは337銘柄ぶん53KBある。集計だけを heatmap_agg に入れる。
  ⭐マイ銘柄は全銘柄の名前が要るが、登録がある人しか使わないので
  トップ側で「登録があるときだけ heatmap.json を取りに行く」ようにした。
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from data_targets import stale_list, freshness_table

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).parent
DOCS = HERE / "docs"
OUT = DOCS / "board.json"

KESSAN_DAYS = 14      # ⭐マイ銘柄の突き合わせに使う先の日数


def load(name):
    p = DOCS / name
    if not p.exists():
        print(f"  {name} が無い", file=sys.stderr)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  {name} を読めない: {e}", file=sys.stderr)
        return None


def pick(d, keys):
    return {k: d[k] for k in keys if d is not None and k in d}


def main():
    files = {}

    d = load("radar.json")
    if d:
        files["radar.json"] = pick(d, ["regime", "signal_count", "is_halt", "n225", "vix", "updated"])

    # 地合いの日次履歴。トップのヒーローが「7日連続」と前日比の矢印に使う。
    # 元ファイルは400日まで伸びるので、必要な25日ぶんだけ積む（トップは1fetchを守る）。
    d = load("radar_history.json")
    if d and d.get("items"):
        files["radar_history.json"] = {"items": d["items"][-25:]}

    d = load("gauge.json")
    if d:
        files["gauge.json"] = pick(d, ["lit", "total", "stage", "stage_key", "updated"])
        # 前日の点灯数。「3つ目に点灯」をトップで出すのに使う（履歴ごと配ると重い）
        h = d.get("history") or []
        if len(h) > 1:
            files["gauge.json"]["lit_prev"] = h[-2].get("lit")

    d = load("crash.json")
    if d:
        files["crash.json"] = pick(d, ["score", "stage", "stage_key", "updated"])

    d = load("karauri.json")
    if d and d.get("summary"):
        files["karauri.json"] = {"summary": pick(d["summary"], ["up", "new", "down", "out"]),
                                 "updated": d.get("updated")}

    d = load("kessan.json")
    if d:
        days = []
        for x in (d.get("days") or []):
            if x.get("past"):
                continue
            days.append({"d": x.get("d"), "w": x.get("w"), "n": x.get("n"),
                         "major": x.get("major"), "rush": x.get("rush"), "past": False,
                         # ⭐の突き合わせに要るのはコードと名前だけ
                         "items": [{"c": i.get("c"), "n": i.get("n")} for i in (x.get("items") or [])]})
            if len(days) >= KESSAN_DAYS:
                break
        files["kessan.json"] = {"today": d.get("today"), "updated": d.get("updated"), "days": days}

    d = load("shinyo.json")
    if d:
        files["shinyo.json"] = pick(d, ["buy_oku", "ratio", "buy_chg_oku", "updated"])

    d = load("investor_flow.json")
    if d:
        f = [i for i in (d.get("items") or []) if i.get("key") == "foreigners"]
        files["investor_flow.json"] = {"week": d.get("week"), "updated": d.get("updated"),
                                       "items": [pick(f[0], ["key", "net_oku"])] if f else []}

    d = load("cot.json")
    if d:
        y = [i for i in (d.get("items") or []) if i.get("key") == "jpy"]
        files["cot.json"] = {"updated": d.get("updated"),
                             "items": [pick(y[0], ["key", "net", "date"])] if y else []}

    d = load("ai_analysis.json")
    if d and d.get("latest"):
        L = d["latest"]
        files["ai_analysis.json"] = {"latest": {
            "headline": L.get("headline"), "stance": L.get("stance"),
            # 本文は80字しか出さないので、先頭だけ入れる（全文は48KBある）
            "today_watch": (L.get("today_watch") or "")[:140],
            "stance_reason": (L.get("stance_reason") or "")[:140],
            "updated": L.get("updated")}}

    # heatmap は集計だけ（337銘柄の明細は入れない）
    d = load("heatmap.json")
    if d:
        items = d.get("items") or []
        up = sum(1 for i in items if (i.get("c") or 0) > 0)
        dn = sum(1 for i in items if (i.get("c") or 0) < 0)
        ranked = sorted([i for i in items if i.get("r") and (i.get("c") or 0) > 0],
                        key=lambda i: -i["r"])[:20]
        secs = {}
        for i in ranked:
            secs[i.get("s")] = secs.get(i.get("s"), 0) + 1
        top = ranked[0] if ranked else None
        files["heatmap_agg"] = {
            "updated": d.get("updated"), "trade_date": d.get("trade_date"),
            "count": len(items), "up": up, "down": dn,
            "surge": sum(1 for i in items if (i.get("r") or 0) >= 2),
            "top1": ({"n": top.get("n"), "r": top.get("r")} if top else None),
            "secs": sorted(secs, key=lambda k: -secs[k])[:2],
        }

    if len(files) < 5:
        print(f"材料が足りない（{len(files)}本）。board.json は更新しない", file=sys.stderr)
        return 1

    # ★止まっているデータをトップに出すための材料★
    #   監視のIssueとメールは届いていなかった（空売りが10日間止まったのに気づけなかった）。
    #   本人と読者が毎日見る場所に出すのがいちばん確実。
    #   閾値は data_targets.py の TARGETS をそのまま使う（夜の監視と同じ表）。
    #   トップは朝も昼も描画されるので、当日の夕方ジョブ待ちで鳴らないよう1営業日の猶予を足す。
    stale = stale_list(HERE, slack=1)
    if stale:
        print("⚠️ 止まっているデータ: " + "／".join(f"{x['label']}({x['age']}営業日前)" for x in stale))

    # 状況ページの鮮度表（全 TARGETS）。status.html が自前の許容日数を持たないよう、ここから描く
    out = {"v": 1, "updated": datetime.now(JST).isoformat(timespec="seconds"),
           "stale": stale, "freshness": freshness_table(HERE), "files": files}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    kb = OUT.stat().st_size / 1024
    print(f"board.json: {len(files)}本ぶん / {kb:.1f}KB")
    for k in files:
        print(f"  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
