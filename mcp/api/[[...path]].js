// Vercel Functions エントリ(Web標準 Request/Response シグネチャ)。
// vercel.json の rewrite で全パスをここに集約する。
import { handle } from "../src/server.js";

const run = (request) => handle(request, process.env);
export const GET = run;
export const POST = run;
export const DELETE = run;
export const OPTIONS = run;
