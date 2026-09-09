/**
 * GitHub Actions の schedule を Cloudflare の Cron Trigger で置き換える。
 *
 * ■ なぜ要るか（引継ぎ.md §19-8 / 2026-09-09 Fable 相談⑧）
 * GitHub Actions の schedule は**常時2〜5時間遅れて起動する**。
 * schedule で動く24本すべてで観測し、中央値は free-scanner 192分・
 * precompute-daily 277分・daily-signal 241分。
 * 「毎時0分・30分は混むので分をずらす」は効かない（:05 でも :47 でも同じ）。
 * 2026-09-05 には free-scanner が丸ごと起動せず、公開JSONの対象日が
 * 4日間止まった。GitHub は「起動しなかった」を失敗として記録しないので
 * 誰も気づけなかった。
 *
 * Cloudflare の Cron Trigger は数秒〜数十秒のずれで動く。ここから
 * GitHub API の workflow_dispatch を叩けば、狙った時刻に確実に走る。
 *
 * ■ Cron Trigger は2本だけ（重要）
 * Cloudflare の Cron Trigger は**アカウントあたり5本**まで（無料枠）。
 * 時間帯ごとに1本ずつ割り当てると足りない（実際の時間帯は9系統ある）。
 * そこで「毎時 0分・30分」の1本で起こし、**何を起動するかは下の PLAN**
 * で決める。時刻を足したいときは PLAN に1行足すだけで、wrangler.toml は触らない。
 *   ※ PLAN に書ける時刻は :00 と :30 だけ。それ以外を書いても発火しない。
 * 2本目はザラ場の :15/:45 だけ。realtime-signal を 15分間隔で回すための補いで、
 * INTRADAY で扱う（PLAN には書かない――1日に何度も走るのが正常な唯一の例外）。
 *
 * ■ GitHub 側の schedule は当面残す
 * この Worker が落ちたときの保険。二重に走っても各ワークフローの冪等ガード
 * （当日ぶんが既にあればスキップ）が効くので害はない。
 * 切り替えが3営業日安定してから schedule を消す（起動文 Step 4）。
 */

import { isTradingDay, isKnownYear, ymd } from "./holidays.js";

const REPO = "oo12takemaru-create/stock-trading-";
const REF = "main"; // workflow_dispatch は既定ブランチのものしか動かない

/**
 * JST の "HH:MM" → その時刻に起こすワークフロー（配列の順に叩く）。
 *
 * ★ここに書くのは「実際に走ってほしい時刻」★
 * GitHub の schedule 側は遅延を見越して前倒ししてあるが、こちらは遅れないので
 * 素直に狙いの時刻を書く。
 *
 * ★:00 と :30 しか書けない★（cron が毎時0分・30分のため）
 */
const PLAN = {
  // ── ザラ場（平日）──
  "09:00": ["heatmap.yml"],
  "10:30": ["heatmap.yml"],
  "12:00": ["daily-signal.yml", "heatmap.yml"], // 昼の回
  "14:30": ["heatmap.yml"],
  // ※ JST 15:00 に daily-signal を置かない。slot 判定は JST 15時以降を
  //   一律に「夕」とするので、18:00 の回と**同じタイトルの Issue が
  //   2本立つ**。夕は 18:00（pipeline-daily の先頭）が担当する。

  // ── 引け後（平日）。この順序が公開JSONの鮮度を決める ──
  "16:00": ["heatmap.yml"],
  "18:00": ["pipeline-daily.yml"], // 夕シグナル→free-scanner→radar→派生→前計算→点検
  "19:00": ["buyback-daily.yml", "ai-record.yml"],

  // ── 予備（冪等ガードが効くので二重でも害はない）──
  "21:00": ["pipeline-daily.yml"],

  // ── 深夜〜早朝 ──
  "23:30": ["data-healthcheck.yml"],
  "06:30": ["ai-analysis.yml"],
  "07:00": ["pipeline-morning.yml"], // 前営業日ぶんが無ければ失敗させる関門
  "07:30": ["karauri-daily.yml", "kessan-daily.yml"],
  "08:00": ["daily-signal.yml"],
  "08:30": ["morning-digest.yml", "kessan-react-daily.yml"],
};

/** 土日に動かすもの（JSTの曜日 → 時刻 → ワークフロー） */
const WEEKEND_PLAN = {
  6: { "09:30": ["pipeline-weekly.yml"] }, // 土
};

