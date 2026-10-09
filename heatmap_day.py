# -*- coding: utf-8 -*-
"""その日のヒートマップの記録（日付アーカイブ用）と業種別ヒートマップ（入口強化 A-1・A-2・2026-10-09）

■ 出すもの
  docs/heatmap_days/YYYY-MM-DD.json  その取引日の東証全銘柄の上昇・下落の数、値上がり率／値下がり率の上位10、
                                     33業種の平均騰落。サイトの heatmap/YYYY-MM-DD.html はこれから焼く（消さずに積む）
  docs/heatmap_days/index.json       日付の一覧
  docs/heatmap_sector.json           業種別ヒートマップ（33業種を1枚ずつのタイルに。heatmap.json と同じ形）
  docs/x/heatmap_YYYY-MM-DD.png      その日のヒートマップ画像（1200×630・主要337銘柄）。サイトの日付ページと X 投稿（本人）用

■ 元データ
  docs/prices.json（movers_daily.py・東証全銘柄の終値。18時台）＋ tenbagger_universe.csv（名前・33業種・市場）
  画像だけは docs/heatmap.json（主要337銘柄・16:03 の引け後の回）。取引日が違えば画像は作らない

■ 上位10は売買代金1億円以上だけ（movers.json と同じ足切り。売買の少ない銘柄の値飛びを並べない）
  過去分は作らない（今日から積み上げる）。
使い方: python -X utf8 heatmap_day.py（movers-daily.yml の movers_daily.py の後）
"""
import csv
import json
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
DOCS = HERE / "docs"
DAYS = DOCS / "heatmap_days"
MIN_V = 100.0      # 売買代金（百万円）。1億円未満は上位10に入れない

# 33業種 → サイトの業種ハブ（kaburadar gen_stocks.py の SECTOR_SLUG と同じ。業種タイルのリンク先）
SECTOR_SLUG = {
    "水産・農林業": "fishery", "鉱業": "mining", "建設業": "construction",
    "食料品": "foods", "繊維製品": "textiles", "パルプ・紙": "pulp-paper",
    "化学": "chemicals", "医薬品": "pharmaceutical", "石油・石炭製品": "oil-coal",
    "ゴム製品": "rubber", "ガラス・土石製品": "glass-ceramics", "鉄鋼": "iron-steel",
    "非鉄金属": "nonferrous", "金属製品": "metal-products", "機械": "machinery",
    "電気機器": "electric-appliances", "輸送用機器": "transportation-equipment",
    "精密機器": "precision-instruments", "その他製品": "other-products",
    "電気・ガス業": "electric-power-gas", "陸運業": "land-transportation",
    "海運業": "marine-transportation", "空運業": "air-transportation",
    "倉庫・運輸関連業": "warehousing", "情報・通信業": "information-communication",
    "卸売業": "wholesale", "小売業": "retail", "銀行業": "banks",
    "証券、商品先物取引業": "securities", "保険業": "insurance",
    "その他金融業": "other-financing", "不動産業": "real-estate", "サービス業": "services",
}


