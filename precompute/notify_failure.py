# -*- coding: utf-8 -*-
"""前計算（precompute-daily.yml）が失敗したら、本人にメールを1通送る。

★なぜ要るか★（2026-10-16）
  2026-10-13〜10-16 の4日間、このジョブは毎日失敗して公開数字が止まっていたのに、
  誰も気づかなかった。GitHub の通知は他の通知に埋もれる。失敗したら、本人の受信箱に直接届ける。
  公開サイト側（ruletrade-site の sync-stats.yml・_tools/notify_failure.py）と同じ形にしてある。

★正常な日は1通も送らない★
  毎日「正常でした」が届くと、人はすぐ読まなくなる。読まれない通知は、無い通知と同じ。
  「1日1通まで」はワークフロー側（actions/cache の日付つきの印）で守る。ここは送るだけ。

★送り方はこのリポジトリの既存の仕組みに相乗りする★
  サイト側は Resend だが、このリポジトリの Secrets に Resend の鍵は無い。
  ザラ場通知（realtime_notifier.py）が使っている Gmail SMTP と、同じ Secrets 名を使う。

★試しに送れる★ 手動実行で alert_test を選ぶと、失敗していなくても1通送る。
  「届くはず」と推測で済ませない。本人の受信箱に届くのを見て確かめる。

★鍵とメールアドレスは出さない★ ログに出すのは、送れたか・送れなかった理由だけ。

環境変数（ワークフローから渡す）:
  GMAIL_USER / GMAIL_APP_PASSWORD … GitHub Secrets（realtime-signal.yml と同じもの）
  NOTIFY_TO   … GitHub Secrets（宛先。未設定なら GMAIL_USER に送る＝realtime_notifier と同じ）
  STEPS_JSON  … ${{ toJSON(steps) }}（どの段が失敗したか）
  RUN_URL     … 実行ログの URL
  ALERT_TEST  … 手動実行でテストを選んだら "true"
"""
from __future__ import annotations

import datetime
import json
import os
import smtplib
import ssl
import sys
from email.header import Header
from email.mime.text import MIMEText

# ワークフローの段の id → 本人が読んで分かる名前と、まず見るところ
STEP_INFO = {
    "guard": ("予備実行の判定", "check_freshness.py が例外で落ちていないか"),
    "metrics": ("株価の取得と前計算（daily_metrics）",
                "「行未満の銘柄」「取得できませんでした」「正本に足すのを止めました」のどれか"),
    "rebuild": ("規定値ポートフォリオの計算", "正本（daily_metrics）を読めているか"),
    "publish": ("公開数字の検査と書き出し",
                "「前回と違います」の行。公開数字は前回のまま残っています"),
    "score3": ("3軸スコアの縮約版", "列が無い・upsert が 400 で落ちていないか"),
    "warm": ("型と規定値のキャッシュ温め", "公開数字には影響しません"),
}


def build_message(steps: dict, run_url: str, test: bool, today: str):
    """(mode, 件名, 本文) を返す。送信はしない（試験から呼べるように分けてある）。"""
    failed = [sid for sid, s in (steps or {}).items() if (s or {}).get("outcome") == "failure"]
    # 失敗した段があれば失敗の知らせ。無くて、手動でテストを選んだときだけテスト。
    # どちらでもない（id の無い段＝checkout などで落ちた）ときも、失敗として送る（黙らない）。
    mode = "test" if (not failed and test) else "failure"

    if mode == "test":
        subject = "[ルールトレード] 前計算の失敗通知のテスト（%s）" % today
        lines = ["これはテストです。前計算（precompute-daily）の失敗通知が、この宛先に届くことを確かめるためのメールです。",
                 "失敗したときは、同じ差出人からこの形で届きます。"]
    else:
        subject = "[ルールトレード] 前計算（公開数字の元）が失敗しました（%s）" % today
        lines = ["毎日の前計算（GitHub Actions の precompute-daily）が失敗しました。",
                 "このままだと、サイトの公開数字（portfolio_stats.json）が前回のまま止まります。"]
    lines.append("")
    if failed:
        lines.append("失敗した段:")
        for sid in failed:
            name, hint = STEP_INFO.get(sid, (sid, ""))
            lines.append("  ・%s　→ まず見るところ: %s" % (name, hint) if hint else "  ・%s" % name)
    elif mode != "test":
        lines.append("失敗した段を特定できませんでした。ログを見てください。")
    lines += ["", "実行ログ:", "  " + run_url, "",
              "対応は stock-trading（データ作成側）の実務チャットへ。",
              "",
              "※ このメールは失敗した日だけ、1日1通まで出ます。同じ日に何度失敗しても2通目は出ません。",
              "※ 正常な日は何も送りません。"]
    return mode, subject, "\n".join(lines)


def main():
    user = os.environ.get("GMAIL_USER", "").strip()
    pw = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    to = (os.environ.get("NOTIFY_TO", "") or user).strip()
    if not user or not pw:
        # ★黙らない★ 警告を出す仕組みそのものが壊れているのが、いちばん困る形
        missing = "・".join(n for n, v in (("GMAIL_USER", user), ("GMAIL_APP_PASSWORD", pw)) if not v)
        print("::error::%s が未設定のため、本人へのメールを送れません。"
              "gh secret set で登録してください。" % missing)
        return 1
    # ★宛先の形を、値を出さずに確かめる★ どこが違うかだけ言う
    probs = []
    if to.count("@") != 1:
        probs.append("半角の @ が%s" % ("ありません" if "@" not in to else "2つ以上あります"))
    if any(ord(ch) > 127 for ch in to):
        probs.append("全角の文字が混ざっています")
    print("宛先の形: %d 文字%s" % (len(to), ("・" + "／".join(probs)) if probs else "・問題なし"))

    try:
        steps = json.loads(os.environ.get("STEPS_JSON") or "{}")
    except ValueError:
        steps = {}
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d")
    mode, subject, text = build_message(steps, os.environ.get("RUN_URL", "(不明)"),
                                        os.environ.get("ALERT_TEST") == "true", today)

    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = to
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as smtp:
            smtp.login(user, pw)
            smtp.sendmail(user, [to], msg.as_string())
        print("✓ 本人へメールを送りました（%s）" % ("テスト" if mode == "test" else "失敗の知らせ"))
        return 0
    except smtplib.SMTPAuthenticationError as e:
        # 理由のコードだけ出す。鍵・宛先は出さない
        print("::error::本人へのメールを送れませんでした（Gmail の認証で拒否 %s）。"
              "GMAIL_APP_PASSWORD が失効していないか確かめてください" % e.smtp_code)
        return 1
    except Exception as e:
        print("::error::本人へのメールを送れませんでした（%s）" % type(e).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
