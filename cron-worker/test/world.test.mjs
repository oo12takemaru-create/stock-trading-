/**
 * 世界の値動きの地図（world.js）の時刻表の自己点検（2026-10-30）
 *
 *   1. JST の時刻が既存の cron（:00/:30）で発火する（cron を増やしていない）
 *   2. 曜日: 07:00 は火〜土、16:00・23:00 は月〜金
 *   3. 東証の休場日（例: 11/3 文化の日）でも起こす
 *   4. world.yml が実在し workflow_dispatch を受け、NO_DEDUP に入っている
 *
 *   node --test cron-worker/test/world.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { WORLD_PLAN, WORLD_WORKFLOW, worldTargetsFor } from "../src/world.js";
import { NO_DEDUP } from "../src/index.js";
import { isTradingDay } from "../src/holidays.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOML = readFileSync(join(HERE, "../wrangler.toml"), "utf8");
const WF = join(HERE, "../../.github/workflows");
// JST の "YYYY-MM-DDTHH:MM" → UTC の Date
const at = (s) => new Date(new Date(s + ":00Z").getTime() - 9 * 3600 * 1000);

test("JST の時刻は毎時発火する cron の分に入っている", () => {
  const m = TOML.match(/crons\s*=\s*\[([\s\S]*?)\]/);
  const minutes = new Set();
  for (const c of [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1])) {
    const [fm, fh, , , fd] = c.split(/\s+/);
    if (fh !== "*" || fd !== "*") continue;
    fm.split(",").forEach((x) => minutes.add(+x));
  }
  for (const t of Object.keys(WORLD_PLAN)) assert.ok(minutes.has(+t.slice(3)), `WORLD_PLAN の ${t} は発火しません`);
});

test("曜日: 07:00 は火〜土、16:00・23:00 は月〜金", () => {
  // 2026-11-02 が月曜
  const days = ["2026-11-01", "2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"]; // 日〜土
  const want = { "07:00": [0, 0, 1, 1, 1, 1, 1], "16:00": [0, 1, 1, 1, 1, 1, 0], "23:00": [0, 1, 1, 1, 1, 1, 0] };
  for (const [t, w] of Object.entries(want)) {
    days.forEach((d, i) => {
      const r = worldTargetsFor(at(`${d}T${t}`));
      assert.equal(r.targets.length, w[i], `${d} ${t}: ${JSON.stringify(r)}`);
    });
  }
  assert.equal(worldTargetsFor(at("2026-11-04T07:30")).targets.length, 0, "予定に無い時刻は起こさない");
});

test("東証の休場日（2026-11-03 文化の日）でも起こす", () => {
  assert.equal(isTradingDay(new Date(Date.UTC(2026, 10, 3))), false, "前提: 11/3 は東証の休場日");
  for (const t of ["07:00", "16:00", "23:00"]) {
    assert.deepEqual(worldTargetsFor(at(`2026-11-03T${t}`)).targets, [WORLD_WORKFLOW]);
  }
});

test("world.yml が実在し workflow_dispatch を受け、NO_DEDUP に入っている", () => {
  const y = readFileSync(join(WF, WORLD_WORKFLOW), "utf8");
  assert.match(y, /workflow_dispatch:/);
  assert.ok(NO_DEDUP.has(WORLD_WORKFLOW), "1日3回走るのが正常なので NO_DEDUP に入れる");
});
