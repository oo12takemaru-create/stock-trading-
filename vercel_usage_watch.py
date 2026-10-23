"""Vercel の関数の CPU（Fluid Active CPU）を日次で見張る（2026-10-23 故障対応）。

10/23 に kaburadar の OG 画像の関数（@vercel/og）が無料枠4時間の300%を使い、チーム全体が停止した。
同じことに気づかないまま枠を使い切らないよう、月の累計が 3・6・9・12 時間の段階を初めて超えた日に ntfy で1通知らせる。

数字の取り方: Vercel CLI の `vercel metrics vercel.function_invocation.function_cpu_time_ms`
（ダッシュボードの Functions「Active CPU」と同じ指標・ミリ秒）を、チームの全プロジェクトで日ごとに合計する。
必要なもの: secrets.VERCEL_TOKEN（チームのトークン）。無ければ警告だけ出して終わる（止めない）。

月の区切りは UTC の暦月（Vercel の請求期間の始まりと数日ずれることがある）。
通知は「段階を越えた時だけ」（Fable 判断 10/23）: 月の累計が 3・6・9・12 時間を初めて超えた日に1通。
越えていない日は静か。月が変われば最初の段階からやり直す。
"""
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

TEAM = "team_w3H2B1scUBhauPvE1drtRVpA"
KABURADAR = "prj_I4o2RbQQ0eRDeyH0hYBHPRrzxVG0"
# 表示用の名前（2026-10-23 時点。知らないIDはそのまま出す）
NAMES = {KABURADAR: "kaburadar", "prj_d8nps0uusUkEZunQ91O3njAnYUB3": "tsukuhiro",
         "prj_oTuRtIehrCUjA6qnYA5XneXbrRRy": "thisy", "prj_H1jXuHlC25XaKmVBQEX5pqwsTxXe": "ruletrade-app",
         "prj_fX2T3ENO3xPSKxokb0pWH4ezlh2Y": "raiden-dashboard", "prj_kkoDVMLDHpgAlrRAw6xrxnoMhYC9": "showcase",
         "prj_rCxIfomyFRCW6Hd1UOX3Lt2zmEpQ": "bar-akari", "prj_LyovDuUidliVPGtAdeEfWWFIWNIO": "rule-based"}
# 段階（時間）。月の累計がこの段階を初めて超えた日に1通だけ送る。越えていない日は送らない
STEPS_H = [3, 6, 9, 12]
# 送った段階を覚えておくファイル（ワークフローが Actions のキャッシュで回をまたいで持つ）。月が変われば読み捨てる
STATE = os.environ.get("USAGE_STATE", ".vercel-usage-state.json")
KEY = "vercel_function_invocation_function_cpu_time_ms_sum"


def metrics(since, until):
    cmd = [os.environ.get("VERCEL_BIN", "npx"), *([] if os.environ.get("VERCEL_BIN") else ["--yes", "vercel@54"]), "metrics", "vercel.function_invocation.function_cpu_time_ms",
           "-a", "sum", "--all", "--group-by", "project_id", "--limit", "50", "-g", "1d",
           "--since", since, "--until", until, "-F", "json",
           "--scope", TEAM, "--non-interactive"]
    if os.environ.get("VERCEL_TOKEN"):
        cmd += ["--token", os.environ["VERCEL_TOKEN"]]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        raise RuntimeError(out.stderr[-1500:])
    s = out.stdout
    return json.loads(s[s.index("{"):])


def hm(ms):
    m = ms / 60000
    return f"{int(m // 60)}時間{int(m % 60)}分" if m >= 60 else f"{m:.1f}分"


def ntfy(title, body):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("::warning::NTFY_TOPIC 未設定のため通知できません")
        return
    urllib.request.urlopen(urllib.request.Request(
        "https://ntfy.sh",
        data=json.dumps({"topic": topic, "title": title, "message": body, "priority": 4,
                         "tags": ["warning"], "click": "https://vercel.com/dashboard"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}), timeout=30)
    print("ntfy送信OK:", title)


def main():
    # VERCEL_LOCAL=1 は手元で試すとき（vercel login 済みの認証を使う）
    if not os.environ.get("VERCEL_TOKEN") and not os.environ.get("VERCEL_LOCAL"):
        print("::warning::VERCEL_TOKEN が未設定です。リポジトリの Secrets に入れるまで Vercel の使用量は見張れません")
        return 0
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    d = metrics(start.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    total = {r["project_id"]: r.get(KEY) or 0 for r in d.get("summary", [])}
    days = {}
    for r in d.get("data", []):
        days.setdefault(r["timestamp"][:10], {})[r["project_id"]] = r.get(KEY) or 0
    team_ms = sum(total.values())
    y = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    lines = [f"## Vercel 関数の CPU（Active CPU）{start:%Y-%m} 月の累計（{now:%m/%d %H:%M} UTC 時点）", "",
             f"- チーム合計: **{hm(team_ms)}**（知らせる段階 {'・'.join(map(str, STEPS_H))}時間）"]
    for pid, ms in sorted(total.items(), key=lambda x: -x[1]):
        lines.append(f"- {NAMES.get(pid, pid)}: {hm(ms)}（昨日 {hm(days.get(y, {}).get(pid, 0))}）")
    lines += ["", "| 日(UTC) | kaburadar | チーム合計 |", "|---|---|---|"]
    for k in sorted(days):
        lines.append(f"| {k} | {hm(days[k].get(KABURADAR, 0))} | {hm(sum(days[k].values()))} |")
    rep = "\n".join(lines)
    print(rep)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8").write(rep + "\n")

    month = f"{start:%Y-%m}"
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        st = {}
    sent = st.get("sent", 0) if st.get("month") == month else 0
    passed = [h for h in STEPS_H if team_ms > h * 3600_000]
    top = max(passed) if passed else 0
    test = bool(os.environ.get("TEST_NOTIFY"))
    if top > sent or test:
        ntfy(("【テスト】" if test else "")
             + f"⚠️ Vercel の関数CPUが今月 {hm(team_ms)}（{top or STEPS_H[0]}時間の段階を超えた）",
             f"kaburadar {hm(total.get(KABURADAR, 0))}・昨日 {hm(days.get(y, {}).get(KABURADAR, 0))}\n"
             f"次に知らせるのは {next((h for h in STEPS_H if h > top), '—')} 時間を超えたとき\n"
             "Vercel のダッシュボード → Usage → Fluid Active CPU を確認してください")
    else:
        print(f"通知なし（今月すでに {sent}時間の段階まで知らせ済み・いまの段階 {top}時間）")
    if not test:
        json.dump({"month": month, "sent": max(sent, top)}, open(STATE, "w", encoding="utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
