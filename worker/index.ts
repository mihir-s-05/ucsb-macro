type Snapshot = { id: string; collected_at: string; published_at: string };
type Resource = Snapshot & { payload: string; document: string | null };

const currentJoin = `FROM current_snapshot c JOIN snapshots s ON s.id = c.snapshot_id`;
const snapshotFields = `s.id, s.collected_at, c.published_at`;

function json(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "Cache-Control": status === 200 ? "public, max-age=60, s-maxage=60" : "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}

function metadata(snapshot: Snapshot) {
  const age = Math.max(0, Math.floor((Date.now() - Date.parse(snapshot.collected_at)) / 1000));
  return { id: snapshot.id, collected_at: snapshot.collected_at, published_at: snapshot.published_at,
    age_seconds: age, stale: age > 12 * 3600 };
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
    if (!url.pathname.startsWith("/api/")) return env.ASSETS.fetch(request);
    if (request.method !== "GET" && request.method !== "HEAD") {
      return json({ error: "This API is read-only." }, 405);
    }
    try {
      if (url.pathname === "/api/health") {
        const snapshot = await env.DB.prepare(`SELECT ${snapshotFields} ${currentJoin} WHERE c.singleton = 1`).first<Snapshot>();
        return snapshot ? json({ ok: !metadata(snapshot).stale, snapshot: metadata(snapshot), plan_storage: "browser" })
          : json({ error: "Menus have not been published yet." }, 503);
      }
      const key = resourceKey(url);
      if (!key) return json({ error: "Not found." }, 404);
      // The pointer and payload are read in one SQL statement, including during publication/pruning.
      const row = await env.DB.prepare(`SELECT ${snapshotFields}, r.payload, l.document
        ${currentJoin} JOIN resources r ON r.snapshot_id = s.id
        LEFT JOIN labels l ON l.snapshot_id = r.snapshot_id AND l.hash = r.label_hash
        WHERE c.singleton = 1 AND r.key = ?`).bind(key).first<Resource>();
      if (!row) return json({ error: "This menu is unavailable. Reload to get the latest menus." }, 404);
      const data: Record<string, unknown> = JSON.parse(row.payload);
      if (row.document !== null) data.document = row.document;
      return json({ ...data, snapshot: metadata(row), plan_storage: "browser" });
    } catch (error) {
      if (error instanceof Error && error.message.startsWith("Invalid ")) return json({ error: error.message }, 400);
      console.error(JSON.stringify({ event: "api_error", path: url.pathname, error: String(error) }));
      return json({ error: "Menus are temporarily unavailable." }, 503);
    }
  },
} satisfies ExportedHandler<Env>;
