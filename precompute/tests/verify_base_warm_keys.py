# -*- coding: utf-8 -*-
"""土台6通りの温めのキーが、会員アプリの読むキーと一致するか（2026-11-07）。

会員アプリ（/rules/new・/try）は content/base-rules.ts の requestForBase() で
リクエストを組み、lib/backtest/params.ts の normalize()・paramsHash() でキーを作って
backtest_cache を読む。warm_cache.py の BASE_RULES_WARM が1つでも違うと、
**エラーにならず当たらないだけ**（夜に温めても、その日最初の人のときに計算が走る）。

★手で写した値どうしを比べない★
  会員アプリ側は base-rules.ts の BASE_RULES と requestForBase() をそのまま呼んで作る。
  Python 側は warm_cache.py が実際に温める経路（fixed_rule_params）を通す。

    python precompute/tests/verify_base_warm_keys.py
    RULETRADE_APP_DIR=<会員アプリのフォルダ> python precompute/tests/verify_base_warm_keys.py

会員アプリ側で npx tsx が動くこと（node_modules がある）が前提。Supabase には接続しない。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from warm_cache import BASE_RULES_WARM, fixed_rule_params  # noqa: E402
import cache_key  # noqa: E402

APP_DIR = os.environ.get("RULETRADE_APP_DIR", r"D:\マイドキュメント\Claude\Projects\ruletrade-app")
DATA_THROUGH = "2026-09-04"

# 会員アプリ側：土台ごと・3択ごとに requestForBase → normalize → paramsHash
#   一時ファイルに書いて tsx で回す（会員アプリのフォルダには何も書かない）
TS_CODE = """
import { pathToFileURL } from "node:url";
import { join } from "node:path";
const app = process.env.RULETRADE_APP_DIR_RESOLVED as string;
const dataThrough = process.env.DATA_THROUGH as string;
(async () => {
  const br = await import(pathToFileURL(join(app, "src/content/base-rules.ts")).href);
  const pr = await import(pathToFileURL(join(app, "src/lib/backtest/params.ts")).href);
  const out = [];
  for (const b of br.BASE_RULES) {
    for (let i = 0; i < b.choice.options.length; i++) {
      const p = pr.normalize(br.requestForBase(b.id, i), dataThrough);
      out.push({ base_id: b.id, choice: i, hash: await pr.paramsHash(p), normalized: p });
    }
  }
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { console.error(e); process.exit(1); });
"""


def run_ts():
    app = os.path.abspath(APP_DIR)
    npx = shutil.which("npx") or "npx"
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "base_keys.ts")
        with open(path, "w", encoding="utf-8") as f:
            f.write(TS_CODE)
        env = dict(os.environ, RULETRADE_APP_DIR_RESOLVED=app, DATA_THROUGH=DATA_THROUGH)
        return subprocess.run(
            [npx, "tsx", "--tsconfig", os.path.join(app, "tsconfig.json"), path],
            cwd=app, env=env, capture_output=True, text=True, encoding="utf-8")


def main():
    proc = run_ts()
    if proc.returncode != 0:
        print("会員アプリ側の実行に失敗しました:\n" + (proc.stderr or "")[-1500:])
        return 1
    ts = json.loads(proc.stdout.strip().splitlines()[-1])

    # ★数が合うこと★（片方だけ選択肢が増えた・減ったを見逃さない。0件を合格にしない）
    failures = 0
    if len(ts) == 0:
        print("NG   会員アプリ側から1件も取れませんでした")
        return 1
    if len(ts) != len(BASE_RULES_WARM):
        print("NG   数が違います（会員アプリ %d 通り / warm_cache %d 通り）"
              % (len(ts), len(BASE_RULES_WARM)))
        failures += 1

    py = {(r["base_id"], r["choice"]): r for r in BASE_RULES_WARM}
    for t in ts:
        name = "%s 選択肢%d" % (t["base_id"], t["choice"])
        r = py.get((t["base_id"], t["choice"]))
        if r is None:
            print("NG   %s が warm_cache にありません" % name)
            failures += 1
            continue
        p = fixed_rule_params(r, DATA_THROUGH)
        got = cache_key.params_hash(p)
        ok = got == t["hash"]
        print("%s %s" % ("OK  " if ok else "NG  ", name))
        if not ok:
            failures += 1
            print("     会員アプリ  : %s" % t["hash"])
            print("     warm_cache  : %s" % got)
            print("     会員アプリの正規化: %s" % json.dumps(t["normalized"], ensure_ascii=False))
            print("     warm_cache の文字列: %s" % cache_key.canonical(p))

    print("\n判定: %s" % ("合格（%d 通りのキーが一致）" % len(ts) if failures == 0
                        else "不合格（%d 件）" % failures))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
