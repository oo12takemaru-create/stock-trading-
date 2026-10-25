# -*- coding: utf-8 -*-
"""正本（Supabase の market_condition）の地合いを、検証エンジンの式で作り直して書き換える道具。

■ なぜ要るか（2026-10-25・本人決定／Fable 仕様）
  前計算の正本 regime は、これまで旧スキャナー版（BULLISH に「日経>50日線」「日経1ヶ月騰落>0」が無い）
  だった。本番スキャナーは #716 で検証エンジン（integrated_backtest_v2_8_0.py）と同じ判定になったので、
  公開の10年も同じ判定に揃える。あわせて、日経1ヶ月騰落が検証エンジンと1本ずれていた
  （前計算は22本前・エンジンは21本前）のを直す。騰落は PANIC と HALT にも効く。

  precompute/metrics.py を直しても、それが効くのは「これから書く日」だけ。過去の行はここで書き換える。

■ 何を書き換えるか（market_condition の全行。列は6つだけ）
  nikkei_1m_change … 保存済みの nikkei_close から 21本前比で作り直す
  regime           … metrics.classify_regime（検証エンジンと同じ式）。★保存列 regime_engine は使わない★
                     （2018-07-18 の1日だけ、休場日の直しの前の値が残っているため。式から作る）
  regime_engine    … regime と同じ値（別名として残す）
  regime_scanner   … 書き換える前の regime をそのまま写す（監査用。2026-10-25 までの公開の地合い）
  is_halt / halt_reason … metrics.halt_states に新しい騰落を入れて作り直す
  ほかの列（日経の終値・移動平均・S&P・VIX・breadth・score3 など）には触らない。

  頭の21行（2016-09-06〜）の「21本前の終値」は正本に無い（market_condition は 2016-09-06 から）。
  保存済みの22本前比（nikkei_1m_change）から戻す：close[j−22] = close[j] / (1 + 騰落[j]/100)。
  書き換えた後は21本前比から同じように戻せるので、何度流しても同じ答えになる。

■ 書く前の確かめ（どれか1つでも外れたら何も書かずに止まる）
  1. 休場日の行が無い（東証のカレンダー）
  2. 保存済みの nikkei_1m_change が、保存済みの終値から22本前比で作り直した値と6桁で全行一致する
     （＝書き換える前の正本である。21本前比で一致すれば「書き換え済み」と判断する）
  3. 旧スキャナー版の式（22本前比）で、保存済みの regime を全日再現できる
  4. 検証エンジンの式（22本前比）で、保存済みの regime_engine を再現できる（外れてよいのは 2018-07-18 だけ）
  5. HALT の式（22本前比）で、保存済みの is_halt・halt_reason を全日再現できる
  → 2〜5 が通る＝「同じ式に、本数だけ変えて入れ直す」ことが正しい手順だという証拠。

■ 使い方（本人が実行する。CI からは呼ばない）
    python tools/rewrite_market_regime.py --dry   --env-file ../ruletrade-app/.env.local
        … 変わる日の一覧（旧→新）と件数を出すだけ（読むだけ）。一覧は --out-dir に CSV で出す
    python tools/rewrite_market_regime.py --apply --expect-rows N --expect <期待値.json> --env-file ...
        … N は --dry が出した「書き換える行」の数。食い違えば何もしないで止まる
          1) 書く前に「精密な控え」（market_condition の全行・全列、数は ::float8）と
             backtest_cache の全行を gz に保存して読み戻す
          2) 1行ずつ PATCH（主キー＝date で1行だけ当たることを返り値で確かめる。upsert は使わない）
          3) 全行を読み戻し、もう一度計画を作って「書き換える行 0」になることを確かめる
          4) backtest_cache を空にする（地合いの定義が変わったので、保存済みの結果は古い）
          5) 前計算の再計算（正本から・保存しない）が、期待値と売買の明細まで完全一致することを確かめる
    python tools/rewrite_market_regime.py --verify --expect <期待値.json> --env-file ...
        … 5) だけ（読むだけ）
    python tools/rewrite_market_regime.py --rollback --precise <控え.json.gz> --env-file ...
        … 控えの6列を1行ずつ元の値に戻し、backtest_cache を控えから戻す。読み戻して確かめる

  期待値.json は、切り替えの前に固定材料で回した試算の結果（実行前に固定した見張り）。
  ★見張りの物差しは「売買判断の完全一致」★ 浮動小数の許容は使わない。結果を見てから変えない。

■ 書く前に SQL を1回流すこと: tools/sql/2026-10-25_market_condition_regime_scanner.sql
■ 鍵の値は出さない。env ファイルはパスを渡すだけ。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
PRE = os.path.join(os.path.dirname(HERE), "precompute")
sys.path.insert(0, PRE)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
import metrics  # noqa: E402
import supabase_io  # noqa: E402

TABLE = "market_condition"
#: 書き換える列（これ以外には触らない）
WRITE_COLS = ["regime", "regime_engine", "regime_scanner", "nikkei_1m_change", "is_halt", "halt_reason"]
#: 式を入れ直すのに読む列
INPUT_COLS = ["nikkei_close", "nikkei_ma_50", "nikkei_ma_200", "sp500_close", "sp500_ma_200", "vix"]
#: 保存列 regime_engine が式と合わないと分かっている日（休場日の直しの前の1ヶ月騰落が残った・2026-10-24 の試算）
KNOWN_STALE_ENGINE = {"2018-07-18"}
OLD_BARS = int(config.REGIME_RULES["scanner_n225_1m_lookback_bars"])   # 書き換える前の正本の本数
NEW_BARS = int(config.REGIME_RULES["n225_1m_lookback_bars"])           # 検証エンジンと同じ本数
#: 期待値と突き合わせる前計算の結果のキー（portfolio_results.result と同じ形）
RESULT_KEYS = ["summary", "by_rule", "yearly", "exit_reasons", "daily_entries", "equity_curve",
               "unconstrained_signals", "after_circuit_breaker", "period", "basis"]
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "stock-trading-db-backups")

assert NEW_BARS == 21 and OLD_BARS == 22, "thresholds.json の本数が想定と違う（%d / %d）" % (NEW_BARS, OLD_BARS)


def log(m):
    print("[%s] %s" % (dt.datetime.now().strftime("%H:%M:%S"), m), flush=True)


def _s(v):
    """空（None・NaN・空文字）を None に揃える。DataFrame を通すと None が NaN になるため。"""
    if v is None or v == "" or (isinstance(v, float) and np.isnan(v)):
        return None
    return v


def _same_vix_text(a, b):
    """どちらも「VIX=xx.x > 35（…）」で、数の差が表示の1目盛り以内なら同じとみなす。

    VIX の値は正本に単精度で入っていて、元の倍精度の値の小数1桁の表示（.x5 の境目）を
    保存値からは再現できない（2020-03-18・04-08・04-17 の3日で1目盛り違った・2026-10-25）。
    VIX は今回の切り替えで変わらないので、VIX が理由の文は保存の文を残す。"""
    a, b = _s(a), _s(b)
    if not (isinstance(a, str) and isinstance(b, str) and a.startswith("VIX=") and b.startswith("VIX=")):
        return False
    try:
        va, vb = float(a[4:].split(" ")[0]), float(b[4:].split(" ")[0])
    except ValueError:
        return False
    return abs(va - vb) <= 0.1 + 1e-9 and a.split(" ", 1)[1] == b.split(" ", 1)[1]


def g6(v):
    """real 列を PostgREST が返す文字（有効数字6桁）と同じ形にする。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    return "%.6g" % float(np.float32(float(v)))


