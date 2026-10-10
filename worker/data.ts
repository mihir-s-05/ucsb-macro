export type Snapshot = { id: string; collected_at: string; published_at: string };
type Resource = Snapshot & { payload: string; document: string | null };
export const currentJoin = `FROM current_snapshot c JOIN snapshots s ON s.id = c.snapshot_id`;
export const snapshotFields = `s.id, s.collected_at, c.published_at`;

export class ApiError extends Error {
  status: number;
  choices?: { id: number; name: string }[];
  constructor(status: number, message: string, choices?: { id: number; name: string }[]) {
    super(message);
    this.status = status;
    this.choices = choices;
  }
}

export function metadata(snapshot: Snapshot) {
  const age = Math.max(0, Math.floor((Date.now() - Date.parse(snapshot.collected_at)) / 1000));
  return { id: snapshot.id, collected_at: snapshot.collected_at, published_at: snapshot.published_at,
    age_seconds: age, stale: age > 12 * 3600 };
}

export async function readResource<T extends object = Record<string, unknown>>(env: Env, key: string, snapshot?: string) {
  // Pin dependent reads to the same published snapshot. Publication/pruning yields a retryable 409.
  const statement = env.DB.prepare(`SELECT ${snapshotFields}, r.payload, l.document
    ${currentJoin} JOIN resources r ON r.snapshot_id = s.id
    LEFT JOIN labels l ON l.snapshot_id = r.snapshot_id AND l.hash = r.label_hash
    WHERE c.singleton = 1 AND r.key = ? ${snapshot ? "AND s.id = ?" : ""}`);
  const row = await (snapshot ? statement.bind(key, snapshot) : statement.bind(key)).first<Resource>();
  if (!row) {
    if (snapshot) {
      const current = await env.DB.prepare(`SELECT snapshot_id FROM current_snapshot WHERE singleton = 1`)
        .first<{ snapshot_id: string }>();
      if (current && current.snapshot_id !== snapshot) throw new ApiError(409, "Menus changed during this request. Retry it.");
    }
    throw new ApiError(404, "This menu is unavailable. Reload to get the latest menus.");
  }
  return { ...JSON.parse(row.payload) as T, ...(row.document === null ? {} : { document: row.document }),
    snapshot: metadata(row), plan_storage: "browser" };
}
