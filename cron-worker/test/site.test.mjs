/**
 * サイト（kaburadar.jp）の作り直しの起動（src/site.js・2026-11-21）の点検。
 *
 * 守りたいこと:
 *   1. SITE_PLAN の時刻に、wrangler.toml の cron のどれかが**その曜日に**発火する（書いても動かない、を防ぐ）。
 *   2. 平日の休場日は夜・引け後の回を起こさず、朝（AI朝刊）と土日は起こす。
 *   3. 起動先は kaburadar の site-rebuild.yml・ブランチ master。トークンは GH_PAT_SITE を優先する。
 *   4. ここが失敗・例外でも、既存の起動（収集側）は今までどおり動く。
 *   5. 日曜 07:00 に週末版の取り込み（ai-weekly-ingest）を入力なしで起こし、07:15 に作り直す。
 *
 *   cd cron-worker; npm test
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { isTradingDay } from "../src/holidays.js";
import { siteTargetsFor, SITE_PLAN, SITE_REPO, SITE_REF, SITE_WORKFLOW } from "../src/site.js";
import worker, { runSiteRebuild, targetsFor } from "../src/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const TOML = readFileSync(join(HERE, "../wrangler.toml"), "utf8");

/** JST の "YYYY-MM-DD HH:MM" → UTC の Date */
const utcOf = (s) => new Date(Date.parse(s.replace(" ", "T") + ":00Z") - 9 * 3600 * 1000);