# ── PostgREST ───────────────────────────────────────────────
def _req(method, path, body=None, prefer=None, retries=3):
    url, key = supabase_io.credentials()
    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Content-Type": "application/json", "Accept": "application/json"}
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
    last = None
    for i in range(retries + 1 if method in ("GET", "PATCH") else 1):   # DELETE・POST は自動で再試行しない
        try:
            r = urllib.request.urlopen(urllib.request.Request(url + "/rest/v1/" + path, data=data,
                                                              headers=headers, method=method), timeout=120)
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            last = "HTTP %s %s" % (e.code, e.read()[:300].decode("utf-8", "replace"))
            if e.code < 500 and "PGRST303" not in last:   # 時計のずれ（PGRST303）だけは一過性
                break
        except Exception as e:   # noqa: BLE001
            last = type(e).__name__
        time.sleep(2 * (i + 1))
    raise RuntimeError("%s %s に失敗: %s" % (method, path.split("?")[0], last))


def get_all(select, query="order=date.asc"):
    out, off = [], 0
    while True:
        part = _req("GET", "%s?select=%s&%s&limit=1000&offset=%d" % (TABLE, select, query, off))
        out.extend(part)
        if len(part) < 1000:
            return out
        off += 1000


def has_scanner_column():
    try:
        _req("GET", "%s?select=regime_scanner&limit=1" % TABLE, retries=0)
        return True
    except RuntimeError as e:
        if "regime_scanner" in str(e) or "42703" in str(e) or "PGRST204" in str(e):
            return False
        raise


