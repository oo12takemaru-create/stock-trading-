/**
 * 米国株ヒートマップ（us_market.js）の時刻表の自己点検（2026-10-24）
 *
 *   1. ET の時刻が既存の cron のどれかで発火する（cron を増やしていない）
 *   2. 夏時間・冬時間の切替で ET の時刻が正しく出る
 *   3. 米国の休場日・週末は起こさない／東証の休場日でも起こす
 *   4. 休場日の表が収集側（us_bizday.py）と同じ
 *   5. heatmap-us.yml が実在し workflow_dispatch を受け、NO_DEDUP に入っている
 *
 *   node --test cron-worker/test/us.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { US_PLAN, US_HOLIDAYS, isUsDst, toEt, etHhmm, usTargetsFor, US_WORKFLOW } from "../src/us_market.js";
import { NO_DEDUP } from "../src/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOML = readFileSync(join(HERE, "../wrangler.toml"), "utf8");
const WF = join(HERE, "../../.github/workflows");

function crons() {
  const m = TOML.match(/crons\s*=\s*\[([\s\S]*?)\]/);
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

test("ET の時刻は毎時発火する cron の分に入っている（ET と UTC の差は整数時間）", () => {
  const minutes = new Set();
  for (const c of crons()) {
    const [fm, fh, , , fd] = c.split(/\s+/);
    if (fh !== "*" || fd !== "*") continue; // 時・曜日を絞っている cron は数えない
    fm.split(",").forEach((x) => minutes.add(+x));
  }
  for (const t of Object.keys(US_PLAN)) {
    assert.ok(minutes.has(+t.slice(3)), `US_PLAN の ${t} は発火しません（毎時の cron の分は ${[...minutes]}）`);
  }
});

test("夏時間の判定（2026: 3/8〜11/1）", () => {
  assert.equal(isUsDst(new Date("2026-03-08T06:59:00Z")), false, "切替前");
  assert.equal(isUsDst(new Date("2026-03-08T07:00:00Z")), true, "3月第2日曜 2:00 EST");
  assert.equal(isUsDst(new Date("2026-10-23T20:15:00Z")), true);
  assert.equal(isUsDst(new Date("2026-11-01T05:59:00Z")), true, "切替前");
  assert.equal(isUsDst(new Date("2026-11-01T06:00:00Z")), false, "11月第1日曜 2:00 EDT");
  assert.equal(isUsDst(new Date("2027-03-14T07:00:00Z")), true, "2027 は 3/14");
  assert.equal(etHhmm(toEt(new Date("2026-10-23T20:15:00Z"))), "16:15", "夏: UTC-4");
  assert.equal(etHhmm(toEt(new Date("2026-11-02T21:15:00Z"))), "16:15", "冬: UTC-5");
});

const at = (iso) => usTargetsFor(new Date(iso)).targets;

test("引け後の確定値（16:15 ET）は 日本時間 夏5:15・冬6:15 に起こす", () => {
  // 2026-10-23（金・夏時間）16:15 EDT = 20:15 UTC = 10/24（土）05:15 JST
  assert.deepEqual(at("2026-10-23T20:15:00Z"), [US_WORKFLOW]);
  // 2026-11-02（月・冬時間）16:15 EST = 21:15 UTC = 11/3（火・文化の日＝東証休場）06:15 JST
  assert.deepEqual(at("2026-11-02T21:15:00Z"), [US_WORKFLOW]);
  // 場中 9:45 ET（冬）= 14:45 UTC = 23:45 JST、12:30 ET（冬）= 17:30 UTC = 2:30 JST
  assert.deepEqual(at("2026-11-02T14:45:00Z"), [US_WORKFLOW]);
  assert.deepEqual(at("2026-11-02T17:30:00Z"), [US_WORKFLOW]);
  assert.deepEqual(at("2026-11-02T23:00:00Z"), [US_WORKFLOW], "18:00 ET の予備");
  // 時刻表に無い分は起こさない
  assert.deepEqual(at("2026-11-02T21:00:00Z"), []);
  assert.deepEqual(at("2026-11-02T20:15:00Z"), [], "冬の 20:15 UTC は 15:15 ET（場中・表に無い）");
});

test("米国の休場日・週末は起こさない", () => {
  assert.deepEqual(at("2026-11-26T21:15:00Z"), [], "感謝祭");
  assert.deepEqual(at("2026-04-03T20:15:00Z"), [], "グッドフライデー");
  assert.deepEqual(at("2026-10-24T20:15:00Z"), [], "土曜（ET）");
  assert.deepEqual(at("2026-10-25T14:45:00Z"), [], "日曜（ET）");
  // 東証の休場日（スポーツの日 10/12）でも米国は開いている
  assert.deepEqual(at("2026-10-12T13:45:00Z"), [US_WORKFLOW], "東証休場・米国は営業（9:45 EDT）");
});

test("休場日の表が収集側の us_bizday.py と同じ", () => {
  const py = readFileSync(join(HERE, "../../us_bizday.py"), "utf8");
  const body = py.slice(py.indexOf("US_HOLIDAYS = {"), py.indexOf("}", py.indexOf("US_HOLIDAYS = {")));
  const pyset = new Set([...body.matchAll(/"(\d{4}-\d{2}-\d{2})"/g)].map((m) => m[1]));
  assert.deepEqual([...US_HOLIDAYS].sort(), [...pyset].sort());
});

test("heatmap-us.yml は実在し、workflow_dispatch を受け、NO_DEDUP に入っている", () => {
  const y = readFileSync(join(WF, US_WORKFLOW), "utf8");
  assert.match(y, /workflow_dispatch:/);
  assert.ok(NO_DEDUP.has(US_WORKFLOW), "1日4回走るのが正常なので NO_DEDUP に入れる");
});
