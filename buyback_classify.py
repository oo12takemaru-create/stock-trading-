# -*- coding: utf-8 -*-
"""自社株買い開示の表題分類（Actions側。Worker側 src/classify.js と同じ結果を出す）

**片方だけ直すとズレる。**変更したら必ず両方直し、
tests/classify_cases.json（TDnetの実表題）で両方をテストすること:
    python -X utf8 tests/test_classify.py      # こちら
    node tests/run.mjs                          # Worker側（kaburadar-buyback）

■ 実データ（2026-07-30〜09-04・自己株を含む1,575件）から決めた優先順位
  1. 変更・訂正（自己株に係るものだけ。「業績予想の修正…および自己株式取得の決定」は決議）
  2. 処分・譲渡制限付株式など → other（自己株式を放出する側。自社株買いではない・455件）
  3. 消却
  4. 完了（取得結果・取得終了・取得完了）… 進捗より重い
  5. 進捗報告（毎月出る。件数が多い）
  6. 決議・決定
  7. ToSTNeT-3 の買付け
"""
import re
import unicodedata

# 判定から外す但し書き。括弧内は根拠条文の説明が多く、種別の判断に使わない
PAREN = re.compile(r"[（(][^（()）]*[)）]")


def normalize(title):
    """全角→半角、空白除去、括弧内の但し書きを落とす"""
    t = unicodedata.normalize("NFKC", title or "")
    t = PAREN.sub("", t)
    t = re.sub(r"\s+", "", t)
    return t


TOSTNET = re.compile(r"立会外買付|ToSTNeT|TOSTNET", re.I)


def classify(title):
    """(type, tostnet) を返す。判定は上から順に倒す"""
    import unicodedata as _u
    full = re.sub(r"\s+", "", _u.normalize("NFKC", title or ""))  # 括弧を残した形
    t = normalize(title)
    tos = bool(TOSTNET.search(t))
    # 変更・訂正の判定は「その語が自己株の話に係っているか」で決める。
    # 「業績予想の修正…および自己株式取得に係る事項の決定」は決議であって変更ではない。
    # (a)（訂正）や（開示事項の変更）で始まるものは、元の開示の差し替えなので change
    # (b) 本文中なら「自己株」の近く（前後14字）に変更語があるときだけ change
    CHG = r"訂正|変更|修正|中止|取りやめ|取止め"
    if "自己株" in t and not re.search(r"処分|譲渡制限付|株式給付信託", t):
        if re.match(r"^[（(]?(訂正|開示事項の変更|開示事項の一部変更)[)）]?", full):
            return "change", tos
        # 変更語は「自己株」より後ろにあり、間に読点・鉤括弧が入らないものだけ採る。
        # 「配当予想の修正および自己株式取得に係る事項の決定」を拾わないため
        if re.search(r"自己株[^、。「」]{0,22}(" + CHG + ")", t)            or re.search(r"取得枠[^、。]{0,8}(" + CHG + ")", t):
            return "change", tos

    if "自己株" not in t:
        return "other", tos

    # 1. 処分・譲渡は自社株買いではない（自己株式を放出する側）。最優先で外す
    if re.search(r"処分|譲渡制限付|株式給付信託|ストックオプション|新株予約権", t):
        return "other", tos

    # 2. 消却
    if "消却" in t:
        return "cancel", tos

    # 3. 完了（取得結果・取得終了・買付け結果）。進捗より重いので先に見る
    # 「取得状況及び終了」「取得状況および取得完了」も完了。語彙が足りず進捗に落ちていた
    if re.search(r"取得結果|買付.?結果|取得終了|取得の終了|買付け.?終了|取得完了|取得の完了|"
                 r"取得状況(?:及び|および|並びに|ならびに)?(?:取得)?(?:終了|完了)", t):
        return "complete", tos

    # 4. 変更・修正・中止

    # 5. 進捗報告（毎月出る。件数が多いのでここで分ける）
    if "取得状況" in t:
        return "progress", tos

    # 6. 決議・決定
    if re.search(r"決定|決議", t):
        return "decision", tos

    # 7. ToSTNeT-3 の買付け（決議と別に告知されるもの）
    if tos and re.search(r"買付|取得", t):
        return "tostnet3", tos

    return "other", tos