def read_table(require_scanner):
    has = has_scanner_column()
    if require_scanner and not has:
        raise SystemExit("★market_condition に regime_scanner 列がありません。"
                         "先に tools/sql/2026-10-25_market_condition_regime_scanner.sql を SQL Editor で流してください★")
    cols = ["date"] + INPUT_COLS + [c for c in WRITE_COLS if has or c != "regime_scanner"]
    total = supabase_io.count_rows(TABLE)
    rows = get_all(",".join(cols))
    if len(rows) != total:
        raise SystemExit("★%s を %d 行読むはずが %d 行でした（取りこぼし）。止めます★" % (TABLE, total, len(rows)))
    df = pd.DataFrame(rows)
    if not has:
        df["regime_scanner"] = None
    df["date"] = pd.to_datetime(df["date"])
    log("market_condition: %s 行（%s 〜 %s）/ regime_scanner 列: %s"
        % (f"{len(df):,}", df["date"].min().date(), df["date"].max().date(), "あり" if has else "★まだ無い★"))
    return df, has


# ── 計画（純粋な計算。正本は触らない）─────────────────────────
def build_plan(df):
    """読んだ market_condition（全行）から、書き換える値と確かめの結果を作る。

    戻り値: dict(state, new=DataFrame(date + WRITE_COLS), changes=[(date, {列: 新しい値})], checks=[...])
    state … "before"（書き換える前）/ "after"（書き換え済み）
    """
    import trading_calendar as tc

    df = df.sort_values("date").reset_index(drop=True)
    _, bad = tc.drop_non_sessions(df.copy(), "date")
    if len(bad):
        raise SystemExit("★休場日の行が %d 日あります（%s）。止めます★" % (len(bad), ", ".join(str(d.date()) for d in bad[:5])))
    num = {c: pd.to_numeric(df[c], errors="coerce").astype("float64") for c in INPUT_COLS + ["nikkei_1m_change"]}
    close = num["nikkei_close"].to_numpy()
    stored = num["nikkei_1m_change"].to_numpy()
    if np.isnan(close).any():
        raise SystemExit("★nikkei_close に空があります。止めます★")
    n = len(df)

    def recompute(bars):
        return (close[bars:] / close[:-bars] - 1) * 100

    def same6(calc, bars):
        return [i + bars for i, v in enumerate(calc) if g6(v) != g6(stored[i + bars])]

    bad22, bad21 = same6(recompute(OLD_BARS), OLD_BARS), same6(recompute(NEW_BARS), NEW_BARS)
    if not bad22 and bad21:
        state, bars_now = "before", OLD_BARS
    elif not bad21 and bad22:
        state, bars_now = "after", NEW_BARS
    else:
        raise SystemExit("★保存済みの nikkei_1m_change が22本前比とも21本前比とも全行では合いません"
                         "（22本で外れ %d 行・21本で外れ %d 行。途中まで書いた状態かもしれない）。"
                         "--rollback で控えに戻してからやり直してください★" % (len(bad22), len(bad21)))
    log("保存済みの日経1ヶ月騰落は %d 本前比（%s）" % (bars_now, "書き換える前" if state == "before" else "書き換え済み"))

    # 頭の行の「21本前の終値」を、保存済みの騰落から戻す
    pre = np.array([close[j] / (1 + stored[j] / 100) for j in range(bars_now - NEW_BARS, bars_now)])
    full = np.concatenate([pre, close])
    chg_new = (full[NEW_BARS:] / full[:-NEW_BARS] - 1) * 100
    assert len(chg_new) == n
    if state == "after":
        diff = [i for i in range(n) if g6(chg_new[i]) != g6(stored[i])]
        if diff:
            raise SystemExit("★書き換え済みの騰落を作り直すと %d 行合いません。止めます★" % len(diff))

    idx = df.index
    S = lambda a: pd.Series(a, index=idx)  # noqa: E731
    enough_n = num["nikkei_ma_200"].notna()
    enough_s = num["sp500_ma_200"].notna()
    args = (num["nikkei_close"], num["nikkei_ma_50"], num["nikkei_ma_200"],
            num["sp500_close"], num["sp500_ma_200"], num["vix"])
    checks = []

    def check(label, ok_, detail=""):
        checks.append((label, ok_, detail))
        log("  確かめ %s: %s %s" % (label, "合格" if ok_ else "★不合格★", detail))

    dates = df["date"].dt.strftime("%Y-%m-%d").to_numpy()
    old_halt, old_reason = df["is_halt"].astype(bool).to_numpy(), df["halt_reason"].to_numpy()
    if state == "before":
        s_now = S(stored)
        scan = metrics.classify_regime(*args, s_now, enough_n, enough_s, scanner=True)
        eng = metrics.classify_regime(*args, s_now, enough_n, enough_s)
        h, rs = metrics.halt_states(num["vix"], s_now)
        d_scan = [dates[i] for i in range(n) if scan[i] != df.at[i, "regime"]]
        d_eng = [dates[i] for i in range(n) if eng[i] != df.at[i, "regime_engine"]]
        d_halt = [dates[i] for i in range(n) if bool(h[i]) != bool(old_halt[i]) or (_s(rs[i]) != _s(old_reason[i]) and not _same_vix_text(rs[i], old_reason[i]))]
        check("旧スキャナー版の式で保存済みの regime を再現", not d_scan, "外れ %d 日 %s" % (len(d_scan), d_scan[:5]))
        check("検証エンジンの式で保存済みの regime_engine を再現（2018-07-18 だけ外れてよい）",
              set(d_eng) <= KNOWN_STALE_ENGINE, "外れ %d 日 %s" % (len(d_eng), d_eng[:5]))
        check("HALT の式で保存済みの is_halt・halt_reason を再現", not d_halt, "外れ %d 日 %s" % (len(d_halt), d_halt[:5]))
        if df["regime_scanner"].notna().any():
            check("regime_scanner がまだ空", False, "空でない行 %d" % int(df["regime_scanner"].notna().sum()))
        scanner_new = df["regime"].to_numpy()
    else:
        scanner_new = df["regime_scanner"].to_numpy()
        check("regime_scanner が全行埋まっている", bool(pd.Series(scanner_new).notna().all()))

    chg_s = S(chg_new)
    regime_new = metrics.classify_regime(*args, chg_s, enough_n, enough_s)
    halt_new, reason_new = metrics.halt_states(num["vix"], chg_s)
    reason_new = [old_reason[i] if _same_vix_text(reason_new[i], old_reason[i]) else reason_new[i]
                  for i in range(n)]
    new = pd.DataFrame({"date": dates, "regime": regime_new, "regime_engine": regime_new,
                        "regime_scanner": scanner_new, "nikkei_1m_change": chg_new,
                        "is_halt": [bool(x) for x in halt_new], "halt_reason": reason_new})

    changes = []
    for i in range(n):
        body = {}
        for c in ("regime", "regime_engine", "regime_scanner"):
            if _s(new.at[i, c]) != _s(df.at[i, c]):
                body[c] = new.at[i, c]
        if g6(chg_new[i]) != g6(stored[i]):
            body["nikkei_1m_change"] = float(chg_new[i])
        if bool(new.at[i, "is_halt"]) != bool(old_halt[i]):
            body["is_halt"] = bool(new.at[i, "is_halt"])
        if _s(new.at[i, "halt_reason"]) != _s(old_reason[i]):
            body["halt_reason"] = new.at[i, "halt_reason"]
        if body:
            changes.append((dates[i], body))
    return {"state": state, "old": df, "new": new, "changes": changes, "checks": checks}


