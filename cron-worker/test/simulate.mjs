/**
 * 1日ぶんの起動を机上で再現する（デプロイ前の目視確認用）。
 *
 * ■ なぜ要るか
 * PLAN は「時刻 → ワークフロー」の表でしかなく、休場日判定・重複確認・
 * 予備フラグ・ザラ場枠・朝の通知の関門が絡むと**結局いつ何が走るのか**が
 * 読み取りにくい。本番に出す前に、1日を回して結果を表にする。
 *
 * GitHub API も会員アプリも叩かない（fetch を差し替える）。
 * その日に起動したものは**成功したことにする**（ふつうの日を再現する）。
 *
 *   node cron-worker/test/simulate.mjs                      # 平日
 *   node cron-worker/test/simulate.mjs 2026-09-12           # 土曜
 *   node cron-worker/test/simulate.mjs 2026-09-21           # 祝日（敬老の日）
 *   node cron-worker/test/simulate.mjs 2026-09-09 fail=pipeline-daily.yml
 *     → 18:00 が失敗した日（21:00 の予備が予備フラグ付きで走るか）
 *   node cron-worker/test/simulate.mjs 2026-09-09 fail=pipeline-morning.yml
 *     → 朝の関門が落ちた日（7:30 の会員通知を見合わせるか）
 */
import worker from "../src/index.js";

const day = process.argv[2] || "2026-09-09"; // 既定は水曜
const arg = (k) => {
  const a = process.argv.find((x) => x.startsWith(k + "="));
  return a ? a.slice(k.length + 1).split(",") : [];
};
const willFail = new Set(arg("fail"));   // 起動しても失敗する
const already = new Set(arg("ran"));     // その日の朝から既に成功している

const dispatched = new Set([...already]); // 「今日 run がある」もの
const calls = [];
let NOW = "";

globalThis.fetch = async (url, opt = {}) => {
  const u = String(url);

  const m = u.match(/workflows\/([\w.-]+)\/(runs|dispatches)/);
  if (m && m[2] === "runs") {
    // 当日の run があるか・その結果は何か
    if (!dispatched.has(m[1])) return { ok: true, json: async () => ({ workflow_runs: [] }) };
    return {
      ok: true,
      json: async () => ({
        workflow_runs: [{
          created_at: `${day}T00:00:00Z`,
          status: "completed",
          conclusion: willFail.has(m[1]) ? "failure" : "success",
        }],
      }),
    };
  }
  if (m) {
    const body = JSON.parse(opt.body || "{}");
    calls.push({ at: NOW, what: m[1], inputs: body.inputs || null });
    dispatched.add(m[1]);
    return { status: 204, text: async () => "" };
  }

  if (u.includes("api.resend.com")) {
    const b = JSON.parse(opt.body || "{}");
    calls.push({ at: NOW, what: `[メール] ${b.subject || ""}`, mail: true });
    return { ok: true, status: 200, text: async () => "" };
  }

  // 会員アプリの朝の通知
  calls.push({ at: NOW, what: `[HTTP] POST ${u.replace(/^https:\/\/[^/]+/, "")}` });
  return { ok: true, status: 200, text: async () => '{"sent":1}' };
};

const env = {
  GH_PAT: "dummy", RESEND_API_KEY: "dummy",
  NOTIFY_TO: "x@example.com", CRON_SECRET: "dummy",
};
const ctx = { waitUntil: (p) => p };
const quiet = console.log;

// cron は「毎時 :00 :30」と「ザラ場の :15 :45」の2本。両方を再現する
for (let h = 0; h < 24; h++) {
  for (const mi of [0, 15, 30, 45]) {
    NOW = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
    const utc = Date.parse(`${day}T${NOW}:00Z`) - 9 * 3600 * 1000; // JST の壁時計 → UTC
    console.log = () => {};
    console.warn = () => {};
    console.error = () => {};
    await worker.scheduled({ scheduledTime: utc }, env, ctx);
    console.log = quiet;
  }
}

const dow = ["日", "月", "火", "水", "木", "金", "土"][new Date(`${day}T00:00:00Z`).getUTCDay()];
console.log(`\n=== ${day}（${dow}）に起きること ===`);
if (willFail.size) console.log(`※ 失敗するものとみなす: ${[...willFail].join(", ")}`);
if (already.size) console.log(`※ 朝から成功済みとみなす: ${[...already].join(", ")}`);
if (calls.length === 0) console.log("（何も起きない）");

let last = "";
for (const c of calls) {
  const t = c.at === last ? "     " : c.at.padEnd(5);
  last = c.at;
  console.log(`${t}  ${c.what}${c.inputs ? `  <- ${JSON.stringify(c.inputs)}` : ""}`);
}

const n = {};
for (const c of calls) n[c.what] = (n[c.what] || 0) + 1;
const wf = Object.entries(n).filter(([k]) => k.endsWith(".yml"));
console.log(`\nワークフロー起動 ${wf.reduce((a, [, v]) => a + v, 0)} 回 / ${wf.length} 種類`);
for (const [k, v] of wf.sort((a, b) => b[1] - a[1])) {
  console.log(`  ${String(v).padStart(2)} 回  ${k}`);
}
const other = Object.entries(n).filter(([k]) => !k.endsWith(".yml"));
if (other.length) {
  console.log("\nそのほか");
  for (const [k, v] of other) console.log(`  ${String(v).padStart(2)} 回  ${k}`);
}
