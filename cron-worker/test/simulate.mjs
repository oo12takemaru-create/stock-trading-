/**
 * 1日ぶんの起動を机上で再現する（デプロイ前の目視確認用）。
 *
 * ■ なぜ要るか
 * PLAN は「時刻 → ワークフロー」の表でしかなく、休場日判定・重複確認・
 * 予備フラグ・ザラ場枠が絡むと**結局いつ何が走るのか**が読み取りにくい。
 * 本番に出す前に、1日を1分刻みで回して結果を表にする。
 *
 * GitHub API は叩かない（fetch を差し替える）。
 *   node cron-worker/test/simulate.mjs            # 平日
 *   node cron-worker/test/simulate.mjs 2026-09-12 # 土曜
 *   node cron-worker/test/simulate.mjs 2026-09-21 # 祝日（敬老の日）
 *   node cron-worker/test/simulate.mjs 2026-09-09 ran=pipeline-daily.yml
 *     → 18:00 が既に成功している日（21:00 の予備が止まるかを見る）
 */
import worker from "../src/index.js";

const day = process.argv[2] || "2026-09-09"; // 既定は水曜
const ranArg = process.argv.find((a) => a.startsWith("ran="));
const alreadyRan = new Set(ranArg ? ranArg.slice(4).split(",") : []);

const calls = [];
globalThis.fetch = async (url, opt = {}) => {
  const m = String(url).match(/workflows\/([\w.-]+)\/(runs|dispatches)/);
  if (m && m[2] === "runs") {
    // 「当日ぶんが既に成功しているか」の問い合わせ
    const runs = alreadyRan.has(m[1])
      ? [{ created_at: `${day}T09:00:00Z`, status: "completed", conclusion: "success" }]
      : [];
    return { ok: true, json: async () => ({ workflow_runs: runs }) };
  }
  if (m) {
    const body = JSON.parse(opt.body || "{}");
    calls.push({ at: NOW, workflow: m[1], inputs: body.inputs || null });
    return { status: 204, text: async () => "" };
  }
  return { ok: true, status: 200, text: async () => "" }; // Resend 等
};

const env = { GH_PAT: "dummy", RESEND_API_KEY: "dummy", NOTIFY_TO: "x@example.com" };
const ctx = { waitUntil: (p) => p };
const quiet = console.log;
let NOW = "";

// cron は「毎時 :00 :30」と「ザラ場の :15 :45」の2本。両方を1分刻みで再現する
const MINUTES = [0, 15, 30, 45];
for (let h = 0; h < 24; h++) {
  for (const mi of MINUTES) {
    NOW = `${String(h).padStart(2, "0")}:${String(mi).padStart(2, "0")}`;
    // JST の壁時計 → UTC（Worker は event.scheduledTime を UTC で受け取る）
    const utc = Date.parse(`${day}T${NOW}:00Z`) - 9 * 3600 * 1000;
    console.log = () => {};        // Worker のログは伏せる（表だけ見たい）
    console.warn = () => {};
    await worker.scheduled({ scheduledTime: utc }, env, ctx);
    console.log = quiet;
  }
}

const dow = ["日", "月", "火", "水", "木", "金", "土"][new Date(`${day}T00:00:00Z`).getUTCDay()];
console.log(`\n=== ${day}（${dow}）に起動されるもの ===`);
if (alreadyRan.size) console.log(`※ 既に成功済みとみなす: ${[...alreadyRan].join(", ")}`);
if (calls.length === 0) console.log("（何も起動しない）");

let last = "";
for (const c of calls) {
  const t = c.at === last ? "     " : c.at.padEnd(5);
  last = c.at;
  const note = c.inputs ? `  ← ${JSON.stringify(c.inputs)}` : "";
  console.log(`${t}  ${c.workflow}${note}`);
}

const n = {};
for (const c of calls) n[c.workflow] = (n[c.workflow] || 0) + 1;
console.log(`\n合計 ${calls.length} 回 / ${Object.keys(n).length} 種類`);
for (const [k, v] of Object.entries(n).sort((a, b) => b[1] - a[1])) {
  console.log(`  ${String(v).padStart(2)} 回  ${k}`);
}