function crons() {
  const m = TOML.match(/crons\s*=\s*\[([\s\S]*?)\]/);
  assert.ok(m, "wrangler.toml の crons を読み取れません");
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

/** JST の "HH:MM"（曜日 dow・0=日）に cron が発火するか（plan.test.mjs と同じ。Cloudflare の曜日は 1=日〜7=土） */
function fires(key, dow) {
  const [h, mi] = key.split(":").map(Number);
  const utcH = (h - 9 + 24) % 24;
  const utcDow0 = h < 9 ? (dow + 6) % 7 : dow;
  const cfDow = utcDow0 + 1;
  return crons().some((c) => {
    const [fm, fh, , , fd] = c.split(/\s+/);
    const inField = (field, v) =>
      field === "*" ||
      field.split(",").some((part) => {
        const r = part.match(/^(\d+)-(\d+)$/);
        return r ? v >= +r[1] && v <= +r[2] : +part === v;
      });
    return inField(fm, mi) && inField(fh, utcH) && inField(fd, cfDow);
  });
}

test("SITE_PLAN の時刻は、書いた曜日すべてで cron が発火する", () => {
  for (const [key, p] of Object.entries(SITE_PLAN)) {
    assert.ok(p.days.length > 0, `${key} の曜日が空です`);
    for (const dow of p.days) {
      assert.ok(fires(key, dow), `サイトの作り直し ${key}（曜日 ${dow}）は cron が発火しないので永久に走りません`);
    }
  }
});

test("土日に置いた回は holidays: true（土日は東証の休場日なので、false だと動かない）", () => {
  for (const [key, p] of Object.entries(SITE_PLAN)) {
    if (p.days.some((d) => d === 0 || d === 6)) assert.equal(p.holidays, true, `${key} は土日に置いたのに holidays: false`);
  }
});

test("平日（営業日）は 06:45・16:15・20:00・21:30 の4回", () => {
  const on = [];
  for (let h = 0; h < 24; h++) {
    for (const mi of [0, 5, 15, 30, 35, 40, 45]) {
      const k = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
      if (siteTargetsFor(utcOf(`2026-11-25 ${k}`), isTradingDay).run) on.push(k); // 水曜
    }
  }
  assert.deepEqual(on, ["06:45", "16:15", "20:00", "21:30"]);
});

test("平日の休場日（2026-11-23 勤労感謝の日）は朝の回だけ", () => {
  const at = (k) => siteTargetsFor(utcOf(`2026-11-23 ${k}`), isTradingDay).run;
  assert.equal(at("06:45"), true, "AI朝刊は休場日も出るので朝は起こす");
  assert.equal(at("16:15"), false);
  assert.equal(at("20:00"), false);
  assert.equal(at("21:30"), false);
});

test("土曜は 08:30、日曜は 07:15 だけ", () => {
  const day = (d) => {
    const on = [];
    for (const k of Object.keys(SITE_PLAN)) if (siteTargetsFor(utcOf(`${d} ${k}`), isTradingDay).run) on.push(k);
    return on;
  };
  assert.deepEqual(day("2026-11-28"), ["08:30"], "土曜");
  assert.deepEqual(day("2026-11-29"), ["07:15"], "日曜");
});

test("日曜 07:00 に週末版の取り込みを起こし、それより後に作り直す", () => {
  const sun07 = new Date(Date.parse("2026-11-29T07:00:00Z")); // JST の壁時計として
  assert.deepEqual(targetsFor(sun07).targets, ["ai-weekly-ingest.yml"]);
  const sat07 = new Date(Date.parse("2026-11-28T07:00:00Z"));
  assert.ok(!targetsFor(sat07).targets.includes("ai-weekly-ingest.yml"), "土曜 07:00 には起こさない");
  const sunRebuild = Object.entries(SITE_PLAN).find(([, p]) => p.days.includes(0))[0];
  assert.ok(sunRebuild > "07:00", `日曜の作り直し ${sunRebuild} が取り込み（07:00）より前です`);
});

/* ── 起動そのもの（fetch を差し替える。GitHub は叩かない） ── */

function stubFetch(handler) {
  const calls = [];
  const orig = globalThis.fetch;
  globalThis.fetch = async (url, opt = {}) => {
    calls.push({ url: String(url), opt });
    return handler(String(url), opt);
  };
  return { calls, restore: () => { globalThis.fetch = orig; } };
}
const quietly = async (fn) => {
  const [l, e, w] = [console.log, console.error, console.warn];
  console.log = console.error = console.warn = () => {};
  try { return await fn(); } finally { [console.log, console.error, console.warn] = [l, e, w]; }
};

test("kaburadar の site-rebuild.yml を master で起動し、GH_PAT_SITE を優先する", async () => {
  const s = stubFetch(async () => ({ status: 204, ok: true, text: async () => "" }));
  try {
    const r = await quietly(() => runSiteRebuild(utcOf("2026-11-25 20:00"), { GH_PAT: "A", GH_PAT_SITE: "B" }));
    assert.equal(r.ok, true);
    assert.equal(s.calls.length, 1);
    assert.equal(
      s.calls[0].url,
      `https://api.github.com/repos/${SITE_REPO}/actions/workflows/${SITE_WORKFLOW}/dispatches`,
    );
    assert.equal(SITE_REPO, "oo12takemaru-create/kaburadar");
    assert.deepEqual(JSON.parse(s.calls[0].opt.body), { ref: SITE_REF });
    assert.equal(SITE_REF, "master");
    assert.equal(s.calls[0].opt.headers.Authorization, "Bearer B");
  } finally { s.restore(); }
});

test("GH_PAT_SITE が無ければ GH_PAT で起動する", async () => {
  const s = stubFetch(async () => ({ status: 204, ok: true, text: async () => "" }));
  try {
    await quietly(() => runSiteRebuild(utcOf("2026-11-25 20:00"), { GH_PAT: "A" }));
    assert.equal(s.calls[0].opt.headers.Authorization, "Bearer A");
  } finally { s.restore(); }
});

test("予定の無い時刻は何も叩かない", async () => {
  const s = stubFetch(async () => { throw new Error("叩いてはいけない"); });
  try {
    const r = await quietly(() => runSiteRebuild(utcOf("2026-11-25 20:30"), { GH_PAT: "A" }));
    assert.equal(r.ran, false);
    assert.equal(s.calls.length, 0);
  } finally { s.restore(); }
});

test("404（トークンが kaburadar を見られない）ならメールで知らせ、例外にしない", async () => {
  const s = stubFetch(async (url) =>
    url.includes("api.resend.com")
      ? { ok: true, status: 200, text: async () => "" }
      : { status: 404, ok: false, text: async () => '{"message":"Not Found"}' });
  try {
    const r = await quietly(() =>
      runSiteRebuild(utcOf("2026-11-25 20:00"), { GH_PAT: "A", RESEND_API_KEY: "k", NOTIFY_TO: "x@example.com" }));
    assert.equal(r.ok, false);
    assert.equal(r.status, 404);
    const mail = s.calls.find((c) => c.url.includes("api.resend.com"));
    assert.ok(mail, "メールが出ていない");
    assert.match(JSON.parse(mail.opt.body).text, /Repository access に kaburadar/);
  } finally { s.restore(); }
});

test("サイト側の起動が例外でも、既存の起動（収集側）は今までどおり走る", async () => {
  // 平日 20:00 は収集側の board.yml とサイトの作り直しが同じ時刻
  const s = stubFetch(async (url) => {
    if (url.includes("/repos/oo12takemaru-create/kaburadar/")) throw new Error("network down");
    if (url.includes("api.resend.com")) return { ok: true, status: 200, text: async () => "" };
    return { status: 204, ok: true, text: async () => "" };
  });
  const pending = [];
  try {
    await quietly(async () => {
      await worker.scheduled(
        { scheduledTime: utcOf("2026-11-25 20:00").getTime() },
        { GH_PAT: "A", RESEND_API_KEY: "k", NOTIFY_TO: "x@example.com" },
        { waitUntil: (p) => pending.push(p) },
      );
      await Promise.all(pending);
    });
    const board = s.calls.find((c) => c.url.includes("/stock-trading-/actions/workflows/board.yml/dispatches"));
    assert.ok(board, "収集側の board.yml が起動されていない");
    assert.ok(s.calls.some((c) => c.url.includes("/kaburadar/actions/workflows/site-rebuild.yml/dispatches")));
  } finally { s.restore(); }
});
