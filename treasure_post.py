# -*- coding: utf-8 -*-
"""宝探し「今日の一枚」の X 投稿案（積上⑦）→ docs/x/treasure_post.txt ・ docs/x/treasure_card.png

投稿は本人が手で行う（ここは案を作るだけ）。文言は「映った」「たどる」だけを使い、
推奨・断定に読める言葉は使わない（BANNED で検査し、混ざっていたら止める）。

使い方: python -X utf8 treasure_post.py
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from make_x_post import BG, CARD, LINE, FG, SUB, ACCENT, FONT_B, FONT_R, font, tweet_len

HERE = Path(__file__).parent
DOCS = HERE / "docs"
XDIR = DOCS / "x"
URL = "https://kaburadar.jp/treasure.html"
ENT_JA = {"quiet": "静けさ", "contra": "逆張り", "tenbagger": "十倍株の新規", "volume": "出来高急増",
          "movers": "大きく動いた日"}
# gen_stocks.py の禁止語と同じ考え方。名詞（空売り・買残など）は先に除いてから見る
ALLOW = ["空売り", "買残", "売残", "買い残", "売り残"]
BANNED = ["買い", "売り", "推奨", "狙い目", "おすすめ", "割安", "割高", "チャンス", "有望", "仕込み"]


def check(text):
    t = text
    for a in ALLOW:
        t = t.replace(a, "")
    hit = [b for b in BANNED if b in t]
    if hit:
        raise SystemExit(f"::error::投稿案に使わない言葉が入った: {hit}")


def md(iso):
    return f"{int(iso[5:7])}/{int(iso[8:10])}"


def lines_of(card):
    out = []
    for k in card["from"]:
        for r in card["reasons"].get(k, []):
            out.append((ENT_JA[k], r))
    return out


def build_text(t):
    c, cnt = t["card"], t["counts"]
    head = f"🗺 今日の一枚（{md(t['trade_date'])}）\n{c['n']}（{c['c']}）"
    body = "\n".join(f"・{r}" for _, r in lines_of(c)[:3])
    tail = (f"\n\n今日は静けさに{cnt['quiet']}銘柄・逆張りに{cnt['contra']}銘柄が映りました"
            f"（重なり{cnt['overlap']}）。")
    s = head + "\n映った理由\n" + body + tail
    reply = f"映った銘柄の一覧と、銘柄ページへたどる道はこちら\n{URL}"
    check(s + reply)
    while tweet_len(s) > 270 and body.count("\n"):
        body = body.rsplit("\n", 1)[0]
        s = head + "\n映った理由\n" + body + tail
    return s, reply


def build_card(t, out):
    W, H = 1200, 675
    img = Image.new("RGB", (W, H), BG)
    dr = ImageDraw.Draw(img)
    c, cnt = t["card"], t["counts"]
    dr.text((56, 44), "株レーダー 宝探し", font=font(FONT_B, 30), fill=FG)
    dr.text((W - 56, 50), f"{t['trade_date']} の取引から", font=font(FONT_R, 24), fill=SUB, anchor="ra")
    dr.rounded_rectangle((44, 108, W - 44, 520), radius=22, fill=CARD, outline=LINE, width=2)
    dr.text((80, 132), "今日の一枚", font=font(FONT_B, 28), fill=ACCENT)
    name = c["n"] if len(c["n"]) <= 14 else c["n"][:13] + "…"
    dr.text((80, 178), name, font=font(FONT_B, 64), fill=FG)
    dr.text((80, 262), f"{c['c']}　{c.get('s') or ''}", font=font(FONT_R, 26), fill=SUB)
    y = 320
    fl, fr = font(FONT_B, 24), font(FONT_R, 28)
    for ent, r in lines_of(c)[:4]:
        bw = int(dr.textlength(ent, font=fl)) + 30
        dr.rounded_rectangle((80, y + 2, 80 + bw, y + 40), radius=12, outline=ACCENT, width=2)
        dr.text((95, y + 8), ent, font=fl, fill=ACCENT)
        dr.text((80 + bw + 20, y + 4), r if len(r) <= 30 else r[:29] + "…", font=fr, fill=FG)
        y += 48
    dr.text((56, 548), f"静けさ {cnt['quiet']}銘柄 ・ 逆張り {cnt['contra']}銘柄 ・ 重なり {cnt['overlap']}",
            font=font(FONT_B, 30), fill=FG)
    dr.text((56, 604), "条件に映った銘柄を並べたもので、売買を推奨するものではありません　kaburadar.jp",
            font=font(FONT_R, 21), fill=SUB)
    img.save(out)


def main():
    t = json.loads((DOCS / "treasure.json").read_text(encoding="utf-8"))
    if not t.get("card"):
        print("今日の一枚が無い（投稿案は作らない）")
        return 0
    XDIR.mkdir(parents=True, exist_ok=True)
    s, reply = build_text(t)
    (XDIR / "treasure_post.txt").write_text(
        f"【本文】（{tweet_len(s)}/280）\n{s}\n\n【リプ】\n{reply}\n", encoding="utf-8")
    build_card(t, XDIR / "treasure_card.png")
    print(f"投稿案を出力: {t['card']['n']}（{t['card']['c']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