/** 特定の曜日だけ動かすもの（平日）。JST の曜日で判定 */
// 信用残(shinyo)・建玉(cot)・十倍株・週次AIは pipeline-weekly.yml にまとめたので
// ここには書かない（書くと土曜と二重に走る）
const WEEKDAY_ONLY_PLAN = {
  4: { "16:30": ["investor-flow.yml"] }, // 木: 投資部門別（JPX の公表が木曜）
};

/**
 * ザラ場中に15分間隔で回すもの（PLAN とは別枠）。
 *
 * ★PLAN に書かない理由★
 * PLAN のものは「1日に1回走ればよい」もので、二重起動を防ぐために
 * alreadyRanToday() で弾く。realtime-signal は**1日に28回走るのが正常**なので、
 * 同じ仕組みに乗せると 2回目以降が全部スキップされる。
 *
 * JST 09:00〜15:45 の :00 :15 :30 :45。:00/:30 は1本目の cron、
 * :15/:45 は2本目の cron が担当する（合わせて 15分間隔）。
 * 市場時間外の通知抑制は realtime-signal 側のゲートに任せる。
 */
const INTRADAY = {
  workflow: "realtime-signal.yml",
  from: "09:00",
  to: "15:45",
};

/**
 * 一日に何度も走るのが**正常**なもの（二重起動の確認をしない）。
 *
 * ★ここに入れ忘れると黙って2回目以降が全部消える★
 * alreadyRanToday() は「当日に success があれば起動しない」という仕組み。
 * 1日複数回走るものをこの仕組みに乗せると、朝の1回目以外が
 * すべて SKIP され、**失敗としては記録されない**（気づけない）。
 * PLAN に複数の時刻で書いたものは、ここか DEDUP_ANYWAY のどちらかに
 * 入れること。入っていなければ plan.test.mjs が落ちる。
 */
export const NO_DEDUP = new Set([
  INTRADAY.workflow,  // ザラ場15分毎（1日約28回）
  "heatmap.yml",      // ザラ場の値動き（1日5回）
  "daily-signal.yml", // 朝・昼・夕の3回が正常（夕は pipeline-daily の中）
]);

/**
 * PLAN に複数の時刻で置いているが、**意図的に**重複確認をするもの。
 * （二本目は予備＝一本目が成功していたら走らせない）
 */
const DEDUP_ANYWAY = new Set([
  "pipeline-daily.yml", // 21:00 は予備。18:00 が成功していたら SKIP してよい
]);

/**
 * 予備の回として起動するもの（JST 時刻 → ワークフロー名）。
 *
 * ★なぜ入力で伝える必要があるか★
 * 呼ばれる側の workflow から見た github.event_name は**呼び出し元のイベント**に
 * なるため、daily-signal の「予備の回は Issue を出さない」ガードが
 * パイプライン経由だと効かない。本命（18:00）で daily-signal は成功したが
 * 下流で落ちた日に予備を回すと、同じシグナル Issue が2本立つ。
 * 「二重通知は欠落より悪い」（引継ぎ.md §19-7）のため、ここで明示する。
 * データ（JSON・CSV）は予備でも欠かさず作る。
 */
const BACKUP_AT = {
  "21:00": new Set(["pipeline-daily.yml"]),
};

/**
 * 会員向けの「朝の通知」を ruletrade-app に叩かせる（引継ぎ.md §19 相談⑨）。
 *
 * ■ GitHub のワークフローではなく HTTP を叩く
 * 送信の中身は会員アプリ（Next.js）側にある。Vercel Pro を買わずに
 * 時刻どおり叩くため、この Worker から呼ぶ（Fable 決定・選択肢③）。
 *
 * ■ gate が**成功していなければ叩かない**
 * pipeline-morning（07:00）は「前営業日ぶんのデータが揃っているか」の関門。
 * これが落ちている日に通知を送ると、**古いデータで会員に直接届く**。
 * 2026-09-06 の事故（^GSPC が取れないまま別物の数字を公開）と同じ形なので、
 * 判定できないときも含めて**送らない側に倒す**（人にはメールで知らせる）。
 *   ※ alreadyRanToday() は逆に「判定できなければ走らせる」。
 *     あちらは二重に走っても害がないが、こちらは会員に届くので逆向きにする。
 */
