/**
 * 時刻表（PLAN）の自己点検。
 *
 * ここで守りたいこと:
 *   1. cron は「毎時 :00 と :30」なので、それ以外の時刻を書いても**発火しない**。
 *      書き間違えると黙って動かないだけなので、機械で止める。
 *   2. 起動対象の yml が実在すること（綴り間違いは 404 になって初めて分かる）。
 *   3. 休場日・土日の判定が意図どおりであること。
 *   4. wrangler.toml の cron と PLAN・INTRADAY が食い違っていないこと。
 *      （cron を後から減らすと、PLAN はそのままで黙って発火しなくなる）
 *   5. 予備の回（BACKUP_AT）の受け皿が yml 側にあること。
 *      （入力名がずれると GitHub は 422 を返すだけで、二重通知は黙って戻る）
 *
 *   node --test cron-worker/test/plan.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { isTradingDay, isKnownYear, ymd } from "../src/holidays.js";
import { targetsFor, inIntraday } from "../src/index.js";

const HERE = dirname(fileURLToPath(import.meta.url));

/**
 * ※ 行コメントを落としてから読む。
 *   このファイルは index.js を**文字列として**見ているので、
 *   コメントアウトした行を「まだ有る」と誤認する。
 *   （実際、注入テストで NO_DEDUP のコメントアウトを見逃した）
 *   URL の "//" は落とさない（: の直後は除く）。実際 https:// に当たった。
 */
const stripComments = (t) =>
  t.split("\n").map((l) => l.replace(/(^|[^:])\/\/.*$/, "$1").trimEnd()).join("\n");

const SRC = stripComments(readFileSync(join(HERE, "../src/index.js"), "utf8"));
const WORKFLOW_DIR = join(HERE, "../../.github/workflows");
const TOML = readFileSync(join(HERE, "../wrangler.toml"), "utf8");
const wf = (n) => readFileSync(join(WORKFLOW_DIR, n), "utf8");

/** index.js から時刻表を読み出す（実装と二重に持たないため正規表現で拾う） */
function plans() {
  const out = {};
  for (const name of ["PLAN", "WEEKEND_PLAN", "WEEKDAY_ONLY_PLAN"]) {
    const m = SRC.match(new RegExp(`const ${name} = (\\{[\\s\\S]*?\\n\\});`));
    assert.ok(m, `${name} を読み取れません`);
    out[name] = m[1];
  }
  return out;
}

const P = plans();
const timesIn = (src) => [...src.matchAll(/"(\d{2}:\d{2})":/g)].map((m) => m[1]);
const filesIn = (src) => [...new Set([...src.matchAll(/"([\w.-]+\.yml)"/g)].map((m) => m[1]))];

test("時刻は :00 か :30 だけ（cron が毎時0分・30分のため）", () => {
  for (const [name, src] of Object.entries(P)) {
    for (const t of timesIn(src)) {
      const mm = t.slice(3);
      assert.ok(
        mm === "00" || mm === "30",
        `${name} の ${t} は発火しません（cron は :00 と :30 のみ）`,
      );
    }
  }
});

test("起動対象の yml が実在する", () => {
  const existing = new Set(readdirSync(WORKFLOW_DIR));
  const missing = [];
  for (const [name, src] of Object.entries(P)) {
    for (const f of filesIn(src)) {
      if (!existing.has(f)) missing.push(`${name}: ${f}`);
    }
  }
  assert.deepEqual(missing, [], `存在しないワークフローを起動しようとしています:\n${missing.join("\n")}`);
});

test("休場日の判定", () => {
  const day = (s) => new Date(`${s}T00:00:00Z`); // JST の壁時計として扱う
  assert.equal(isTradingDay(day("2026-09-09")), true, "水曜は営業日");
  assert.equal(isTradingDay(day("2026-09-12")), false, "土曜は休み");
  assert.equal(isTradingDay(day("2026-09-13")), false, "日曜は休み");
  assert.equal(isTradingDay(day("2026-09-21")), false, "敬老の日は休場");
  assert.equal(isTradingDay(day("2026-01-01")), false, "元日は休場");
  assert.equal(isKnownYear("2026-09-09"), true);
  assert.equal(isKnownYear("2030-01-05"), false, "表の範囲外は false（ログで気づけるように）");
});

