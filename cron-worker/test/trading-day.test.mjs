/**
 * tools/trading-day.mjs の試験。
 *
 *   node --test test/
 *
 * ★ここで守りたいのは「1日ずれ」★
 *   isTradingDay() は JSTの壁時計をUTCとして持つ Date を取る。
 *   呼び方を間違えると**答えだけ合う日がある**ので気づきにくい
 *   （2026-09-21 に実際に踏んだ：9/21 を +09:00 で渡すと 9/20 の日曜を見て、
 *     同じ「休場」という答えになった）。
 *   祝日と、その前後の平日を並べて確かめる。
 */
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { isKnownYear, isTradingDay, ymd } from "../src/holidays.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLI = path.join(HERE, "..", "tools", "trading-day.mjs");

/** CLI を1回まわして {code, out} を返す */
function run(...args) {
  try {
    const out = execFileSync(process.execPath, [CLI, ...args], {
      encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, out };
  } catch (e) {
    return { code: e.status, out: (e.stdout || "") + (e.stderr || "") };
  }
}

test("祝日と前後の平日を取り違えない（1日ずれの検出）", () => {
  // 9/18(金)営業 → 9/19(土)9/20(日)休 → 9/21〜23 祝日 → 9/24(木)営業
  const want = {
    "2026-09-17": true, "2026-09-18": true,
    "2026-09-19": false, "2026-09-20": false,
    "2026-09-21": false, "2026-09-22": false, "2026-09-23": false,
    "2026-09-24": true, "2026-09-25": true,
  };
  for (const [day, open] of Object.entries(want)) {
    const r = run(day);
    assert.equal(r.code, open ? 0 : 1, `${day} の終了コード（出力: ${r.out.trim()}）`);
    assert.match(r.out, open ? /営業日/ : /休場/, `${day} の表示`);
  }
});

test("★1日ずらした呼び方だと答えが変わる（対照）★", () => {
  // 間違った渡し方: +09:00 を付けると中では前日として判定される
  const wrong = new Date("2026-09-24T00:00:00+09:00");
  const right = new Date("2026-09-24T00:00:00Z");
  assert.equal(ymd(right), "2026-09-24");
  assert.equal(ymd(wrong), "2026-09-23", "間違った渡し方は前日を見てしまう");
  assert.equal(isTradingDay(right), true);
  assert.equal(isTradingDay(wrong), false, "同じ日を聞いたのに答えが変わる");
});

test("範囲で並べられる", () => {
  const r = run("2026-09-17", "2026-09-25");
  assert.equal(r.code, 0);
  assert.equal(r.out.trim().split("\n").length, 10); // 9日＋合計の行
  assert.match(r.out, /営業日 4 日/);
});

test("直近／次の営業日", () => {
  assert.match(run("2026-09-21", "--prev").out, /2026-09-18/);
  assert.match(run("2026-09-21", "--next").out, /2026-09-24/);
});

test("★表に無い年は答えずに止まる★", () => {
  // 2027年は元日しか埋まっていない。営業日と答えてしまうより、止まるほうがよい
  const r = run("2027-01-11");           // 成人の日（表に無い）
  assert.equal(r.code, 2, `出力: ${r.out.trim()}`);
  assert.match(r.out, /表にありません/);
  assert.equal(isKnownYear("2027-01-11"), false);
});

test("壊れた入力は 2 で落ちる", () => {
  for (const bad of ["9/24", "2026-02-30", "--nope"]) {
    assert.equal(run(bad).code, 2, `入力: ${bad}`);
  }
  assert.equal(run("2026-09-25", "2026-09-17").code, 2, "逆順の範囲");
});

test("--quiet は終了コードだけ返す", () => {
  const ng = run("--quiet", "2026-09-21");
  assert.equal(ng.code, 1);
  assert.equal(ng.out.trim(), "");
  assert.equal(run("--quiet", "2026-09-24").code, 0);
});
