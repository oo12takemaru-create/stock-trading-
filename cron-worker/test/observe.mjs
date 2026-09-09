/**
 * 切り替えの観測（起動文 Step 4-2 の並走記録）。
 *
 * ■ 何を見るか
 * 「Worker が予定どおり起こしたか」を、時刻表（PLAN）を正として突き合わせる。
 * 予定は index.js の targetsFor() をそのまま呼んで作るので、**本番と同じ表**を見る。
 * 手で表を書き写すと、PLAN を直したときに観測だけ古くなる。
 *
 * ■ 判定
 *   OK   … workflow_dispatch の run があり、狙い時刻から5分以内
 *   遅い … dispatch の run はあるが5分より遅れた
 *   無い … 当日その時刻の run が見つからない（★これが致命的。schedule と違い黙って消える）
 *   済   … alreadyRanToday で正しく飛ばした（当日すでに success がある）
 *
 * 会員向けの朝の通知（7:30・HTTP）は GitHub に痕跡が残らないのでここでは見えない。
 * Cloudflare のログか、届いたメールで確かめる。
 *
 *   node cron-worker/test/observe.mjs              # 今日（JST）
 *   node cron-worker/test/observe.mjs 2026-09-10
 *   node cron-worker/test/observe.mjs 2026-09-09 from=22:00   # デプロイ後だけ見る
 */
import { execFileSync } from "node:child_process";
import { targetsFor, NO_DEDUP } from "../src/index.js";

const pad = (n) => String(n).padStart(2, "0");
const today = () => {
  const d = new Date(Date.now() + 9 * 3600 * 1000);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
};
const day = process.argv[2] || today();

// Worker を日中にデプロイした日は、それ以前の時間帯を判定しても意味がない。
// 「無い」が並ぶだけで、本当に見たいものが埋もれる。
const fromArg = process.argv.find((a) => a.startsWith("from="));
const FROM = fromArg ? fromArg.slice(5) : "00:00";

// ── 予定を作る（本番と同じ targetsFor を使う）──
const expected = []; // { at: "HH:MM", workflow }
for (let h = 0; h < 24; h++) {
  for (const mi of [0, 15, 30, 45]) {
    const at = `${pad(h)}:${pad(mi)}`;
    const jst = new Date(`${day}T${at}:00Z`); // JST の壁時計を UTC として持つ
    for (const w of targetsFor(jst).targets) expected.push({ at, workflow: w });
  }
}

// ── 実際の run を取る ──
// run 一覧にはファイル名が無いので、workflow 一覧から id → ファイル名を作る
const byId = new Map(
  JSON.parse(execFileSync("gh", ["workflow", "list", "--limit", "100", "--json", "id,path"],
    { encoding: "utf8" })).map((w) => [w.id, w.path.split("/").pop()]),
);

const raw = execFileSync("gh", [
  "run", "list", "--limit", "250",
  "--json", "name,event,createdAt,conclusion,status,workflowDatabaseId",
], { encoding: "utf8", maxBuffer: 32 * 1024 * 1024 });

const runs = JSON.parse(raw)
  .map((r) => {
    const t = new Date(Date.parse(r.createdAt) + 9 * 3600 * 1000); // JST
    return { ...r, jst: t, file: byId.get(r.workflowDatabaseId) || "" };
  })
  .filter((r) => r.jst.toISOString().slice(0, 10) === day);

const mins = (t) => t.getUTCHours() * 60 + t.getUTCMinutes();
const toMin = (hhmm) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3));

console.log(`\n=== ${day}（JST）予定と実際 ===\n`);
if (FROM !== "00:00") console.log(`（${FROM} 以降だけを判定）`);
console.log("予定    ワークフロー                  判定    実際      ズレ   結果");
console.log("-".repeat(76));

const used = new Set();
let missing = 0, late = 0, ok = 0, skipped = 0;

for (const e of expected.sort((a, b) => toMin(a.at) - toMin(b.at))) {
  const want = toMin(e.at);
  // その時刻以降に出た同じワークフローの dispatch を探す（60分まで）
  const hit = runs
    .filter((r) => r.file === e.workflow && r.event === "workflow_dispatch" && !used.has(r))
    .filter((r) => mins(r.jst) >= want && mins(r.jst) - want <= 60)
    .sort((a, b) => mins(a.jst) - mins(b.jst))[0];

  if (e.at < FROM) continue; // デプロイ前などは判定しない

  let verdict, actual = "-", lag = "-";
  if (hit) {
    used.add(hit);
    const d = mins(hit.jst) - want;
    actual = `${pad(hit.jst.getUTCHours())}:${pad(hit.jst.getUTCMinutes())}`;
    lag = `+${d}分`;
    if (d <= 5) { verdict = "OK"; ok++; } else { verdict = "遅い"; late++; }
  } else {
    // 当日すでに success があれば、飛ばしたのが正しい。
    // ただし NO_DEDUP のもの（1日複数回が正常）は飛ばさないのが正しいので、
    // 当日すでに success があっても「無い」は「無い」。
    const earlier = !NO_DEDUP.has(e.workflow) && runs.find(
      (r) => r.file === e.workflow && r.conclusion === "success" && mins(r.jst) < want,
    );
    if (earlier) { verdict = "済"; skipped++; } else { verdict = "★無い"; missing++; }
  }
  const concl = hit ? (hit.conclusion || hit.status || "-") : "";
  console.log(
    `${e.at}   ${e.workflow.padEnd(28)} ${verdict.padEnd(6)} ${actual.padEnd(9)} ${String(lag).padEnd(6)} ${concl}`,
  );
}

console.log("-".repeat(76));
console.log(`OK ${ok} / 遅い ${late} / 済（重複回避）${skipped} / ★無い ${missing}`);

// ── 起動元ごとのズレ ──
const lags = { workflow_dispatch: [], schedule: [] };
for (const r of runs) {
  if (!lags[r.event]) continue;
  const m = mins(r.jst);
  lags[r.event].push(m - Math.floor(m / 15) * 15); // 直前の15分刻みからの遅れ（分）
}
console.log("\n=== 起動元ごとの「15分刻みからの遅れ」（分）===");
for (const [k, v] of Object.entries(lags)) {
  if (!v.length) { console.log(`  ${k}: 0 件`); continue; }
  const s = [...v].sort((a, b) => a - b);
  console.log(`  ${k.padEnd(18)} ${s.length} 件  中央値 ${s[s.length >> 1]} 分 / 最大 ${s[s.length - 1]} 分`);
}

if (missing > 0) {
  console.log(
    "\n★「無い」が1つでもあれば切り替えを完了させない。" +
      "\n  起動しなかったことは GitHub に失敗として残らない（2026-09-05 の4日間放置と同じ形）。",
  );
}
console.log("\n※ 会員向けの朝の通知（7:30・HTTP）はここには出ない。Cloudflare のログかメールで確認する。");
