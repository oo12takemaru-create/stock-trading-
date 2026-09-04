// Cloudflare Workers エントリ
import { handle } from "./src/server.js";
export default {
  fetch: (request, env, ctx) => handle(request, env, ctx),
};