const MORNING_NOTIFY = {
  at: "07:30", // ※ :00 か :30 だけ（cron の発火時刻）
  url: "https://ruletrade-app.vercel.app/api/cron/morning-notify",
  gate: "pipeline-morning.yml", // 07:00 の関門。これが success のときだけ叩く
};

/** now(UTC) を JST の壁時計に直す */
function toJst(now) {
  return new Date(now.getTime() + 9 * 60 * 60 * 1000);
}

function hhmm(jst) {
  const h = String(jst.getUTCHours()).padStart(2, "0");
  const m = String(jst.getUTCMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

function ghHeaders(env) {
  return {
    // User-Agent が無いと GitHub API は 403 を返す
    "User-Agent": "ruletrade-cron-worker",
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    Authorization: `Bearer ${env.GH_PAT}`,
    "Content-Type": "application/json",
  };
}

/**
 * 同じ日に、そのワークフローが既に走っている（or 走り終わった）か。
 *
 * ★二重起動防止★（起動文 Step 2）
 * Worker が二重に発火した場合や、GitHub 側の schedule が先に走った場合に、
 * 同じものをもう一度起こさないため。各ワークフローの冪等ガードでも守られるが、
 * 無駄な実行を減らすほうが観測が読みやすい。
 *
 * 判定できないとき（API 失敗など）は **false を返して起動させる**。
 * 「走らない」より「二重に走る」ほうが安全側だから。
 */
async function alreadyRanToday(workflow, jstToday, env) {
  const url =
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/runs?per_page=10`;
  try {
    const res = await fetch(url, { headers: ghHeaders(env) });
    if (!res.ok) return false;
    const data = await res.json();
    for (const run of data.workflow_runs || []) {
      // created_at は UTC。JST の日付に直して比べる
      const runJst = toJst(new Date(run.created_at));
      if (ymd(runJst) !== jstToday) continue;
      if (run.status === "in_progress" || run.status === "queued") return true;
      if (run.conclusion === "success") return true;
    }
    return false;
  } catch {
    return false;
  }
}

/**
 * そのワークフローの「当日の結果」を返す。
 *   "success" / "failure" / "running"（実行中・順待ち）/ "none"（当日の run が無い）
 *   "unknown"（API が答えない）
 * 直近の run を新しいものから見て、最初に見つかった当日のものを採る。
 */
async function todaysResult(workflow, jstToday, env) {
  const url =
    `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/runs?per_page=10`;
  try {
    const res = await fetch(url, { headers: ghHeaders(env) });
    if (!res.ok) return "unknown";
    const data = await res.json();
    for (const run of data.workflow_runs || []) {
      if (ymd(toJst(new Date(run.created_at))) !== jstToday) continue;
      if (run.status === "in_progress" || run.status === "queued") return "running";
      return run.conclusion === "success" ? "success" : "failure";
    }
    return "none";
  } catch {
    return "unknown";
  }
}

/**
 * 会員向けの朝の通知を叩く。gate が success のときだけ。
 * 送らなかったときは必ずメールで知らせる（黙って止めない）。
 */
async function morningNotify(jstToday, env) {
  const { url, gate } = MORNING_NOTIFY;
  const why = {
    failure: `${gate} が失敗しています（前営業日ぶんのデータが揃っていない）`,
    running: `${gate} がまだ終わっていません（07:00 の回が30分以上かかっている）`,
    none: `${gate} が今日まだ起動していません`,
    unknown: `${gate} の結果を GitHub API から確認できません`,
  };

  const r = await todaysResult(gate, jstToday, env);
  if (r !== "success") {
    console.error(`朝の通知を送りません: ${why[r]}`);
    await notify(
      "[ルールトレード] 朝の通知を送りませんでした",
      `${why[r]}。\n\n` +
        "古いデータで会員に届くのを避けるため、送信を見合わせました。\n" +
        `GitHub Actions: https://github.com/${REPO}/actions\n` +
        "直したあと、アプリ側の手動実行で送れます。",
      env,
    );
    return { ok: false, skipped: true, reason: r };
  }

  if (!env.CRON_SECRET) {
    console.error("CRON_SECRET が未設定のため朝の通知を叩けません");
    await notify(
      "[ルールトレード] cron Worker に CRON_SECRET がありません",
      `朝の通知（${url}）を叩こうとしましたが、\n` +
        "CRON_SECRET が未設定のため何もできませんでした。\n" +
        "npx wrangler secret put CRON_SECRET で登録してください。",
      env,
    );
    return { ok: false, reason: "no-secret" };
  }

  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.CRON_SECRET}`,
        "Content-Type": "application/json",
        "User-Agent": "ruletrade-cron-worker",
      },
      body: JSON.stringify({ date: jstToday }),
    });
    const body = (await res.text()).slice(0, 300);
    if (!res.ok) {
      console.error(`朝の通知に失敗: ${res.status} ${body}`);
      await notify(
        "[ルールトレード] 朝の通知の呼び出しが失敗しました",
        `${url}\n${res.status}\n${body}\n\n` +
          "401 なら CRON_SECRET がアプリ側と食い違っています。",
        env,
      );
      return { ok: false, status: res.status };
    }
    console.log(`朝の通知を叩きました: ${res.status} ${body}`);
    return { ok: true, status: res.status };
  } catch (e) {
    const msg = String(e).slice(0, 200);
    console.error("朝の通知で例外:", msg);
    await notify("[ルールトレード] 朝の通知で例外", `${url}\n${msg}`, env);
    return { ok: false, reason: "exception" };
  }
}

async function dispatch(workflow, env, inputs) {
  const url = `https://api.github.com/repos/${REPO}/actions/workflows/${workflow}/dispatches`;
  const body = { ref: REF };
  // ※ REST 経由の workflow_dispatch は入力を**文字列**で受け取る。
  //   yml 側も type: string で揃えてある（boolean だと取り違える）。
  if (inputs) body.inputs = inputs;
  const res = await fetch(url, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify(body),
  });
  // 成功は 204。本文は空
  const ok = res.status === 204;
  const detail = ok ? "" : ` ${res.status} ${(await res.text()).slice(0, 200)}`;
  console.log(`${ok ? "OK  " : "NG  "} ${workflow}${detail}`);
  return { workflow, ok, detail };
}