test("JST の日付が正しく出る", () => {
  // UTC 2026-09-09 15:30 → JST 2026-09-10 00:30
  const jst = new Date(Date.parse("2026-09-09T15:30:00Z") + 9 * 3600 * 1000);
  assert.equal(ymd(jst), "2026-09-10");
});

test("同じ時刻に同じワークフローを二重に書いていない", () => {
  for (const [name, src] of Object.entries(P)) {
    for (const m of src.matchAll(/"(\d{2}:\d{2})": \[([^\]]*)\]/g)) {
      const items = [...m[2].matchAll(/"([\w.-]+\.yml)"/g)].map((x) => x[1]);
      assert.equal(
        new Set(items).size, items.length,
        `${name} の ${m[1]} に同じワークフローが重複しています`,
      );
    }
  }
});

/* ─────────────────────────────────────────────
   cron 式と時刻表の整合
   ───────────────────────────────────────────── */

/** wrangler.toml の [triggers] から cron 式を取り出す */
function crons() {
  const m = TOML.match(/crons\s*=\s*\[([\s\S]*?)\]/);
  assert.ok(m, "wrangler.toml の crons を読み取れません");
  return [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]);
}

/** JST の "HH:MM"（曜日 dow）に、いずれかの cron が発火するか */
function fires(key, dow) {
  const [h, mi] = key.split(":").map(Number);
  const utcH = (h - 9 + 24) % 24;
  // JST 0:00〜8:59 は前日の UTC。曜日も1つ戻る
  const utcDow = h < 9 ? (dow + 6) % 7 : dow;
  return crons().some((c) => {
    const [fm, fh, , , fd] = c.split(/\s+/);
    const inField = (field, v) =>
      field === "*" ||
      field.split(",").some((part) => {
        const r = part.match(/^(\d+)-(\d+)$/);
        return r ? v >= +r[1] && v <= +r[2] : +part === v;
      });
    return inField(fm, mi) && inField(fh, utcH) && inField(fd, utcDow);
  });
}

test("PLAN に書いた時刻は必ずどれかの cron で発火する", () => {
  // 水曜（dow=3）で確かめる。PLAN は平日共通
  for (const [name, src] of Object.entries(P)) {
    for (const t of timesIn(src)) {
      assert.ok(fires(t, 3), `${name} の ${t} は cron が発火しないので永久に走りません`);
    }
  }
});

test("ザラ場は 15 分間隔で発火する（:00 :15 :30 :45）", () => {
  const got = [];
  for (let h = 9; h <= 16; h++) {
    for (const mi of [0, 15, 30, 45]) {
      const key = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
      if (!inIntraday(key)) continue;
      assert.ok(fires(key, 3), `ザラ場の ${key} が発火しません（cron が足りない）`);
      got.push(key);
    }
  }
  // 09:00〜16:15 を 15 分刻み = 30 回
  // （2026-09-10 に 15:45 → 16:15 へ延長。引継ぎ.md §23 相談⑪(2)）
  assert.equal(got.length, 30, `ザラ場の発火回数が 30 ではありません: ${got.length}`);
});

test("realtime-signal は PLAN に書かれていない（ザラ場枠と二重になる）", () => {
  for (const [name, src] of Object.entries(P)) {
    assert.ok(
      !src.includes("realtime-signal.yml"),
      `${name} に realtime-signal.yml があります。ザラ場枠（INTRADAY）と二重に走ります`,
    );
  }
});

test("ザラ場の時刻に realtime-signal が入り、時間外には入らない", () => {
  const at = (key, dow = 3) => {
    const [h, mi] = key.split(":").map(Number);
    const d = new Date(Date.UTC(2026, 8, 9, h, mi)); // 2026-09-09 は水曜
    d.setUTCDate(d.getUTCDate() + (dow - 3));
    return targetsFor(d).targets;
  };
  assert.ok(at("09:00").includes("realtime-signal.yml"), "09:00 に入っていない");
  assert.ok(at("09:00").includes("heatmap.yml"), "09:00 の PLAN 分が消えている");
  assert.ok(at("15:30").includes("realtime-signal.yml"), "15:30 に入っていない");
  // 延長した2回（引継ぎ.md §23 相談⑪(2)）
  assert.ok(at("16:00").includes("realtime-signal.yml"), "16:00 に入っていない");
  assert.ok(at("16:15").includes("realtime-signal.yml"), "16:15 に入っていない");
  // ここから先は捨てる。18:00 のパイプラインが拾う
  assert.ok(!at("16:30").includes("realtime-signal.yml"), "16:30（対象外）に入っている");
  assert.ok(!at("16:45").includes("realtime-signal.yml"), "16:45（対象外）に入っている");
  assert.ok(!at("08:00").includes("realtime-signal.yml"), "08:00（寄り前）に入っている");
  assert.deepEqual(at("09:00", 6), [], "土曜は何も起こさない");
});

