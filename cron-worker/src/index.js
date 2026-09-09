/**
 * GitHub Actions の schedule を Cloudflare の Cron Trigger で置き換える。
 *
 * ■ なぜ要るか（引継ぎ.md §19-8 / 2026-09-09 Fable 相談⑧）
 * GitHub Actions の schedule は**常時2〜5時間遅れて起動する**。
 * 19本すべてで観測し、中央値は free-scanner 192分・precompute-daily 277分・
 * daily-signal 241分。「毎時0分・30分は混むので分をずらす」は効かない
 * （:05 でも :47 でも同じように遅れた）。
 * 2026-09-05 には free-scanner が丸ごと起動せず、公開JSONの対象日が
 * 4日間止まった。GitHub は「起動しなかった」を失敗として記録しないので
 * 誰も気づけなかった。
 *
 * Cloudflare の Cron Trigger は数秒〜数十秒のずれで動く。ここから
 * GitHub API の workflow_dispatch を叩けば、狙った時刻に確実に走る。
 *
 * ■ GitHub 側の schedule は消さない
 * この Worker が落ちたときの保険として残す。二重に走っても、各ワークフローの
 * 冪等ガード（当日ぶんが既にあればスキップ）が効くので害はない。
 *
 * ■ Cron Trigger は1本だけにしてある
 * 無料プランは1 Worker あたり3本まで。時刻ごとに分けると足りないので、
 * 「毎時 0分・30分」に起こして、Worker の中で JST を見て振り分ける。
 * 時刻を足したいときは下の PLAN に1行足すだけでよい（wrangler.toml は触らない）。
 */

const REPO = "oo12takemaru-create/stock-trading-";
const REF = "main"; // workflow_dispatch は既定ブランチのものしか動かない

/**
 * JST の "HH:MM" → その時刻に起こすワークフロー（配列の順に叩く）。
 *
 * ★ここに書くのは「実際に走ってほしい時刻」★
 * GitHub の schedule 側は遅延を見越して3時間前倒ししてあるが（PR #380）、
 * こちらは遅れないので前倒し不要。素直に狙いの時刻を書く。
 */
const PLAN = {
  "08:00": ["daily-signal.yml"],       // 朝
  "12:00": ["daily-signal.yml"],       // 昼
  "18:00": ["daily-signal.yml"],       // 夕（終値ベース・radar-publish がこの完了で連鎖する）
  "18:30": ["precompute-daily.yml"],   // 前計算 → 公開数字 → score3_lite → キャッシュ温め
  "19:30": ["free-scanner.yml"],       // 無料版スキャナー
  "20:30": ["free-scanner.yml"],       // 予備（当日ぶんがあれば冪等ガードで何もしない）
  "22:30": ["precompute-daily.yml"],   // 予備
};

/** 平日だけ動かす（土日は日本市場が休み） */
function isWeekday(jst) {
  const d = jst.getUTCDay(); // jst は「JSTの壁時計をUTCとして持つ」Date
  return d >= 1 && d <= 5;
}

/** now(UTC) を JST の壁時計に直す */
function toJst(now) {
  return new Date(now.getTime() + 9 * 60 * 60 * 1000);
}

function hhmm(jst) {
  const h = String(jst.getUTCHours()).padStart(2, "0");
  const m = String(jst.getUTCMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

async function dispatch(workflow, env) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      // User-Agent が無いと GitHub API は 403 を返す
      "User-Agent": "ruletrade-cron-worker",
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Authorization": `Bearer ${env.GH_PAT}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: REF }),
  });
  // 成功は 204。本文は空
  const ok = res.status === 204;
  const detail = ok ? "" : ` ${res.status} ${(await res.text()).slice(0, 200)}`;
  console.log(`${ok ? "OK  " : "NG  "} ${workflow}${detail}`);
  return ok;
}

export default {
  async scheduled(event, env, ctx) {
    const jst = toJst(new Date(event.scheduledTime));
    const key = hhmm(jst);
    const targets = PLAN[key];

    if (!targets) {
      console.log(`JST ${key}: 予定なし`);
      return;
    }
    if (!isWeekday(jst)) {
      console.log(`JST ${key}: 土日なので何もしない`);
      return;
    }
    if (!env.GH_PAT) {
      // 黙って何もしないと「動いているつもり」になる。必ず記録に残す
      console.error("GH_PAT が設定されていません。何も起動できません");
      return;
    }

    console.log(`JST ${key}: ${targets.length} 本を起動します`);
    for (const wf of targets) {
      // 順に叩く（並列にしない＝順序を保つ）
      await dispatch(wf, env);
    }
  },

  /**
   * 動作確認用。ブラウザで開くと今の JST と、次に何が起きるかを返す。
   * 起動はしない（GET で副作用を起こさない）。
   */
  async fetch(request, env) {
    const jst = toJst(new Date());
    const body = {
      now_jst: jst.toISOString().replace("T", " ").slice(0, 16),
      weekday: isWeekday(jst),
      has_token: Boolean(env.GH_PAT),
      repo: REPO,
      plan: PLAN,
    };
    return new Response(JSON.stringify(body, null, 2), {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  },
};
