// node --test "test/*.test.mjs"
import test from "node:test";
import assert from "node:assert/strict";
import { build, buildOne, cadence, tzOffset, sig, rnd, groups, ITEMS } from "../src/build.js";

const J = (s) => Date.parse(s);   // "2026-11-20T10:00:00+09:00" など
function spark(symbol, tsList, closes, meta) {
  return { symbol, response: [{ meta: { symbol, ...meta }, timestamp: tsList.map((x) => x / 1000), indicators: { quote: [{ close: closes }] } }] };
}
function series(fromMs, n, ivMin, start, step) {
  const t = [], c = [];
  for (let i = 0; i < n; i++) { t.push(fromMs + i * ivMin * 60000); c.push(start + i * step); }
  return { t, c };
}

test("間隔: 平日の日本の取引時間は5分・夜は15分・週末は60分", () => {
  assert.equal(cadence(J("2026-11-20T08:44:00+09:00")), 15);
  assert.equal(cadence(J("2026-11-20T08:45:00+09:00")), 5);
  assert.equal(cadence(J("2026-11-20T15:44:00+09:00")), 5);
  assert.equal(cadence(J("2026-11-20T15:45:00+09:00")), 15);
  assert.equal(cadence(J("2026-11-21T06:30:00+09:00")), 15);   // 土曜の朝（米国の金曜の引けまで）
  assert.equal(cadence(J("2026-11-21T07:00:00+09:00")), 60);
  assert.equal(cadence(J("2026-11-22T12:00:00+09:00")), 60);
  assert.equal(cadence(J("2026-11-23T08:44:00+09:00")), 60);   // 月曜の朝
});

test("夏時間", () => {
  assert.equal(tzOffset("America/New_York", J("2026-07-01T00:00:00Z")), -240);
  assert.equal(tzOffset("America/New_York", J("2026-11-20T00:00:00Z")), -300);
  assert.equal(tzOffset("Europe/London", J("2026-07-01T00:00:00Z")), 60);
  assert.equal(tzOffset("Europe/Berlin", J("2026-11-20T00:00:00Z")), 60);
});

test("丸めは market_board_1d.py と同じ", () => {
  assert.equal(sig(56436.6484375), 56437);
  assert.equal(sig(1.098234), 1.0982);
  assert.equal(rnd(159.2345, "fx"), 159.23);
  assert.equal(rnd(5.0523, "rate"), 5.052);
});

test("日経平均: 取引の途中は fin=0・時刻は取得元の値の時刻・前日終値は収集側のファイルを正に", () => {
  const now = J("2026-11-20T10:31:00+09:00");
  const s = series(J("2026-11-20T09:00:00+09:00"), 18, 5, 100, 1);   // 9:00〜10:25
  const res = spark("^N225", s.t, s.c, { regularMarketPrice: 118.5, regularMarketTime: J("2026-11-20T10:27:00+09:00") / 1000, chartPreviousClose: 90, regularMarketDayHigh: 119, regularMarketDayLow: 99 });
  const base = { n225: { id: "n225", ok: 1, d: "2026-11-19", fin: 1, pc: 95, last: 100 } };
  const r = buildOne(ITEMS[0], { t: s.t.map((x) => x / 1000), c: s.c, meta: res.response[0].meta }, 5, now, base);
  assert.equal(r.ok, 1);
  assert.equal(r.fin, 0);
  assert.equal(r.d, "2026-11-20");
  assert.equal(r.tj, "2026-11-20T10:27+09:00");
  assert.equal(r.pc, 100);                // 前の取引日の引けまでの last
  assert.equal(r.last, 118.5);
  assert.equal(r.pct, 18.5);
  assert.equal(r.c.length, 18);           // 今の足の中なので置きかえ（点は増えない）
  assert.equal(r.c[17], 118.5);
});

test("日経平均: 引け後は fin=1・「15:30まで」", () => {
  const now = J("2026-11-20T16:00:00+09:00");
  const s = series(J("2026-11-20T09:00:00+09:00"), 78, 5, 100, 0.1);   // 9:00〜15:25 と 15:30 の足
  const meta = { regularMarketPrice: 108, regularMarketTime: J("2026-11-20T15:45:00+09:00") / 1000, chartPreviousClose: 101 };
  const r = buildOne(ITEMS[0], { t: s.t.map((x) => x / 1000), c: s.c, meta }, 5, now, null);
  assert.equal(r.fin, 1);
  assert.equal(r.te, "15:30");
  assert.equal(r.last, 108);
  assert.equal(r.pc, 101);
});

test("ドル円: 直近24時間・基準は24時間前", () => {
  const now = J("2026-11-20T22:00:00+09:00");
  const s = series(J("2026-11-19T09:00:00+09:00"), 150, 15, 150, 0.05);
  const meta = { regularMarketPrice: 157.5, regularMarketTime: J("2026-11-20T21:58:00+09:00") / 1000 };
  const r = buildOne(ITEMS[15], { t: s.t.map((x) => x / 1000), c: s.c, meta }, 15, now, { usdjpy: { ok: 1, d: "2026-11-20", pc: 1 } });
  assert.equal(r.ok, 1);
  assert.equal(r.roll, 24);
  assert.equal(r.fin, 0);
  assert.ok(r.c.length <= 97);
  assert.notEqual(r.pc, 1);               // roll は収集側の値で上書きしない
});

test("全体: 取れない本は ok=0 で残す・2つの呼び方に全部の記号が入る", () => {
  const g = groups();
  assert.equal(g[0].symbols.length + g[1].symbols.length, ITEMS.length);
  const out = build([{ spark: { result: [] } }, { err: 429 }], J("2026-11-20T10:00:00+09:00"), null);
  assert.equal(out.ok, 0);
  assert.equal(out.items.length, ITEMS.length);
  assert.equal(out.every, 5);
});