/**
 * 失敗をメールで知らせる（Resend）。
 * ここが落ちても本処理は止めない（通知は補助）。
 */
async function notify(subject, text, env) {
  if (!env.RESEND_API_KEY || !env.NOTIFY_TO) {
    console.error("通知先が未設定のためメールを送れません:", subject);
    return;
  }
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: "ルールトレード <noreply@ruletrade.jp>",
        to: [env.NOTIFY_TO],
        subject,
        text,
      }),
    });
    if (!res.ok) {
      console.error("メール送信に失敗:", res.status, (await res.text()).slice(0, 200));
    }
  } catch (e) {
    console.error("メール送信で例外:", String(e).slice(0, 200));
  }
}

/** ザラ場の時間帯か（"HH:MM" は辞書順がそのまま時刻順） */
export function inIntraday(key) {
  return key >= INTRADAY.from && key <= INTRADAY.to;
}

/** この時刻に起こすワークフローを決める */
export function targetsFor(jst) {
  const key = hhmm(jst);
  const dow = jst.getUTCDay();

  if (dow === 0 || dow === 6) {
    return { targets: (WEEKEND_PLAN[dow] || {})[key] || [], why: "週末の予定" };
  }

  const weekly = (WEEKDAY_ONLY_PLAN[dow] || {})[key];
  const base = weekly || PLAN[key] || [];
  const why = weekly ? `${dow}曜だけの予定` : "平日の予定";

  // ザラ場中は realtime-signal を上乗せする（:00 :15 :30 :45 の15分間隔）。
  // :15/:45 は base が空なので realtime-signal だけになる。
  if (!inIntraday(key)) return { targets: base, why };
  return {
    targets: [...base, INTRADAY.workflow],
    why: base.length ? `${why}＋ザラ場` : "ザラ場の15分間隔",
  };
}

