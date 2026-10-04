# -*- coding: utf-8 -*-
"""どのJSONが何営業日まで古くてよいか（鮮度の基準表・唯一の置き場）

■ 1か所で持つ理由
  同じ表を夜の監視（data-healthcheck.yml）とトップの表示（make_board.py）に
  別々に書くと、必ず片方だけ古くなる。トップが「正常」と言っている横で
  夜にIssueが立つ、という食い違いがいちばん困る。

■ 許容日数の意味
  0 = その日のうちに更新されていること。夜23:43の点検はその日の全ジョブが
      終わった後なので0でよい。祝日は誤検知しうるが、気づけないより軽い害。

■ トップのバッジには1営業日の猶予を足す（slack=1）
  トップは朝も昼も描画される。その時点ではまだ当日の夕方ジョブが走っていないので、
  0のまま判定すると毎朝「止まっています」と出てしまう。
  読者に見せる警告は「1営業日ぶん余計に遅れている」ときだけにする。
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

JST = timezone(timedelta(hours=9))

# (ファイル, 表示名, 許容遅れ営業日数)
TARGETS = [
    ("docs/heatmap.json",       "ヒートマップ/出来高", 0),
    ("docs/radar.json",         "地合い判定",         0),
    ("docs/ai_analysis.json",   "AI朝刊",             0),
    ("docs/score3.json",        "3軸スコア",          0),
    ("docs/cot.json",           "投機筋(COT)",        6),
    ("docs/investor_flow.json", "投資部門別",         6),
    ("docs/investor_hist.json", "投資部門別の履歴",   9),
    ("docs/tenbagger.json",     "十倍株スキャナー",   6),
    ("docs/karauri.json",       "空売り残高",         2),
    ("docs/kessan.json",        "決算カレンダー",     2),
    ("docs/gauge.json",         "暴落の傾斜計",       0),
    ("docs/crash.json",         "着火判定",           0),
    ("docs/shinyo.json",        "信用取引の需給",     6),
    ("docs/shinyo_meigara.json", "銘柄別信用残",      6),
    ("docs/ai_record.json",     "AI・地合いの成績表", 2),
    ("docs/sector_record.json", "セクター答え合わせ", 2),
    ("docs/buyback.json",       "自社株買い開示",     2),
    # 決算速報は平日に3回走る。決算が0件の日も updated は進むので当日更新を求める
    ("docs/kessan_flash.json",  "決算速報",           0),
    ("docs/board.json",         "トップの要約",       0),
    ("docs/karauri_score.json", "空売りの答え合わせ", 9),
    ("docs/cot_score.json",     "投機筋の答え合わせ", 9),
    ("docs/cot_scale.json",     "投機筋の10年の物差し", 9),
    ("docs/event_score.json",   "相場の暦",           40),
    ("docs/ai_weekly.json",     "週末版AI",           8),
    # ★2026-09-23 追加★ どちらの監視からも漏れていた。
    #   builder_example.json は 9/15 の初版のまま8日間動かず、誰も気づかなかった
    #   （更新ステップが一度も成功していない。GH_PAT が ruletrade-app を読めず404・
    #   引継ぎ.md §33）。「失敗したら止まる」は作ってあったが、
    #   **成功し続けているのに中身が動かない**を見ていなかった。
    #   ※ equity_curve.json はここに置かない。この検査は土日しか見ず祝日を知らないので、
    #     休場明けに必ず誤報が出る。市場を知っている check_freshness.py 側に置いた
    #     （portfolio_stats.json と必ず同じコミットで出るため）。
    # 月次。event_score は 40 だが、40 だと1回飛んでも約2か月気づけない
    # （9月の8日間より悪い）。毎月1日起動なので 25。
    ("docs/builder_example.json", "閾値を変えたら",   25),
    # ★2026-10-03 追加★ エンジン③（movers_daily.py・平日18:07 JST）。
    #   銘柄ページの値動き欄（エンジン①）の供給源なので当日更新必須。
    ("docs/prices.json",        "全銘柄の値動き",     0),
]

# トップのバッジには出さないもの。board.json 自身は「自分の鮮度」なので無意味。
BOARD_SKIP = {"docs/board.json"}


def bizdays_between(d1, d2):
    """d1→d2 の営業日数（土日を除く。祝日は考慮しない＝安全側に厳しめ）"""
    n, cur = 0, d1
    while cur < d2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def ref_date(now=None):
    """「その晩のチェック」としての基準日。

    23:43のcronがGitHub側の遅延で日付をまたぐと、金曜更新の日次データが
    月曜0時に「1営業日前」と誤検知される（2026-08-24 Issue #321）。
    7時間引いた時点の日付で評価する。
    """
    now = now or datetime.now(JST)
    return (now - timedelta(hours=7)).date()


def age_of(path, today):
    """更新からの営業日数。読めない・日付が無いときは None"""
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    # updated を優先し、無いときだけ asof を見る（夜の監視と同じ順序にする）
    upd = (d.get("updated") or (d.get("latest") or {}).get("updated")
           or d.get("asof") or "")
    try:
        return bizdays_between(datetime.fromisoformat(upd).date(), today)
    except Exception:
        return None


def stale_list(root=".", now=None, slack=0, skip=None):
    """許容を超えて古いものを返す。

    [{"f": "karauri.json", "label": "空売り残高", "age": 6, "tol": 2}, ...]
    古い順（＝いちばん困っているものが先頭）。
    """
    root = Path(root)
    today = ref_date(now)
    skip = BOARD_SKIP if skip is None else skip
    out = []
    for path, label, tol in TARGETS:
        if path in skip:
            continue
        age = age_of(root / path, today)
        if age is None or age <= tol + slack:
            continue
        out.append({"f": Path(path).name, "label": label, "age": age, "tol": tol})
    return sorted(out, key=lambda x: -(x["age"] - x["tol"]))
