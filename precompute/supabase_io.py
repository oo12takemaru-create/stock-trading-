# -*- coding: utf-8 -*-
"""Supabase(PostgREST) への書き込み。標準ライブラリだけで動く。

環境変数:
  SUPABASE_URL                 https://xxxx.supabase.co
  SUPABASE_SERVICE_ROLE_KEY    service_role キー（RLS を迂回する。絶対に公開しない）

GitHub Actions では Settings → Secrets → Actions に登録して渡す。
ローカルでは ruletrade-app/.env.local から読んでもよい（--env-file オプション）。
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request

CHUNK_ROWS = 2000
TIMEOUT = 180


class SupabaseError(RuntimeError):
    pass


def load_env_file(path):
    """KEY=VALUE 形式のファイルを os.environ に流し込む（既存の値は上書きしない）。"""
    if not path or not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            # ruletrade-app 側は NEXT_PUBLIC_SUPABASE_URL という名前で持っている
            if k == "NEXT_PUBLIC_SUPABASE_URL":
                k = "SUPABASE_URL"
            os.environ.setdefault(k, v)


def credentials():
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not key:
        raise SupabaseError(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY が設定されていません。"
            "GitHub Secrets か --env-file で渡してください。")
    return url, key


def _clean(v):
    """JSON に出せない値（NaN / NaT / numpy 型）を素の Python 値に直す。"""
    if v is None:
        return None
    # pandas の欠損（pd.NA / NaT）は bool 化すると例外になるので先に潰す
    if type(v).__name__ in ("NAType", "NaTType"):
        return None
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    if isinstance(v, (bool, int, str)):
        return v
    # numpy / pandas のスカラー
    item = getattr(v, "item", None)
    if callable(item):
        try:
            return _clean(item())
        except Exception:
            pass
    if hasattr(v, "isoformat"):          # date / Timestamp
        s = v.isoformat()
        return s[:10] if len(s) >= 10 and s[10:11] in ("", "T") else s
    try:
        if v != v:                       # その他の NaN 相当
            return None
    except (TypeError, ValueError):
        return None
    return str(v)


def frame_to_records(df, date_cols=("date",)):
    """DataFrame を PostgREST に渡せる dict のリストにする。"""
    recs = df.to_dict(orient="records")
    out = []
    for r in recs:
        row = {k: _clean(v) for k, v in r.items()}
        for c in date_cols:
            if c in row and row[c] is not None:
                row[c] = str(row[c])[:10]
        out.append(row)
    return out


def _request(method, url, key, body=None, extra_headers=None):
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)
    data = None
    if body is not None:
        # PostgREST は gzip したリクエストボディを受け付けない
        # （Content-Encoding: gzip を付けると "Empty or invalid json" になる）。
        # 素の JSON で送ること。
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.status, r.read()


def _post_chunk(endpoint, key, part, headers, log, retries, min_chunk=125):
    """1かたまりを投入する。時間切れなら半分に割って入れ直す。

    無料枠の Postgres は混んでいると 2,000行の upsert でも statement_timeout に
    当たる（実測）。落ちたら諦めるのではなく、粒度を落として通す。
    """
    for attempt in range(retries + 1):
        try:
            _request("POST", endpoint, key, part, headers)
            return
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode("utf-8", "replace")
            timed_out = e.code in (500, 504) and "57014" in detail
            if timed_out and len(part) > min_chunk:
                half = len(part) // 2
                log("  ! 時間切れ: %d行 → %d行×2 に割って入れ直す" % (len(part), half))
                _post_chunk(endpoint, key, part[:half], headers, log, retries, min_chunk)
                _post_chunk(endpoint, key, part[half:], headers, log, retries, min_chunk)
                return
            if attempt >= retries:
                raise SupabaseError("投入に失敗 (HTTP %s): %s" % (e.code, detail)) from None
            log("  ! HTTP %s 再試行 %d/%d: %s" % (e.code, attempt + 1, retries, detail[:160]))
            time.sleep(3 * (attempt + 1))
        except Exception as e:  # ネットワーク断など
            if attempt >= retries:
                raise SupabaseError("投入に失敗: %s" % e) from None
            log("  ! %s 再試行 %d/%d" % (type(e).__name__, attempt + 1, retries))
            time.sleep(3 * (attempt + 1))


def upsert(table, records, on_conflict, log=print, chunk=CHUNK_ROWS, retries=4):
    """records を table に upsert する。on_conflict は主キーの列名（カンマ区切り）。"""
    if not records:
        log("  %s: 投入する行がありません" % table)
        return 0
    url_base, key = credentials()
    endpoint = "%s/rest/v1/%s?on_conflict=%s" % (url_base, table, on_conflict)
    headers = {"Prefer": "resolution=merge-duplicates,return=minimal"}

    done = 0
    t0 = time.time()
    for i in range(0, len(records), chunk):
        part = records[i:i + chunk]
        _post_chunk(endpoint, key, part, headers, log, retries)
        done += len(part)
        if (i // chunk) % 10 == 0 or done == len(records):
            log("  %s: %s / %s 行 (%.0f秒)" % (table, f"{done:,}", f"{len(records):,}",
                                              time.time() - t0))
    return done


SECONDARY_INDEXES = ["ticker_date", "dev_25", "vol_ratio_20",
                     "high_20_ratio", "high_52w_ratio"]


def _rpc(name, body=None):
    url_base, key = credentials()
    return _request("POST", "%s/rest/v1/rpc/%s" % (url_base, name), key, body or {})


def analyze():
    """投入後に統計情報を作り直す（索引が使われるようにする）。"""
    _rpc("analyze_metrics")


def truncate_metrics():
    """daily_metrics を空にする。全期間の入れ直し前に呼ぶ。"""
    _rpc("truncate_metrics")


def drop_secondary_indexes(log=print):
    """一括投入の間だけ副索引を外す。

    ★これをやらないと投入が途中で事実上止まる（2026-09-05 実測）。
      索引がメモリに載らなくなった時点でランダムIOになり、
      81.5万行の後半では1行の upsert に2.6秒かかるところまで劣化した。
    """
    _rpc("drop_metrics_indexes")
    log("  副索引を外した（%s）" % ", ".join(SECONDARY_INDEXES))


def rebuild_secondary_indexes(log=print):
    """投入後に副索引を張り直す。1本ずつ呼ぶ（1回のHTTPを長くしないため）。"""
    for name in SECONDARY_INDEXES:
        t0 = time.time()
        _rpc("rebuild_metrics_index", {"p_name": name})
        log("  索引 %s を作成 (%.0f秒)" % (name, time.time() - t0))


def delete_where(table, query, log=print):
    """例: delete_where('daily_metrics', 'date=gte.2026-01-01')"""
    url_base, key = credentials()
    endpoint = "%s/rest/v1/%s?%s" % (url_base, table, query)
    _request("DELETE", endpoint, key, None, {"Prefer": "return=minimal"})
    log("  %s: 削除 %s" % (table, query))


def count_rows(table, query=""):
    url_base, key = credentials()
    endpoint = "%s/rest/v1/%s?select=*&limit=1%s" % (url_base, table,
                                                     ("&" + query) if query else "")
    headers = {"apikey": key, "Authorization": "Bearer " + key,
               "Prefer": "count=exact", "Range": "0-0"}
    req = urllib.request.Request(endpoint, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        cr = r.headers.get("Content-Range", "")
    return int(cr.split("/")[-1]) if "/" in cr else 0


def max_value(table, column):
    """table の column の最大値（最新日付の確認用）。"""
    url_base, key = credentials()
    endpoint = "%s/rest/v1/%s?select=%s&order=%s.desc&limit=1" % (
        url_base, table, column, column)
    status, body = _request("GET", endpoint, key)
    rows = json.loads(body.decode("utf-8"))
    return rows[0][column] if rows else None