def report(plan, out_dir):
    old, new, ch = plan["old"], plan["new"], plan["changes"]
    end = config.MATERIAL_START, "2026-09-04"
    inper = (new["date"] >= end[0]) & (new["date"] <= end[1])
    log("計画: 書き換える行 %s / 全 %s 行" % (f"{len(ch):,}", f"{len(new):,}"))
    for c in WRITE_COLS:
        k = sum(1 for _, b in ch if c in b)
        log("  列 %-17s 変わる行 %s" % (c, f"{k:,}"))
    ct = pd.crosstab(old["regime"].rename("旧 regime"), pd.Series(new["regime"], name="新 regime"))
    log("地合い 旧→新（全期間 %d 日）\n%s" % (len(new), ct.to_string()))
    chg_days = new.loc[old["regime"].to_numpy() != new["regime"].to_numpy(), "date"]
    log("  地合いが変わる日: 全期間 %d 日 / 検証期間（%s〜%s）%d 日"
        % (len(chg_days), end[0], end[1], int((inper & (old["regime"].to_numpy() != new["regime"].to_numpy())).sum())))
    hd = [(d, b["is_halt"]) for d, b in ch if "is_halt" in b]
    log("  HALT が変わる日: %d 日 %s" % (len(hd), ", ".join("%s→%s" % (d, "HALT" if v else "解除") for d, v in hd[:30])))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "regime_rewrite_plan_%s.csv" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date"] + ["旧_" + c for c in WRITE_COLS] + ["新_" + c for c in WRITE_COLS] + ["書く列"])
            want = dict(ch)
            for i in range(len(new)):
                d = new.at[i, "date"]
                if d not in want:
                    continue
                w.writerow([d] + [old.at[i, c] if c != "nikkei_1m_change" else g6(old.at[i, c]) for c in WRITE_COLS]
                           + [new.at[i, c] if c != "nikkei_1m_change" else g6(new.at[i, c]) for c in WRITE_COLS]
                           + ["・".join(want[d])])
        log("  一覧: %s" % p)


