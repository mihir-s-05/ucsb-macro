import { ApiError, currentJoin, metadata, readResource, snapshotFields, type Snapshot } from "./data";
import { publicApi } from "./public-api";

const corsHeaders = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
  "Access-Control-Allow-Headers": "Accept, Content-Type", "Access-Control-Max-Age": "86400" };

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": status === 200 ? "public, max-age=60, s-maxage=60" : "no-store",
      "X-Content-Type-Options": "nosniff",
      ...corsHeaders,
      ...(status === 405 ? { Allow: "GET, HEAD, OPTIONS" } : {}),
    },
  });
}

function id(query: URLSearchParams, name: string): string {
  const value = query.get(name);
  if (!value || !/^\d+$/.test(value) || !Number.isSafeInteger(Number(value))) {
    throw new Error(`Invalid ${name} ID.`);
  }
  return String(Number(value));
}

function resourceKey(url: URL): string | null {
  const q = url.searchParams;
  if (url.pathname === "/api/catalog") return "catalog";
  if (url.pathname === "/api/options") return `options:${id(q, "hall")}:${id(q, "section")}`;
  if (url.pathname === "/api/menu") return `menu:${id(q, "hall")}:${id(q, "section")}:${id(q, "menu")}`;
  if (url.pathname === "/api/label") return `label:${id(q, "hall")}:${id(q, "section")}:${id(q, "menu")}:${id(q, "item")}`;
  return null;
}

export default {
  async fetch(request, env): Promise<Response> {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/") && url.pathname !== "/api") return env.ASSETS.fetch(request);
    if (request.method === "OPTIONS") {
      const method = request.headers.get("Access-Control-Request-Method");
      if (method && !["GET", "HEAD"].includes(method)) return json({ error: "This API is read-only." }, 405);
      return new Response(null, { status: 204, headers: { ...corsHeaders, Allow: "GET, HEAD, OPTIONS" } });
    }
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ error: "This API is read-only." }, 405);
    }
    try {
      if (["/api", "/api/", "/api/v1", "/api/v1/"].includes(url.pathname)) {
        return json({ version: "1", base_url: `${url.origin}/api/v1`, docs: `${url.origin}/docs`,
          openapi: `${url.origin}/api/openapi.json`, endpoints: ["halls", "menus", "menu", "item", "label", "health"] });
      }
      if (url.pathname === "/api/openapi.json") {
        const asset = await env.ASSETS.fetch(request);
        const response = new Response(asset.body, asset);
        for (const [name, value] of Object.entries(corsHeaders)) response.headers.set(name, value);
        response.headers.set("Cache-Control", asset.ok ? "public, max-age=60, s-maxage=60" : "no-store");
        response.headers.set("X-Content-Type-Options", "nosniff");
        return response;
      }
      if (url.pathname === "/api/health" || url.pathname === "/api/v1/health") {
        const snapshot = await env.DB.prepare(`SELECT ${snapshotFields} ${currentJoin} WHERE c.singleton = 1`).first<Snapshot>();
        return snapshot ? json({ ok: !metadata(snapshot).stale, snapshot: metadata(snapshot), plan_storage: "browser" })
          : json({ error: "Menus have not been published yet." }, 503);
      }
      if (url.pathname.startsWith("/api/v1/")) return json(await publicApi(url, env));
      const key = resourceKey(url);
      if (!key) return json({ error: "Not found." }, 404);
      return json(await readResource(env, key));
    } catch (error) {
      if (error instanceof ApiError) return json({ error: error.message, ...(error.choices ? { choices: error.choices } : {}) }, error.status);
      if (error instanceof Error && error.message.startsWith("Invalid ")) return json({ error: error.message }, 400);
      console.error(JSON.stringify({ event: "api_error", path: url.pathname, error: String(error) }));
      return json({ error: "Menus are temporarily unavailable." }, 503);
    }
  },
} satisfies ExportedHandler<Env>;