def jload(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def jsave(p, obj):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def universe():
    u = {}
    with open(HERE / "tenbagger_universe.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            u[r["code"].strip()] = (unicodedata.normalize("NFKC", (r.get("name") or "").strip()),
                                    (r.get("sector33") or "").strip(), (r.get("market") or "").strip())
    return u


def build(prices, u):
    items = prices.get("items") or {}
    rows = []
    for c, it in items.items():
        if it.get("stale") or it.get("c") is None:
            continue
        n, s, m = u.get(c, (c, "", ""))
        rows.append({"c": c, "n": n, "s": s, "m": m, "chg": it["c"], "v": it.get("v") or 0, "p": it.get("p")})
    up = sum(1 for r in rows if r["chg"] > 0)
    dn = sum(1 for r in rows if r["chg"] < 0)
    liq = [r for r in rows if r["v"] >= MIN_V]
    top_up = sorted(liq, key=lambda r: -r["chg"])[:10]
    top_dn = sorted(liq, key=lambda r: r["chg"])[:10]
    sec = {}
    for r in rows:
        if not r["s"]:
            continue
        x = sec.setdefault(r["s"], {"s": r["s"], "n": 0, "sum": 0.0, "up": 0, "dn": 0, "v": 0.0})
        x["n"] += 1
        x["sum"] += r["chg"]
        x["up"] += r["chg"] > 0
        x["dn"] += r["chg"] < 0
        x["v"] += r["v"]
    sectors = []
    for x in sec.values():
        sectors.append({"s": x["s"], "slug": SECTOR_SLUG.get(x["s"]), "n": x["n"], "avg": round(x["sum"] / x["n"], 2),
                        "up": x["up"], "dn": x["dn"], "v": round(x["v"] / 100, 1)})      # v: 億円
    sectors.sort(key=lambda x: -x["avg"])
    keep = ("c", "n", "s", "chg", "v", "p")
    return {"count": len(rows), "up": up, "down": dn, "flat": len(rows) - up - dn,
            "top_up": [{k: r[k] for k in keep} for r in top_up],
            "top_down": [{k: r[k] for k in keep} for r in top_dn],
            "sectors": sectors}


def sector_map(day, updated, trade_date):
    """業種別ヒートマップ用（heatmap.json と同じ形。1業種＝1タイル。リンク先は業種ハブ）"""
    items = []
    for x in day["sectors"]:
        items.append({"t": x["slug"] or x["s"], "n": x["s"], "a": x["s"], "s": x["s"], "c": x["avg"],
                      "v": x["v"], "cnt": x["n"], "up": x["up"], "dn": x["dn"],
                      "u": f"sector/{x['slug']}.html" if x["slug"] else "sectors.html"})
    return {"updated": updated, "trade_date": trade_date, "count": len(items), "unit": "sector", "items": items}


# ─────────────────────────── 画像（主要337銘柄のヒートマップ）
FONTS = [r"C:\Windows\Fonts\YuGothB.ttc", r"C:\Windows\Fonts\meiryob.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
         "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf"]


def squarify(vals, x, y, w, h):
    """値の大きい順の配列 → [(x,y,w,h)]（Bruls ほか）。サイトの heatmap.html と同じ考え方"""
    out = []
    vals = list(vals)
    total = sum(vals)
    if total <= 0 or w <= 0 or h <= 0:
        return [(x, y, 0, 0)] * len(vals)
    scale = w * h / total
    areas = [v * scale for v in vals]
    i = 0
    while i < len(areas):
        short = min(w, h)
        row = [areas[i]]
        j = i + 1

        def worst(r):
            s = sum(r)
            return max(max(s * s / (short * short * a), short * short * a / (s * s)) for a in r if a > 0) if s > 0 else 1e9
        while j < len(areas) and worst(row + [areas[j]]) <= worst(row):
            row.append(areas[j])
            j += 1
        s = sum(row)
        if w >= h:
            cw = s / h if h else 0
            cy = y
            for a in row:
                ch = a / cw if cw else 0
                out.append((x, cy, cw, ch))
                cy += ch
            x += cw
            w -= cw
        else:
            rh = s / w if w else 0
            cx = x
            for a in row:
                cw2 = a / rh if rh else 0
                out.append((cx, y, cw2, rh))
                cx += cw2
            y += rh
            h -= rh
        i = j
    return out


def color(c):
    """上昇＝緑、下落＝赤（サイトの配色）。±3%で最も濃く"""
    t = max(-1.0, min(1.0, (c or 0) / 3.0))
    base = (38, 44, 58)
    tgt = (32, 170, 96) if t > 0 else (210, 58, 72)
    k = abs(t)
    return tuple(int(base[i] + (tgt[i] - base[i]) * k) for i in range(3))


def render_png(hm, day, path, date_label):
    from PIL import Image, ImageDraw, ImageFont
    fp = next((p for p in FONTS if os.path.exists(p)), None)
    if not fp:
        print("::warning::日本語フォントが無いので画像を作らない", file=sys.stderr)
        return False
    W, H, TOP = 1200, 630, 74
    im = Image.new("RGB", (W, H), (5, 8, 15))
    dr = ImageDraw.Draw(im)
    f_t, f_s, f_c, f_l = (ImageFont.truetype(fp, s) for s in (34, 20, 13, 12))
    dr.text((24, 14), f"日本株ヒートマップ {date_label}", font=f_t, fill=(238, 242, 255))
    sub = f"東証全{day['count']:,}銘柄  上昇 {day['up']:,}・下落 {day['down']:,}"
    dr.text((24, 54 - 6), sub, font=f_s, fill=(160, 172, 200))
    dr.text((W - 160, 22), "kaburadar.jp", font=f_s, fill=(255, 212, 121))
    items = [i for i in hm["items"] if i.get("v")]
    groups = {}
    for i in items:
        groups.setdefault(i["s"], []).append(i)
    gl = sorted(groups.items(), key=lambda kv: -sum(i["v"] for i in kv[1]))
    rects = squarify([sum(i["v"] for i in g) for _, g in gl], 8, TOP + 6, W - 16, H - TOP - 14)
    for (name, g), (gx, gy, gw, gh) in zip(gl, rects):
        g = sorted(g, key=lambda i: -i["v"])
        pad = 15 if gw > 70 and gh > 40 else 0
        for i, (x, y, w, h) in zip(g, squarify([i["v"] for i in g], gx + 1, gy + 1 + pad, gw - 2, gh - 2 - pad)):
            if w < 2 or h < 2:          # 小さすぎるタイルは描かない（枠の色だけ残る）
                continue
            dr.rectangle([x, y, x + w - 1, y + h - 1], fill=color(i["c"]), outline=(5, 8, 15))
            if w > 46 and h > 28:
                lab = i.get("a") or i["n"]
                dr.text((x + 4, y + 3), lab[:max(2, int(w // 13))], font=f_c, fill=(240, 244, 255))
                dr.text((x + 4, y + 18), f"{i['c']:+.1f}%", font=f_c, fill=(240, 244, 255))
        if gw >= 2 and gh >= 2:
            dr.rectangle([gx, gy, gx + gw - 1, gy + gh - 1], outline=(90, 104, 140))
        if pad:
            dr.text((gx + 4, gy + 1), name[:max(2, int(gw // 12))], font=f_l, fill=(200, 210, 235))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    im.save(path, optimize=True)
    return True


def main():
    prices = jload(DOCS / "prices.json")
    if not prices or not prices.get("trade_date"):
        print("::error::prices.json が無い", file=sys.stderr)
        return 1
    d = prices["trade_date"]
    u = universe()
    day = build(prices, u)
    day.update({"d": d, "updated": datetime.now(JST).isoformat(timespec="seconds"), "png": None, "main": None})
    hm = jload(DOCS / "heatmap.json")
    wd = "月火水木金土日"[datetime.fromisoformat(d).weekday()]
    label = f"{int(d[:4])}年{int(d[5:7])}月{int(d[8:10])}日（{wd}）"
    if hm and hm.get("trade_date") == d:
        items = hm.get("items") or []
        day["main"] = {"count": len(items), "up": sum(1 for i in items if i["c"] > 0),
                       "down": sum(1 for i in items if i["c"] < 0)}
        png = DOCS / "x" / f"heatmap_{d}.png"
        try:
            if render_png(hm, day, png, label):
                day["png"] = f"x/heatmap_{d}.png"
        except Exception as e:
            print(f"::warning::画像を作れなかった: {e}", file=sys.stderr)
    else:
        print(f"heatmap.json の取引日（{hm and hm.get('trade_date')}）が {d} と違うので画像は作らない", file=sys.stderr)
    jsave(DAYS / f"{d}.json", day)
    dates = sorted((p.stem for p in DAYS.glob("????-??-??.json")), reverse=True)
    jsave(DAYS / "index.json", {"updated": day["updated"], "dates": dates})
    jsave(DOCS / "heatmap_sector.json", sector_map(day, day["updated"], d))
    print(f"heatmap_days/{d}.json: 全{day['count']} 上昇{day['up']} 下落{day['down']} 業種{len(day['sectors'])} 画像={day['png']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
