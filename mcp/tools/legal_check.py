# -*- coding: utf-8 -*-
"""生成した JSON に「出してはいけない語」が残っていないかを build の時点で止める。

■ なぜ build で止めるか
サーバー側の `legal.js` が推奨語を伏せ字（［表現調整］）に置き換えるので、
**そのまま出しても法務の線は守られる**。問題はそこではなく、

    「各買い時点の日経平均の単純平均」
  → 「各［表現調整］点の日経平均の単純平均」

のように **意味が壊れた文がエージェントに届く** こと。伏せ字は最後の砦であって、
そこに頼る設計にしてはいけない。書籍の文章をそのまま取り込む build では
特に起きやすいので、**JSON を作った時点で気づけるようにする**。

■ NG語は `src/legal.js` から読む
Python 側に書き写すと二重管理になり、片方だけ増えて検査が素通りする。

■ 使い方

    from legal_check import assert_clean
    assert_clean(doc)          # 引っかかったら例外。build が止まる
"""
from __future__ import annotations

import io
import os
import re

LEGAL_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "legal.js")


def ng_words():
    """`src/legal.js` の NG_WORDS をそのまま読む。"""
    s = io.open(LEGAL_JS, encoding="utf-8").read()
    m = re.search(r"export const NG_WORDS\s*=\s*\[(.*?)\]", s, re.S)
    if not m:
        raise RuntimeError("legal.js の NG_WORDS を読み取れません（形が変わった?）")
    return [w for w in re.findall(r'"([^"]+)"', m.group(1))]


def walk_strings(v, path=""):
    """JSON の文字列の値とキーだけを集める。★数値の中は見ない★

    p値 0.0045 のような小数を文字列にして探すと、小数部が銘柄コードに見える。
    """
    if isinstance(v, str):
        yield path, v
    elif isinstance(v, dict):
        for k, x in v.items():
            yield path + "/" + str(k), str(k)
            yield from walk_strings(x, path + "/" + str(k))
    elif isinstance(v, list):
        for i, x in enumerate(v):
            yield from walk_strings(x, "%s[%d]" % (path, i))


def find_ng(doc, extra=()):
    """(NG語, 場所, 本文) の一覧を返す。"""
    words = ng_words() + list(extra)
    hits = []
    for path, text in walk_strings(doc):
        for w in words:
            if w.lower() in text.lower():
                hits.append((w, path, text))
    return hits


def assert_clean(doc, extra=()):
    """1つでも見つかったら例外。build を止める。"""
    hits = find_ng(doc, extra)
    if not hits:
        return
    lines = ["出してはいけない語が JSON に残っています（%d 件）。" % len(hits),
             "※ サーバー側の scrub で伏せ字になりますが、文の意味が壊れます。",
             "   言い換えるか、その項目を落としてください。"]
    for w, path, text in hits[:10]:
        lines.append("  [%s] %s" % (w, path))
        lines.append("      %s" % (text[:110] + ("…" if len(text) > 110 else "")))
    raise SystemExit("\n".join(lines))
