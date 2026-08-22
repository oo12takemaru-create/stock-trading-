# -*- coding: utf-8 -*-
"""歴代暴落の再現データ → docs/crash_replay.json（株レーダー「あの日の株レーダー」用）

crash_fetch.py の7フラグをそのまま過去の有名な急落局面に当てはめ、
「本震の前日、着火メーターは何点灯だったか」を日次で再現する。

■ これはバックテストの可視化である（ページにも明記する）
  着火メーターは2026年に作った道具で、当時は存在しない。
  「当時この画面があったらこう見えていた」という過去データへの当てはめ。

■ 歴史的事実は変わらないので定期実行しない。
  フラグ定義を変えたときだけ手で再実行してコミットする。
  使い方: python crash_replay.py
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np

from crash_fetch import load, features, flag_matrix, stage_of, FLAGS

JST = timezone(timedelta(hours=9))
OUT = Path(__file__).parent / "docs" / "crash_replay.json"

# (id, 名前, 表示ウィンドウ開始, 終了, 一言(事実のみ))
EPISODES = [
    ("aug2024",  "2024年8月 円キャリー巻き戻し", "2024-06-24", "2024-09-30",
     "8/5に日経平均が1日で-12.4%。ブラックマンデー超えの下げ幅を記録した急落"),
    ("corona",   "2020年 コロナショック",        "2020-01-06", "2020-05-29",
     "感染拡大で世界同時株安。約1ヶ月で日経平均は3割下落した"),
    ("dec2018",  "2018年 クリスマス急落",        "2018-09-03", "2019-01-31",
     "米利上げと貿易摩擦への懸念で年末にかけて世界株安"),
    ("china2015","2015年 チャイナショック",      "2015-06-01", "2015-10-30",
     "人民元切り下げをきっかけに8月後半に急落"),
    ("tohoku",   "2011年 東日本大震災",          "2011-01-04", "2011-06-30",
     "3/11の震災直後、日経平均は2営業日で-16%超"),
    ("lehman",   "2008年 リーマンショック",      "2008-06-02", "2009-03-31",
     "9/15のリーマン破綻から10月にかけて歴史的な暴落が連鎖した"),
]


def main():
    px = load()
    f = features(px)
    F = flag_matrix(f)
    score = F.sum(axis=1, min_count=len(FLAGS))
    n = px["n225"]
    ret1 = n.pct_change()

    episodes = []
    for eid, name, d0, d1, desc in EPISODES:
        w = (px.index >= d0) & (px.index <= d1)
        if w.sum() < 30:
            print(f"スキップ {eid}: データ不足", file=sys.stderr)
            continue
        idx = px.index[w]
        days = []
        for d in idx:
            s = score.loc[d]
            days.append({
                "d": d.strftime("%Y-%m-%d"),
                "n": round(float(n.loc[d]), 1),
                "s": None if np.isnan(s) else int(s),
            })
        # ウィンドウ内の事実を機械的に計算（手書きの数字は入れない）
        seg = n[w]
        peak_d = seg.idxmax(); bot_d = seg[seg.index >= peak_d].idxmin()
        maxdd = float(n.loc[bot_d] / n.loc[peak_d] - 1)
        r = ret1[w].dropna()
        worst_d = r.idxmin()
        # 本震前日のスコア（前営業日 = メーターがその晩に見せていた値）
        pos = px.index.get_loc(worst_d)
        prev_d = px.index[pos - 1]
        prev_s = score.loc[prev_d]
        # 本震までの直近10営業日で警戒(5)以上だった日数
        last10 = score.loc[:prev_d].tail(10)
        warn_days = int((last10 >= 5).sum())
        episodes.append({
            "id": eid, "name": name, "desc": desc,
            "days": days,
            "peak": {"d": peak_d.strftime("%Y-%m-%d"), "n": round(float(n.loc[peak_d]), 1)},
            "bottom": {"d": bot_d.strftime("%Y-%m-%d"), "n": round(float(n.loc[bot_d]), 1)},
            "maxdd": round(maxdd * 100, 1),
            "worst": {"d": worst_d.strftime("%Y-%m-%d"),
                      "pct": round(float(r.loc[worst_d]) * 100, 1),
                      "prev_score": None if np.isnan(prev_s) else int(prev_s),
                      "prev_d": prev_d.strftime("%Y-%m-%d")},
            "warn_days_before": warn_days,
        })

    # 歴代の1日下落率ランキング（スコアが計算できる期間のみ＝前日スコアを正直に出せる日だけ）
    ok = score.notna()
    ranks = []
    for d, v in ret1[ok].nsmallest(15).items():
        pos = px.index.get_loc(d)
        prev_s = score.iloc[pos - 1] if pos > 0 else np.nan
        ranks.append({
            "d": d.strftime("%Y-%m-%d"),
            "pct": round(float(v) * 100, 1),
            "n": round(float(n.loc[d]), 1),
            "prev_score": None if np.isnan(prev_s) else int(prev_s),
        })

    data = {
        "generated": datetime.now(JST).isoformat(timespec="seconds"),
        "period": f"{px.index[ok][0]:%Y}年〜{px.index[-1]:%Y}年",
        "flag_total": len(FLAGS),
        "note": "着火メーターは2026年に設計した基準を過去データに当てはめた再現（バックテスト）です",
        "episodes": episodes,
        "worst_days": ranks,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"OK {OUT.name}: {len(episodes)}エピソード / ランキング{len(ranks)}日 "
          f"({OUT.stat().st_size//1024}KB)")
    for e in episodes:
        w = e["worst"]
        print(f"  {e['name']}: 最大DD {e['maxdd']}% / 本震{w['d']} {w['pct']}% "
              f"(前日スコア {w['prev_score']}/{len(FLAGS)}) / 直近10日の警戒以上 {e['warn_days_before']}日")


if __name__ == "__main__":
    main()
