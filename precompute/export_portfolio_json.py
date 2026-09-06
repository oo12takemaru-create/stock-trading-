# -*- coding: utf-8 -*-
"""規定値ポートフォリオの成績を docs/portfolio_stats.json に書き出す。

■ 何のためか（引継ぎ.md §19・起動文 2-4c・2026-09-06 Fable）
静的サイト（ruletrade.jp）が公開数字を読むための1枚。
**会員アプリの /dashboard 右カラムと同じ値になること**が要件なので、
どちらも `portfolio_results` 表の同じ行を出どころにする。
静的サイト側の読み込みは改装セッションが担当する。

  出力先: stock-trading- の docs/portfolio_stats.json
          （GitHub Pages で https://oo12takemaru-create.github.io/stock-trading-/portfolio_stats.json）

■ 出力スキーマ（2026-09-06 Fable 相談⑤で確定）
`起動文_実務_サイト改装_主役シグナル_2026-09-05.md` §12 の形。サイトの `assets/stats.js` が
このキー名で読む。**キー名・単位を変えるときは §12 とサイト側を同時に直すこと。**
  ・% は数値で持つ（52.4）。表示側で「%」を付ける
  ・負の値は負号で持つ（-24.2）。表示側で「−」に変換
  ・`asof` / `version` / `basis` を必ず含める
  ・**口座残高・株数・投入額・銘柄別の売買ラインは入れない**（公開rawに置くため）
    → validate() が機械で止める

■ 「一つの定義・一つの数字」を守るために
サイトに出す数字をこのファイル以外から作らないこと。
値を変えたいときは `portfolio_engine.py` を直して再計算し、ここを出し直す。
（2026-09-06: 集客サイト企画側の `_precompute/build_portfolio_stats.py` は使わない。
  生成はこのスクリプトに一本化する。）

    python precompute/export_portfolio_json.py --env-file ../ruletrade-app/.env.local
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import supabase_io  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(REPO_ROOT, "docs", "portfolio_stats.json")
RESULT_ID = "preset_v2_8_0"

SCHEMA_VERSION = 1

# 出口理由の日本語ラベル → 公開スキーマのキー（§12）。
# 新しい出口理由が増えたらここに足す。足し忘れは build() が落として気づかせる。
EXIT_KEYS = {
    "保有期限": "time_limit",
    "損切り": "stop",
    "25日MA戻り": "ma25_return",
    "タイムストップ": "time_stop",
    "+10%利確": "tp10",
    "半分利確": "half_tp",
    "期末強制決済": "forced",
    "50EMA下抜け": "ema50_break",
}

# 旧い Supabase 行（basis に機械可読キーが無い）を読むための対応表。
# portfolio_run.py に *_key を足したので、次のバッチ以降は使われない。
# **知らない文言が来たら落とす**（黙って違う basis を公開しないため）。
LEGACY_REGIME = {"日次スキャナ版": "scanner"}
LEGACY_EXEC = {"判定・約定とも終値（当日終値で条件成立→翌営業日の終値で約定）": "close"}
LEGACY_STOP = {"安値が水準に触れた日の終値": "low_touch_close"}

# 公開してはいけないキー（口座規模や個別の売買ラインが逆算できるもの）。完全一致で見る。
FORBIDDEN_KEYS = frozenset({
    "capital", "final_capital", "total_pnl", "balance", "equity",
    "shares", "amount", "position_size", "avg_pnl_yen", "median_pnl_yen",
    "code", "ticker", "tickers", "name", "symbol",
    "entry_price", "exit_price", "stop_price", "target_price",
})


def log(msg):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def r(v, nd=2):
    """小数の桁を落とす。None はそのまま返す。"""
    if v is None:
        return None
    return round(float(v), nd)


def _date_only(v):
    """'2026-09-06T06:03:34.226387+00:00' でも '2026-09-06' でも YYYY-MM-DD にする。"""
    return str(v)[:10]


def _basis_value(basis, key, legacy_map, legacy_field, label):
    """機械可読キーがあればそれを、無ければ日本語文言から引く。引けなければ落とす。"""
    if basis.get(key) is not None:
        return basis[key]
    text = basis.get(legacy_field)
    if text in legacy_map:
        return legacy_map[text]
    raise SystemExit(
        "basis の%sを判定できません: %r\n"
        "  portfolio_run.py で %s を出すようにするか、"
        "このスクリプトの対応表に追加してください。" % (label, text, key)
    )


def _universe_total(basis):
    """ユニバース総数。機械可読キーが無い旧行は文言から数字を拾う。"""
    if basis.get("universe_total") is not None:
        return int(basis["universe_total"])
    m = re.search(r"(\d+)\s*銘柄", str(basis.get("universe", "")))
    if m:
        return int(m.group(1))
    raise SystemExit(
        "basis からユニバース総数を取れません: %r\n"
        "  portfolio_run.py で universe_total を出すようにしてください。"
        % basis.get("universe")
    )


def build(row: dict) -> dict:
    result = row["result"]
    s = result["summary"]
    basis = result["basis"]
    period = result["period"]

    exits = {}
    for label, count in (result.get("exit_reasons") or {}).items():
        key = EXIT_KEYS.get(label)
        if key is None:
            raise SystemExit(
                "未知の出口理由『%s』。EXIT_KEYS に追加してください（§12 のキー名で）。" % label
            )
        exits[key] = int(count)
    exits = dict(sorted(exits.items(), key=lambda kv: -kv[1]))

    start = _date_only(period["from"])
    end = _date_only(period["to"])
    years = int(round(
        (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days / 365.25
    ))

    avg_win = s.get("avg_win")
    avg_loss = s.get("avg_loss")
    rr = abs(float(avg_win) / float(avg_loss)) if avg_win and avg_loss else None

    max_dd = s.get("max_dd")
    # 最大DDは負号で持つ（§12）。engine が絶対値で返しても符号を揃える。
    max_dd_pct = r(-abs(float(max_dd)), 1) if max_dd is not None else None

    return {
        "schema": SCHEMA_VERSION,
        # いつ時点の数字か。サイトは asof が静的値より古い JSON を無視する（§12 段階2）
        "asof": _date_only(row["computed_at"]),
        "version": basis.get("rule_version") or "v2.8.0",

        "period_start": start,
        "period_end": end,
        "years": years,
        "universe": _universe_total(basis),
        "universe_effective": basis.get("tickers_used"),

        "trades": s.get("trades"),
        "wins": s.get("wins"),
        "losses": s.get("losses"),
        "win_rate": r(s.get("win_rate"), 1),
        "pf": r(s.get("pf"), 2),
        "avg_pnl_pct": r(s.get("avg_return"), 2),
        "median_pnl_pct": r(s.get("median_return"), 2),
        "avg_win_pct": r(avg_win, 2),
        "avg_loss_pct": r(avg_loss, 2),
        "rr": r(rr, 2),
        "max_dd_pct": max_dd_pct,
        "cum_return_pct": r(s.get("total_return"), 1),
        "cagr_pct": r(s.get("cagr"), 1),
        "avg_hold_days": r(s.get("avg_hold_days"), 1),
        "max_losing_streak": s.get("max_losing_streak"),

        "exits": exits,

        "basis": {
            "regime": _basis_value(basis, "regime_key", LEGACY_REGIME, "regime", "相場環境の判定"),
            "exec": _basis_value(basis, "exec_key", LEGACY_EXEC, "price", "約定価格"),
            "stop": _basis_value(basis, "stop_key", LEGACY_STOP, "stop", "損切りの判定"),
            "max_positions": basis.get("max_positions", 10),
            "risk_pct": basis.get("risk_pct", 1),
            "compounding": basis.get("compounding", True),
            "costs": basis.get("costs", False),
        },
    }


def validate(d) -> list:
    """公開前の自己点検。1件でも引っかかったら書き出さない。"""
    errs = []

    required = [
        "schema", "asof", "version", "period_start", "period_end", "years",
        "universe", "universe_effective", "trades", "wins", "losses", "win_rate",
        "pf", "avg_pnl_pct", "median_pnl_pct", "avg_win_pct", "avg_loss_pct", "rr",
        "max_dd_pct", "cum_return_pct", "cagr_pct", "avg_hold_days",
        "max_losing_streak", "exits", "basis",
    ]
    for k in required:
        if d.get(k) is None:
            errs.append("必須キーが無い/None: %s" % k)

    # 公開rawなので、口座規模や銘柄が漏れていないことを機械で確認する
    def scan(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in FORBIDDEN_KEYS:
                    errs.append("公開してはいけないキーが混じっている: %s%s" % (path, k))
                scan(v, "%s%s." % (path, k))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                scan(v, "%s[%d]." % (path, i))
        elif isinstance(node, str):
            if re.fullmatch(r"[0-9][0-9A-Za-z]{3}(\.T)?", node):
                errs.append("銘柄コードらしき値がある: %s = %s" % (path[:-1], node))

    scan(d)

    if d.get("wins") is not None and d.get("losses") is not None:
        if d["wins"] + d["losses"] != d.get("trades"):
            errs.append("wins + losses が trades と合わない")
    if d.get("exits") and sum(d["exits"].values()) != d.get("trades"):
        errs.append("exits の合計 %d が trades %s と合わない"
                    % (sum(d["exits"].values()), d.get("trades")))
    if d.get("max_dd_pct") is not None and d["max_dd_pct"] >= 0:
        errs.append("max_dd_pct は負の値で持つ（§12）")
    if d.get("avg_loss_pct") is not None and d["avg_loss_pct"] >= 0:
        errs.append("avg_loss_pct は負の値で持つ（§12）")
    if d.get("win_rate") is not None and not 0 < d["win_rate"] < 100:
        errs.append("win_rate は %% を数値で持つ（0〜100）")
    if d.get("universe") and d.get("universe_effective"):
        if d["universe_effective"] > d["universe"]:
            errs.append("universe_effective が universe を超えている")
    for k in ("asof", "period_start", "period_end"):
        try:
            dt.date.fromisoformat(d.get(k, ""))
        except (ValueError, TypeError):
            errs.append("%s が YYYY-MM-DD でない: %r" % (k, d.get(k)))
    if d.get("asof", "") < d.get("period_end", ""):
        errs.append("asof が period_end より古い（サイト側が JSON を無視する）")
    for k in ("regime", "exec", "stop"):
        if not d.get("basis", {}).get(k):
            errs.append("basis.%s が空" % k)
    return errs


def main():
    p = argparse.ArgumentParser(description="公開数字を docs/portfolio_stats.json に出す")
    p.add_argument("--env-file", default="")
    p.add_argument("--out", default=OUT_PATH)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--from-json", default="",
                   help="Supabase の代わりに portfolio_results 行の JSON ファイルから作る（検証用）")
    args = p.parse_args()

    if args.from_json:
        with open(args.from_json, encoding="utf-8") as f:
            rows = [json.load(f)]
    else:
        if args.env_file:
            supabase_io.load_env_file(args.env_file)
        url, key = supabase_io.credentials()
        import urllib.request
        req = urllib.request.Request(
            "%s/rest/v1/portfolio_results?select=computed_at,data_through,params,result&id=eq.%s"
            % (url, RESULT_ID),
            headers={"apikey": key, "Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=120) as r_:
            rows = json.loads(r_.read())
        if not rows:
            log("portfolio_results に %s がありません。先に build_portfolio.py を流してください。"
                % RESULT_ID)
            return 1

    payload = build(rows[0])

    errs = validate(payload)
    if errs:
        log("検証に失敗したので書き出しません:")
        for e in errs:
            log("  - " + e)
        return 1

    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    log("トレード %s / 勝率 %s%% / PF %s / 最大DD %s%% / 最大連敗 %s / asof %s"
        % (payload["trades"], payload["win_rate"], payload["pf"],
           payload["max_dd_pct"], payload["max_losing_streak"], payload["asof"]))
    if args.dry_run:
        log("--dry-run のため書き出しません")
        print(text)
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("書き出し: %s (%.1f KB)" % (args.out, os.path.getsize(args.out) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
