/**
 * 米国株ヒートマップ（heatmap-us.yml）を米国東部時間（ET）で起こす（2026-10-24）。
 *
 * ■ なぜ別の表にしたか
 *   PLAN は JST の時刻・東証の営業日で動く。米国の引けは JST で 6:00（冬）/ 5:00（夏）と
 *   夏時間で1時間ずれ、休場日も東証と違う（感謝祭・グッドフライデーなど）。
 *   JST の PLAN に夏冬2通りの時刻を書くと、どちらか一方が「休場の日に動く/動くべき日に動かない」になる。
 *   そこで ET の壁時計で時刻表を持ち、ET の曜日と NYSE の休場日で判定する。
 *   ET と UTC の差は整数時間なので、分（:00 :15 :30 :45）は既存の cron のどれかで必ず発火する。
 *
 * ■ 時刻（ET）
 *   09:45 場中（寄り15分後）  12:30 場中  16:15 引け後の確定値  18:00 確定値の取り直し（予備）
 *   日本時間: 冬 23:45 / 2:30 / 6:15 / 8:00、夏 22:45 / 1:30 / 5:15 / 7:00
 *   heatmap-us.yml は1日4回走るのが正常（NO_DEDUP に入れてある）。
 *
 * ■ 休場日は NYSE の公表（年1回の手入れ）。収集側の us_bizday.py と同じ表。年を足すときは両方直す。
 */

export const US_WORKFLOW = "heatmap-us.yml";

export const US_PLAN = {
  "09:45": [US_WORKFLOW],
  "12:30": [US_WORKFLOW],
  "16:15": [US_WORKFLOW],
  "18:00": [US_WORKFLOW],
};

export const US_HOLIDAYS = new Set([
  // 2026
  "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
  "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
  // 2027（6/19 が土曜→6/18、7/4 が日曜→7/5、12/25 が土曜→12/24）
  "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
  "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
]);
export const US_KNOWN_YEARS = [2026, 2027];

/** その年の第n日曜（UTC の Date の日付だけ使う） */
function nthSunday(y, m, n) {
  const d = new Date(Date.UTC(y, m, 1));
  const first = (7 - d.getUTCDay()) % 7; // 最初の日曜までの日数
  return 1 + first + 7 * (n - 1);
}

/**
 * 米国の夏時間か（3月第2日曜 2:00 ET 〜 11月第1日曜 2:00 ET）。
 * 切り替わりは UTC で 3月 7:00（EST 2:00）/ 11月 6:00（EDT 2:00）。
 */
export function isUsDst(nowUtc) {
  const y = nowUtc.getUTCFullYear();
  const start = Date.UTC(y, 2, nthSunday(y, 2, 2), 7);
  const end = Date.UTC(y, 10, nthSunday(y, 10, 1), 6);
  const t = nowUtc.getTime();
  return t >= start && t < end;
}

/** now(UTC) を ET の壁時計に直す（JST の toJst と同じ流儀＝ET の時刻を UTC として持つ Date） */
export function toEt(nowUtc) {
  return new Date(nowUtc.getTime() + (isUsDst(nowUtc) ? -4 : -5) * 3600 * 1000);
}

const pad = (n) => String(n).padStart(2, "0");
export const etYmd = (et) => et.toISOString().slice(0, 10);
export const etHhmm = (et) => `${pad(et.getUTCHours())}:${pad(et.getUTCMinutes())}`;

/** ET の日付で、米国市場が開いている日か */
export function isUsSession(et) {
  const dow = et.getUTCDay();
  if (dow === 0 || dow === 6) return false;
  return !US_HOLIDAYS.has(etYmd(et));
}

/** この時刻（UTC）に起こす米国のワークフロー */
export function usTargetsFor(nowUtc) {
  const et = toEt(nowUtc);
  const key = etHhmm(et);
  const t = US_PLAN[key] || [];
  if (!t.length) return { targets: [], why: "", et: key };
  if (!isUsSession(et)) return { targets: [], why: `米国の休場日（${etYmd(et)}）`, et: key };
  return { targets: t, why: `米国東部 ${key}`, et: key };
}