/* ─────────────────────────────────────────────
   予備の回（二重通知を出さないための入力）
   ───────────────────────────────────────────── */

test("BACKUP_AT の相手が PLAN に実在し、backup 入力を受け取れる", () => {
  const m = SRC.match(/const BACKUP_AT = (\{[\s\S]*?\n\});/);
  assert.ok(m, "BACKUP_AT を読み取れません");

  for (const e of m[1].matchAll(/"(\d{2}:\d{2})": new Set\(\[([^\]]*)\]\)/g)) {
    const [time, list] = [e[1], e[2]];
    for (const y of [...list.matchAll(/"([\w.-]+\.yml)"/g)].map((x) => x[1])) {
      const slot = [...P.PLAN.matchAll(/"(\d{2}:\d{2})": \[([^\]]*)\]/g)]
        .find((x) => x[1] === time);
      assert.ok(slot, `BACKUP_AT の ${time} が PLAN にありません`);
      assert.ok(
        slot[2].includes(`"${y}"`),
        `BACKUP_AT の ${time} ${y} が PLAN の同じ時刻にありません`,
      );
      // 入力名がずれていると GitHub は 422 を返すだけで、二重通知は黙って戻る
      assert.match(
        wf(y), /workflow_dispatch:[\s\S]*?inputs:[\s\S]*?backup:/,
        `${y} に workflow_dispatch の backup 入力がありません`,
      );
    }
  }
});

test("pipeline-daily は backup を daily-signal まで通している", () => {
  assert.match(
    wf("pipeline-daily.yml"), /uses: \.\/\.github\/workflows\/daily-signal\.yml\s+with:\s+backup: \$\{\{ inputs\.backup \}\}/,
    "pipeline-daily が daily-signal に backup を渡していません",
  );
  assert.match(
    wf("daily-signal.yml"), /workflow_call:\s+inputs:\s+backup:/,
    "daily-signal が workflow_call の backup 入力を持っていません",
  );
  assert.match(
    wf("daily-signal.yml"), /if \[ "\$\{\{ inputs\.backup \}\}" = "true" \]/,
    "daily-signal のガードが backup を見ていません",
  );
});

/* ─────────────────────────────────────────────
   workflow_call では event_name で見分けられない
   （呼び出し元のイベントが入る）ことを条件式で担保する
   ───────────────────────────────────────────── */

test("呼ばれる側の分岐に github.event_name == 'workflow_call' を使っていない", () => {
  const bad = [];
  for (const name of readdirSync(WORKFLOW_DIR)) {
    if (!name.endsWith(".yml")) continue;
    if (/event_name\s*==\s*'workflow_call'/.test(wf(name))) bad.push(name);
  }
  assert.deepEqual(
    bad, [],
    "event_name は呼び出し元のイベントになるため 'workflow_call' には決してなりません:\n" + bad.join("\n"),
  );
});

/* ─────────────────────────────────────────────
   重複確認（alreadyRanToday）の対象漏れ

   1日に複数回走るものを重複確認に乗せると、2回目以降が黙って消える。
   しかも「起動しなかった」は失敗として記録されないので誰も気づけない
   （2026-09-05 に free-scanner でこれが4日間放置された）。
   ───────────────────────────────────────────── */

/** const NAME = new Set([...]) の中身を拾う */
function setLiteral(name) {
  // ※ テンプレートリテラルの中に正規表現を書くとエスケープが1段消える。
  //   黙って別の正規表現になるので、ここは切り出しで書く。
  const head = `const ${name} = new Set([`;
  const i = SRC.indexOf(head);
  assert.notEqual(i, -1, `${name} を読み取れません`);
  const j = SRC.indexOf("]);", i);
  assert.notEqual(j, -1, `${name} の終わりを見つけられません`);
  const body = SRC.slice(i + head.length, j);
  return new Set([...body.matchAll(/"([\w.-]+\.yml)"/g)].map((x) => x[1]));
}

