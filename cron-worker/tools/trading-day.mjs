#!/usr/bin/env node
/**
 * 東証が開いている日かを答える小さな道具。
 *
 *   node cron-worker/tools/trading-day.mjs              今日（JST）
 *   node cron-worker/tools/trading-day.mjs 2026-09-24   その日
 *   node cron-worker/tools/trading-day.mjs 2026-09-17 2026-09-25   範囲
 *   node cron-worker/tools/trading-day.mjs --prev       直近の営業日（今日を含む）
 *   node cron-worker/tools/trading-day.mjs --next       次の営業日（今日を含む）
 *   node cron-worker/tools/trading-day.mjs --quiet 2026-09-24   終了コードだけ
 *
 * 終了コード（1日を聞いたとき）: 0=営業日 / 1=休場 / 2=エラー
 *   休場を「失敗」ではなく 1 にしてあるので、こう書ける:
 *     node ... --quiet || echo "今日は休場"
 *
 * ■ なぜ要るか（2026-09-21）
 * 毎朝の確認で isTradingDay() を手で呼んでいて、引数の渡し方を間違えた。
 * `isTradingDay()` は **JSTの壁時計をUTCとして持つ Date** を取る仕様で、
 * `new Date('2026-09-21T00:00:00+09:00')` と書くと中では 9/20 として
 * 判定される。**1日ずれたまま答えだけ合う**ことがあり、気づきにくい。
 * 呼び方をここに1か所へ閉じ込めて、毎回書き直さないようにする。
 *
 * ■ 表の範囲外は黙って通さない
 * holidays.js は、表に無い年を「休場日が1つも無い」＝平日は全部営業日
 * として扱う（止まるより余計に動く側に倒してある）。
 * それを知らずに答えを信じるのがいちばん危ないので、範囲外なら
 * 終了コード 2 で止める。
 */
import { HOLIDAYS, isKnownYear, isTradingDay, ymd } from "../src/holidays.js";

const DOW = "日月火水木金土";
const RE_YMD = /^\d{4}-\d{2}-\d{2}$/;

/** YYYY-MM-DD → holidays.js が期待する Date（JSTの壁時計をUTCとして持つ） */
function jstDate(s) {
  if (!RE_YMD.test(s)) die(`日付は YYYY-MM-DD で指定してください: ${s}`);
  const d = new Date(`${s}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) die(`そんな日はありません: ${s}`);
  // 2026-02-30 のような日は Date が繰り上げてしまうので、往復させて確かめる
  if (ymd(d) !== s) die(`そんな日はありません: ${s}`);
  return d;
}

/** 今日（JST）。動かしている機械のタイムゾーンに左右されない */
function todayJst() {
  return new Date(Math.floor((Date.now() + 9 * 3600 * 1000) / 86400000) * 86400000);
}

function die(msg) {
  console.error(`[エラー] ${msg}`);
  process.exit(2);
}

function label(d) {
  return `${ymd(d)} (${DOW[d.getUTCDay()]})`;
}

/** 休場の理由。土日か、表に載っている休場日か */
function reason(d) {
  const dow = d.getUTCDay();
  if (dow === 0 || dow === 6) return "土日";
  return HOLIDAYS.has(ymd(d)) ? "休場日" : null;
}

function step(d, days) {
  return new Date(d.getTime() + days * 86400000);
}

/** 表の範囲内かを確かめる。外なら止める（黙って平日=営業日にしない） */
function assertKnown(d) {
  if (!isKnownYear(ymd(d))) {
    die(
      `${ymd(d).slice(0, 4)}年は休場日の表にありません。\n` +
      `        このまま答えると、祝日を営業日と言ってしまいます。\n` +
      `        cron-worker/src/holidays.js にその年を足してください（出典: JPX 取引カレンダー）。`
    );
  }
}

function main(argv) {
  const args = argv.filter((a) => !a.startsWith("--"));
  const flags = new Set(argv.filter((a) => a.startsWith("--")));
  const quiet = flags.has("--quiet");

  for (const f of flags) {
    if (!["--quiet", "--prev", "--next"].includes(f)) die(`知らない指定です: ${f}`);
  }

  const from = args[0] ? jstDate(args[0]) : todayJst();
  assertKnown(from);

  // --prev / --next : 営業日を探して1行返す
  if (flags.has("--prev") || flags.has("--next")) {
    const dir = flags.has("--next") ? 1 : -1;
    let d = from;
    for (let i = 0; i <= 30; i++) {
      assertKnown(d);
      if (isTradingDay(d)) {
        console.log(quiet ? ymd(d) : `${label(d)} 営業日`);
        return 0;
      }
      d = step(d, dir);
    }
    die("30日さかのぼって（進んで）も営業日が見つかりません。表を疑ってください。");
  }

  // 範囲
  if (args[1]) {
    const to = jstDate(args[1]);
    assertKnown(to);
    if (to < from) die("2つめの日付が1つめより前です。");
    if ((to - from) / 86400000 > 400) die("範囲が広すぎます（400日まで）。");
    let open = 0;
    for (let d = from; d <= to; d = step(d, 1)) {
      const r = reason(d);
      if (!r) open++;
      console.log(`${label(d)} ${r ? `休場（${r}）` : "営業日"}`);
    }
    if (!quiet) console.log(`― 営業日 ${open} 日 ―`);
    return 0;
  }

  // 1日
  const open = isTradingDay(from);
  if (!quiet) {
    const r = reason(from);
    console.log(`${label(from)} ${open ? "営業日" : `休場（${r}）`}`);
  }
  return open ? 0 : 1;
}

process.exit(main(process.argv.slice(2)));
