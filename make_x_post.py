# -*- coding: utf-8 -*-
"""analysis.json + inputs.txt から X(Twitter)投稿用のテキストと画像を作る。

出力:
  x_post.txt  ... 本文 + リプ文(URL誘導)
  x_card.png  ... 1200x675 の投稿画像

使い方: python -X utf8 make_x_post.py
"""
import json
import os
import re
import unicodedata
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_URL = "https://kaburadar.jp"

BG = (12, 16, 24)
CARD = (20, 26, 38)
LINE = (44, 54, 72)
FG = (236, 240, 246)
SUB = (140, 152, 172)
UP = (255, 92, 92)      # 日本式: 上昇=赤
DOWN = (74, 158, 255)   # 下落=青
ACCENT = (255, 196, 61)

STANCE_JA = {"attack": "攻め", "neutral": "中立", "defense": "守り"}
STANCE_COLOR = {"attack": UP, "neutral": ACCENT, "defense": DOWN}
BIAS_MARK = {"up": ("▲", UP), "down": ("▼", DOWN), "watch": ("―", SUB)}

# Windows(ローカル)とubuntu(GitHub Actions)の両方で動くようフォントを探す
FONT_CANDIDATES = {
    "bold": [
        r"C:\Windows\Fonts\YuGothB.ttc",
        r"C:\Windows\Fonts\meiryob.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Bold.otf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
    "regular": [
        r"C:\Windows\Fonts\YuGothM.ttc",
        r"C:\Windows\Fonts\meiryo.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    ],
}


def _resolve(kind):
    for p in FONT_CANDIDATES[kind]:
        if os.path.exists(p):
            return p
    raise RuntimeError(f"日本語フォントが見つかりません({kind})")


FONT_B = _resolve("bold")
FONT_R = _resolve("regular")


def font(path, size):
    return ImageFont.truetype(path, size)


def tweet_len(s):
    """Xの重み付き文字数(全角=2, 半角=1)。上限280。"""
    n = 0
    for ch in s:
        n += 2 if unicodedata.east_asian_width(ch) in "WFA" else 1
    return n


def parse_inputs(path):
    """inputs.txt から画像・本文に使う数字を抜き出す。"""
    d = {}
    if not os.path.exists(path):
        return d
    with open(path, encoding="utf-8") as f:
        text = f.read()

    for key, label in [("nikkei", "日経平均"), ("vix", "VIX"), ("jgb", "米10年債利回り")]:
        m = re.search(re.escape(label) + r":\s*([\d,\.]+)\s*\(前日([+\-−][\d\.]+)%\s*/\s*5日([+\-−][\d\.]+)%\)", text)
        if m:
            d[key] = {"value": m.group(1), "d1": m.group(2), "d5": m.group(3)}

    m = re.search(r"CME日経先物\(円建て\):\s*([\d,]+)円.*?乖離\s*([+\-−][\d\.]+)%", text)
    if m:
        d["fut"] = {"value": m.group(1), "gap": m.group(2)}

    m = re.search(r"^(明日|\d+日後[^:：]*): *(.+?) +(\d{1,2}:\d{2})\s*$", text, re.M)
    if m:
        d["event"] = {"when": m.group(1), "name": m.group(2).strip(), "time": m.group(3)}
    return d


def color_for(pct):
    try:
        return UP if float(pct.replace("−", "-")) >= 0 else DOWN
    except ValueError:
        return SUB


def tidy_num(s):
    """65,683.26 -> 65,683 (4桁以上は小数を落として桁を詰める)"""
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return s
    return f"{v:,.0f}" if abs(v) >= 1000 else s


def short_name(s, limit=10):
    s = re.split(r"[（(]", s)[0].strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------- 画像
def build_card(a, d, date_str, out_path):
    W, H = 1200, 675
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)

    f_logo = font(FONT_B, 30)
    f_date = font(FONT_R, 24)
    f_head = font(FONT_B, 52)
    f_badge = font(FONT_B, 30)
    f_num = font(FONT_B, 40)
    f_lbl = font(FONT_R, 21)
    f_sub = font(FONT_R, 23)
    f_sec = font(FONT_B, 25)
    f_foot = font(FONT_R, 25)

    # ヘッダー
    dr.text((56, 44), "株レーダー AI相場分析", font=f_logo, fill=FG)
    dr.text((W - 56, 50), date_str, font=f_date, fill=SUB, anchor="ra")
    dr.line([(56, 96), (W - 56, 96)], fill=LINE, width=2)

    # スタンスのバッジ
    st = a.get("stance", "neutral")
    label = "スタンス " + STANCE_JA.get(st, st)
    bw = int(dr.textlength(label, font=f_badge)) + 44
    dr.rounded_rectangle([56, 128, 56 + bw, 128 + 54], radius=27,
                         fill=CARD, outline=STANCE_COLOR.get(st, ACCENT), width=3)
    dr.text((56 + bw / 2, 128 + 27), label, font=f_badge,
            fill=STANCE_COLOR.get(st, ACCENT), anchor="mm")

    # 見出し(1行に収まるまで縮小、無理なら2行)
    head = a.get("headline", "")
    limit = W - 112
    size = 52
    while size > 38 and dr.textlength(head, font=font(FONT_B, size)) > limit:
        size -= 2
    f_head = font(FONT_B, size)
    lines, line = [], ""
    for ch in head:
        if dr.textlength(line + ch, font=f_head) > limit:
            lines.append(line)
            line = ch
        else:
            line += ch
    lines.append(line)
    y = 218 if len(lines) > 1 else 232
    for ln in lines[:2]:
        dr.text((56, y), ln, font=f_head, fill=FG)
        y += size + 12

    # 数字パネル
    top = 372
    cells = []
    if "nikkei" in d:
        cells.append(("日経平均", tidy_num(d["nikkei"]["value"]), d["nikkei"]["d1"] + "%", color_for(d["nikkei"]["d1"])))
    if "fut" in d:
        cells.append(("寄り付き想定(先物)", tidy_num(d["fut"]["value"]), d["fut"]["gap"] + "%", color_for(d["fut"]["gap"])))
    # VIX・金利は「上昇=良い」ではないので色を付けない
    if "vix" in d:
        cells.append(("VIX", d["vix"]["value"], d["vix"]["d1"] + "%", SUB))
    if "jgb" in d:
        cells.append(("米10年債", d["jgb"]["value"] + "%", d["jgb"]["d1"] + "%", SUB))

    if cells:
        gap, n = 18, len(cells)
        cw = (W - 112 - gap * (n - 1)) / n
        for i, (lbl, val, chg, col) in enumerate(cells):
            x = 56 + i * (cw + gap)
            dr.rounded_rectangle([x, top, x + cw, top + 128], radius=14, fill=CARD)
            dr.text((x + 22, top + 20), lbl, font=f_lbl, fill=SUB)
            # 値と変化率が重ならないよう、値のサイズを落とす
            chg_w = dr.textlength(chg, font=f_sub)
            room = cw - 44 - chg_w - 14
            vsize = 40
            while vsize > 24 and dr.textlength(val, font=font(FONT_B, vsize)) > room:
                vsize -= 2
            dr.text((x + 22, top + 52 + (40 - vsize) // 2), val, font=font(FONT_B, vsize), fill=FG)
            dr.text((x + cw - 22, top + 64), chg, font=f_sub, fill=col, anchor="ra")

    # セクター
    sy = 538
    dr.text((56, sy), "注目セクター", font=f_lbl, fill=SUB)
    x = 56
    for s in a.get("sectors", [])[:4]:
        mark, col = BIAS_MARK.get(s.get("bias"), BIAS_MARK["watch"])
        txt = f"{mark} {short_name(s.get('name', ''))}"
        w = dr.textlength(txt, font=f_sec) + 36
        if x + w > W - 56:
            break
        dr.rounded_rectangle([x, sy + 30, x + w, sy + 78], radius=12, fill=CARD, outline=LINE, width=1)
        dr.text((x + 18, sy + 54), txt, font=f_sec, fill=col, anchor="lm")
        x += w + 14

    # フッター
    dr.line([(56, 632), (W - 56, 632)], fill=LINE, width=2)
    dr.text((56, 652), "kaburadar.jp", font=f_foot, fill=ACCENT, anchor="lm")
    dr.text((W - 56, 653), "※投資判断は自己責任で", font=f_lbl, fill=SUB, anchor="rm")

    img.save(out_path, quality=95)
    return out_path


# ---------------------------------------------------------------- 本文
def build_post(a, d, date_str):
    md = date_str.split("(")[0].strip()
    md = "/".join(md.split("-")[1:]).lstrip("0").replace("/0", "/")

    lines = [f"【AI朝刊 {md}】{a.get('headline','')}", ""]
    lines.append("スタンス:" + STANCE_JA.get(a.get("stance"), "中立"))

    if "nikkei" in d:
        lines.append(f"日経 {d['nikkei']['value']}({d['nikkei']['d1']}%)")
    if "fut" in d:
        lines.append(f"寄り付き想定 現物比{d['fut']['gap']}%")
    if "vix" in d:
        lines.append(f"VIX {d['vix']['value']}")

    secs = [s for s in a.get("sectors", []) if s.get("bias") == "up"][:2]
    if secs:
        lines.append("資金流入:" + "・".join(s.get("name", "").split("（")[0] for s in secs))
    if "event" in d:
        lines.append(f"{d['event']['when']}{d['event']['time']} {d['event']['name']}")

    lines += ["", "#日経平均 #株式投資"]
    body = "\n".join(lines)

    # 280(全角140)を超えたら後ろから削る
    while tweet_len(body) > 278 and len(lines) > 4:
        del lines[-3]
        body = "\n".join(lines)

    reply = f"詳しい分析・答え合わせ・セクター別の根拠はこちら\n{SITE_URL}"
    return body, reply


def main():
    # 引数: [analysis.json] [inputs.txt] [出力ディレクトリ]
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "analysis.json")
    inp = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "inputs.txt")
    outdir = sys.argv[3] if len(sys.argv) > 3 else HERE
    os.makedirs(outdir, exist_ok=True)

    with open(src, encoding="utf-8") as f:
        a = json.load(f)
    # docs/ai_analysis.json 形式(latest入り)にも対応する
    if "latest" in a and "headline" not in a:
        a = a["latest"]
    d = parse_inputs(inp)

    date_str = a.get("date") or datetime.now().strftime("%Y-%m-%d")
    body, reply = build_post(a, d, date_str)
    png = build_card(a, d, date_str, os.path.join(outdir, "x_card.png"))

    txt = os.path.join(outdir, "x_post.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("===== 本文(画像を添付) =====\n" + body +
                f"\n\n[{tweet_len(body)}/280]\n\n===== リプ(URL誘導) =====\n" + reply +
                f"\n\n[{tweet_len(reply)}/280]\n")

    print("=" * 46)
    print(body)
    print(f"--- {tweet_len(body)}/280 ---")
    print("=" * 46)
    print(reply)
    print(f"--- {tweet_len(reply)}/280 ---")
    print("=" * 46)
    print("OK:", txt)
    print("OK:", png)


if __name__ == "__main__":
    main()