# ── 書く ────────────────────────────────────────────────────
def precise_backup(out_dir):
    """market_condition の全行・全列（数は ::float8）と backtest_cache の全行を gz に保存し、読み戻す。"""
    os.makedirs(out_dir, exist_ok=True)
    sample = _req("GET", "%s?select=*&limit=1" % TABLE)[0]
    cols = list(sample)
    numeric = [c for c, v in sample.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    sel = ",".join(("%s::float8" % c) if c in numeric else c for c in cols)
    mc = get_all(sel)
    cache = []
    off = 0
    while True:
        part = _req("GET", "backtest_cache?select=*&order=params_hash&limit=200&offset=%d" % off)
        cache.extend(part)
        if len(part) < 200:
            break
        off += 200
    path = os.path.join(out_dir, "precise_backup_market_condition_%s.json.gz" % dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump({"market_condition": mc, "backtest_cache": cache, "columns": cols, "numeric": numeric}, f, ensure_ascii=False)
    back = json.load(gzip.open(path, "rt", encoding="utf-8"))
    if len(back["market_condition"]) != supabase_io.count_rows(TABLE) or len(back["backtest_cache"]) != len(cache):
        raise SystemExit("★控えの読み戻しで行数が合いません。止めます（何も書いていません）★")
    log("精密な控え: market_condition %s 行 / backtest_cache %d 行 → %s" % (f"{len(mc):,}", len(cache), path))
    return path


def patch_rows(items, label):
    """items = [(date, body)]。1行ずつ PATCH し、1行だけ当たったことを確かめる。"""
    done, fail = 0, []
    t0 = time.time()

    def one(it):
        d, body = it
        res = _req("PATCH", "%s?date=eq.%s&select=date" % (TABLE, d), body, prefer="return=representation")
        if not res or len(res) != 1:
            raise RuntimeError("%s: 当たった行が %s" % (d, len(res) if res else 0))
        return d

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(one, it) for it in items]
        for fu in as_completed(futs):
            try:
                fu.result()
                done += 1
                if done % 500 == 0:
                    log("  %s: %s / %s 行（%.0f秒）" % (label, f"{done:,}", f"{len(items):,}", time.time() - t0))
            except Exception as e:   # noqa: BLE001
                fail.append(str(e))
                for f2 in futs:
                    f2.cancel()
                break
    return done, fail


def apply(a):
    df, _ = read_table(require_scanner=True)
    plan = build_plan(df)
    if plan["state"] != "before":
        raise SystemExit("★正本はもう書き換え済みです（書き換える行 %d）。--verify で見張りだけ確かめてください★"
                         % len(plan["changes"]))
    if not all(ok for _, ok, _ in plan["checks"]):
        raise SystemExit("★書く前の確かめが通りません。何も書かずに止めます★")
    report(plan, a.out_dir)
    if len(plan["changes"]) != a.expect_rows:
        raise SystemExit("★書き換える行が %d（--expect-rows %d と違う）。--dry を流し直して数を確かめてください。"
                         "何も書いていません★" % (len(plan["changes"]), a.expect_rows))
    expect = load_expect(a.expect)
    prec = precise_backup(a.out_dir)
    log("1. market_condition を1行ずつ PATCH（%s 行）" % f"{len(plan['changes']):,}")
    done, fail = patch_rows(plan["changes"], "PATCH")
    log("  PATCH 済み %s 行 / 失敗 %d" % (f"{done:,}", len(fail)))
    if fail or done != len(plan["changes"]):
        raise SystemExit("★PATCH が途中で止まりました（%d 行まで・最初の失敗 %s）。"
                         "戻すには --rollback --precise %s★" % (done, fail[:1], prec))
    log("2. 読み戻して確かめる")
    df2, _ = read_table(require_scanner=True)
    plan2 = build_plan(df2)
    want = plan["new"].set_index("date")
    got = df2.assign(date=df2["date"].dt.strftime("%Y-%m-%d")).set_index("date")
    bad = 0
    for d in want.index:
        w, g = want.loc[d], got.loc[d]
        if (w["regime"] != g["regime"] or w["regime_engine"] != g["regime_engine"]
                or _s(w["regime_scanner"]) != _s(g["regime_scanner"]) or g6(w["nikkei_1m_change"]) != g6(g["nikkei_1m_change"])
                or bool(w["is_halt"]) != bool(g["is_halt"]) or _s(w["halt_reason"]) != _s(g["halt_reason"])):
            bad += 1
    log("  読み戻し: 状態 %s / 書き換える行 %d（0 のはず）/ 計画と違う行 %d（0 のはず）"
        % (plan2["state"], len(plan2["changes"]), bad))
    if plan2["state"] != "after" or plan2["changes"] or bad:
        raise SystemExit("★読み戻しが合いません。公開に進まず --rollback --precise %s で戻してください★" % prec)
    log("3. backtest_cache を空にする（控えは %s）" % prec)
    res = _req("DELETE", "backtest_cache?params_hash=not.is.null&select=params_hash", prefer="return=representation")
    log("  消えた行 %d" % len(res or []))
    log("4. 前計算の再計算（正本から・保存しない）を期待値と突き合わせる")
    ok = verify(expect)
    log("★書き換え完了・見張り %s★ 精密な控え: %s" % ("合格" if ok else "★不合格★", prec))
    if not ok:
        raise SystemExit("★見張りが外れました。公開に進まず、Fable に連絡してください。"
                         "戻すときは --rollback --precise %s★" % prec)


def load_expect(path):
    if not path:
        raise SystemExit("--expect（実行前に固定した期待値の JSON）が要ります")
    e = json.load(open(path, encoding="utf-8"))
    for k in ("result", "trades_sha256", "trades"):
        if k not in e:
            raise SystemExit("★期待値の JSON に %s がありません★" % k)
    return e


def trades_fingerprint(trades):
    """売買の明細（銘柄・戦略・約定日・決済日・株数・価格・出口）を並べた sha256。売買判断の完全一致を見る。"""
    rows = sorted((t.ticker, t.strategy, str(t.entry_date)[:10], str(t.exit_date)[:10], int(t.shares),
                   "%.6f" % t.entry_price, "%.6f" % t.exit_price, t.reason) for t in trades)
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest(), len(rows)


def verify(expect):
    """正本から前計算を回し（build_portfolio.py と同じ読み方・保存しない）、期待値と完全一致か。"""
    import panel_from_db
    import portfolio_run
    panel, market = panel_from_db.load_panel(log=log)
    res, trades = portfolio_run.run(panel, market, log=log)
    got = json.loads(json.dumps(res, default=str))
    ok = True
    for k in RESULT_KEYS:
        same = got.get(k) == expect["result"].get(k)
        ok &= same
        log("  result.%-22s %s" % (k, "一致" if same else "★不一致★"))
    fp, n = trades_fingerprint(trades)
    same = fp == expect["trades_sha256"] and n == expect["trades"]
    ok &= same
    log("  売買の明細 %d 行の指紋 %s…（期待 %s…）%s" % (n, fp[:16], expect["trades_sha256"][:16], "一致" if same else "★不一致★"))
    s = got["summary"]
    log("  6項目: %s件 / 勝率%s%% / PF%s / 最大DD%s%% / 累積%s%% / 年率%s%%"
        % (s["trades"], s["win_rate"], s["pf"], s["max_dd"], s["total_return"], s["cagr"]))
    return ok


# ── 元に戻す ────────────────────────────────────────────────
def rollback(a):
    P = json.load(gzip.open(a.precise, "rt", encoding="utf-8"))
    rows = P["market_condition"]
    log("元に戻す（精密な控え: market_condition %s 行 / backtest_cache %d 行）" % (f"{len(rows):,}", len(P["backtest_cache"])))

    def val(c, v):
        if c == "nikkei_1m_change":
            return None if v is None else float(v)
        return v

    items = [(r["date"], {c: val(c, r.get(c)) for c in WRITE_COLS}) for r in rows]
    done, fail = patch_rows(items, "rollback")
    if fail:
        raise SystemExit("★戻す途中で止まりました（%d 行まで・%s）。もう一度 --rollback を実行してください（何度でも同じ結果）★"
                         % (done, fail[:1]))
    if P["backtest_cache"]:
        for i in range(0, len(P["backtest_cache"]), 50):
            _req("POST", "backtest_cache?on_conflict=params_hash", P["backtest_cache"][i:i + 50],
                 prefer="resolution=merge-duplicates,return=minimal")
    log("backtest_cache を戻した（%d 行）" % len(P["backtest_cache"]))
    # 確かめ：6列が控えと同じ（数は単精度の目盛りで比べる）
    df, _ = read_table(require_scanner=True)
    got = {d.strftime("%Y-%m-%d"): r for d, r in zip(df["date"], df.to_dict("records"))}
    bad = 0
    for r in rows:
        g = got.get(r["date"])
        if g is None:
            bad += 1
            continue
        for c in WRITE_COLS:
            a_, b_ = (g6(g[c]), g6(r[c])) if c == "nikkei_1m_change" else (g[c], r.get(c))
            if _s(a_) != _s(b_):
                bad += 1
                break
    log("★元に戻しました★ 控えと違う行 %d（0 のはず）" % bad if not bad else "★控えと違う行が %d あります★" % bad)
    plan = build_plan(df)
    log("  状態: %s（before のはず）" % plan["state"])


def main():
    p = argparse.ArgumentParser(description="market_condition の地合いを検証エンジンの式で作り直す")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--rollback", action="store_true")
    p.add_argument("--env-file", default="")
    p.add_argument("--out-dir", default=DEFAULT_OUT, help="控えと一覧の置き場所（既定 ~/stock-trading-db-backups）")
    p.add_argument("--expect-rows", type=int, default=-1, help="--dry が出した「書き換える行」の数")
    p.add_argument("--expect", default="", help="実行前に固定した期待値の JSON（見張り）")
    p.add_argument("--precise", default="", help="--rollback に使う精密な控え")
    a = p.parse_args()
    if a.env_file:
        supabase_io.load_env_file(a.env_file)

    if a.dry:
        df, has = read_table(require_scanner=False)
        plan = build_plan(df)
        report(plan, a.out_dir)
        ok = all(o for _, o, _ in plan["checks"])
        log("書く前の確かめ: %s / regime_scanner 列: %s" % ("すべて合格" if ok else "★不合格あり★", "あり" if has else "★まだ無い（SQL を先に流す）★"))
        log("--dry 終わり（正本には何も書いていません）")
        return 0 if ok else 1
    if a.apply:
        apply(a)
        return 0
    if a.verify:
        return 0 if verify(load_expect(a.expect)) else 1
    if not a.precise:
        raise SystemExit("--rollback には --precise（--apply が書き出した精密な控え）が要ります")
    rollback(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