export default {
  async scheduled(event, env, ctx) {
    const jst = toJst(new Date(event.scheduledTime));
    const key = hhmm(jst);
    const today = ymd(jst);
    const { targets, why } = targetsFor(jst);

    // 会員向けの朝の通知（HTTP）。ワークフローの起動とは別枠なので、
    // PLAN が空でも動くように targets の判定より前に置く。
    // 休場日は送らない（gate も走っていない）。
    const isMorningNotify = MORNING_NOTIFY.at === key && isTradingDay(jst);

    if (targets.length === 0 && !isMorningNotify) {
      console.log(`JST ${key}: 予定なし`);
      return;
    }
    if (!env.GH_PAT) {
      // 黙って何もしないと「動いているつもり」になる。必ず記録に残す
      console.error("GH_PAT が設定されていません。何も起動できません");
      ctx.waitUntil(
        notify(
          "[ルールトレード] cron Worker に GH_PAT がありません",
          `JST ${key} に ${targets.join(", ")} を起動しようとしましたが、\n` +
            "GH_PAT が未設定のため何もできませんでした。\n" +
            "npx wrangler secret put GH_PAT で登録してください。",
          env,
        ),
      );
      return;
    }

    // 東証が閉まっている日は、市場データを作るものを起こさない。
    // 週末バッチ（pipeline-weekly）だけは土曜に動かす。
    const marketOpen = isTradingDay(jst);

    // 朝の通知は gate（pipeline-morning）の結果を見てから叩く。
    // 送らなかった場合も中で必ずメールを出すので、ここでは結果を見るだけ。
    if (isMorningNotify) {
      const r = await morningNotify(today, env);
      console.log(`JST ${key}: 朝の通知 → ${r.ok ? "送信" : "見送り"}`);
    }
    const runnable = marketOpen
      ? targets
      : targets.filter((w) => w === "pipeline-weekly.yml");

    if (runnable.length === 0) {
      console.log(`JST ${key}: 休場日（${today}）なので何もしない`);
      return;
    }
    if (!isKnownYear(today)) {
      // 休場日表の範囲外。動く側に倒すが、気づけるようにログに残す
      console.warn(`休場日表に ${today.slice(0, 4)} 年が無い。祝日でも動きます`);
    }

    console.log(`JST ${key}（${why}）: ${runnable.length} 本を起動します`);

    const backupHere = BACKUP_AT[key] || new Set();
    const results = [];
    for (const wf of runnable) {
      // 順に叩く（並列にしない＝順序を保つ）
      // realtime-signal は1日に何度も走るのが正常なので重複確認をしない
      if (!NO_DEDUP.has(wf) && (await alreadyRanToday(wf, today, env))) {
        console.log(`SKIP ${wf}（${today} に既に実行済み/実行中）`);
        results.push({ workflow: wf, ok: true, skipped: true });
        continue;
      }
      // 予備の回は「データは作るが人に届く通知は出さない」
      const inputs = backupHere.has(wf) ? { backup: "true" } : null;
      if (inputs) console.log(`${wf} は予備の回として起動します（通知なし）`);
      results.push(await dispatch(wf, env, inputs));
    }

    const failed = results.filter((r) => !r.ok);
    if (failed.length > 0) {
      ctx.waitUntil(
        notify(
          `[ルールトレード] ワークフローを起動できませんでした（JST ${key}）`,
          "起動に失敗したもの:\n" +
            failed.map((f) => `  - ${f.workflow}${f.detail || ""}`).join("\n") +
            `\n\nGitHub Actions: https://github.com/${REPO}/actions\n` +
            "PAT の期限切れ・権限不足がよくある原因です。",
          env,
        ),
      );
    }
  },

  /**
   * 動作確認用。ブラウザで開くと今の JST と、次に何が起きるかを返す。
   * 起動はしない（GET で副作用を起こさない）。
   */
  async fetch(request, env) {
    const jst = toJst(new Date());
    const today = ymd(jst);
    const { targets, why } = targetsFor(jst);
    const body = {
      now_jst: jst.toISOString().replace("T", " ").slice(0, 16),
      trading_day: isTradingDay(jst),
      holiday_table_covers_year: isKnownYear(today),
      secrets: {
        GH_PAT: Boolean(env.GH_PAT),
        RESEND_API_KEY: Boolean(env.RESEND_API_KEY),
        NOTIFY_TO: Boolean(env.NOTIFY_TO),
        CRON_SECRET: Boolean(env.CRON_SECRET),
      },
      repo: REPO,
      this_minute: { why, targets },
      intraday: { ...INTRADAY, now_in_range: inIntraday(hhmm(jst)) },
      morning_notify: MORNING_NOTIFY,
      backup_at: Object.fromEntries(
        Object.entries(BACKUP_AT).map(([k, v]) => [k, [...v]]),
      ),
      plan: PLAN,
      weekend_plan: WEEKEND_PLAN,
      weekday_only_plan: WEEKDAY_ONLY_PLAN,
    };
    return new Response(JSON.stringify(body, null, 2), {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  },
};