test("PLAN に複数回あるものは NO_DEDUP か DEDUP_ANYWAY に必ず入っている", () => {
  const count = {};
  for (const [, src] of Object.entries(P)) {
    for (const m of src.matchAll(/"(\d{2}:\d{2})": \[([^\]]*)\]/g)) {
      for (const y of [...m[2].matchAll(/"([\w.-]+\.yml)"/g)].map((x) => x[1])) {
        count[y] = (count[y] || 0) + 1;
      }
    }
  }
  const noDedup = setLiteral("NO_DEDUP");
  const anyway = setLiteral("DEDUP_ANYWAY");
  const missing = Object.entries(count)
    .filter(([y, n]) => n > 1 && !noDedup.has(y) && !anyway.has(y))
    .map(([y, n]) => `${y}（${n} 箇所）`);
  assert.deepEqual(
    missing, [],
    "重複確認で2回目以降が黙って消えます。1日複数回が正常なら NO_DEDUP、\n" +
      "二本目が予備なら DEDUP_ANYWAY に入れてください:\n" + missing.join("\n"),
  );
});

test("NO_DEDUP と DEDUP_ANYWAY は食い違わない", () => {
  const both = [...setLiteral("NO_DEDUP")].filter((y) => setLiteral("DEDUP_ANYWAY").has(y));
  assert.deepEqual(both, [], `両方に入っています: ${both.join(", ")}`);
});

test("daily-signal は 1日3回（朝・昼・夕）で、夕は pipeline-daily が担当", () => {
  // JST 15時以降はどれも slot が「夕」になるので、15時以降に daily-signal を
  // 直接置くと 18:00 の回と同じタイトルの Issue が2本立つ。
  const direct = [...P.PLAN.matchAll(/"(\d{2}:\d{2})": \[([^\]]*)\]/g)]
    .filter((m) => m[2].includes('"daily-signal.yml"'))
    .map((m) => m[1]);
  assert.deepEqual(direct, ["12:00", "08:00"], `直接起動の時刻が想定と違います: ${direct}`);
  assert.ok(
    P.PLAN.match(/"18:00": \[[^\]]*"pipeline-daily\.yml"/),
    "18:00 に pipeline-daily がありません（夕の担当が居ない）",
  );
});

/* ─────────────────────────────────────────────
   会員向けの朝の通知（引継ぎ.md §19 相談⑨）
   ───────────────────────────────────────────── */

/** MORNING_NOTIFY の中身を拾う */
function morningNotify() {
  const head = "const MORNING_NOTIFY = {";
  const i = SRC.indexOf(head);
  assert.notEqual(i, -1, "MORNING_NOTIFY を読み取れません");
  const body = SRC.slice(i, SRC.indexOf("};", i));
  const get = (k) => (body.match(new RegExp(k + ': "([^"]+)"')) || [])[1];
  return { at: get("at"), url: get("url"), gate: get("gate") };
}

test("朝の通知の時刻に cron が発火する", () => {
  const { at } = morningNotify();
  assert.match(at, /^\d{2}:(00|30)$/, `${at} は :00 か :30 ではありません`);
  assert.ok(fires(at, 3), `${at} に cron が発火しません`);
});

test("朝の通知の関門が実在し、通知より前の時刻に起動される", () => {
  const { at, gate } = morningNotify();
  assert.ok(readdirSync(WORKFLOW_DIR).includes(gate), `${gate} が存在しません`);

  const slot = [...P.PLAN.matchAll(/"(\d{2}:\d{2})": \[([^\]]*)\]/g)]
    .find((m) => m[2].includes(`"${gate}"`));
  assert.ok(slot, `${gate} が PLAN にありません（関門が走らない）`);
  assert.ok(
    slot[1] < at,
    `関門 ${gate} は ${slot[1]} 起動で、通知 ${at} より後です（結果を待てません）`,
  );
});

test("朝の通知は https の会員アプリを叩く", () => {
  const { url } = morningNotify();
  assert.match(url, /^https:\/\//, `平文の HTTP です: ${url}`);
  assert.match(url, /\/api\/cron\//, `想定外の宛先です: ${url}`);
});

test("朝の通知は関門が success のときだけ叩く", () => {
  // 「判定できないときは送らない」に倒してあること。
  // ここを >= や != に書き換えると、古いデータで会員にメールが届く。
  assert.match(
    SRC, /if \(r !== "success"\) \{/,
    "関門の判定が `r !== \"success\"` になっていません（送らない側に倒す）",
  );
  assert.match(SRC, /CRON_SECRET/, "CRON_SECRET を使っていません");
});
