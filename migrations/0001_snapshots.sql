CREATE TABLE snapshots (
  id TEXT PRIMARY KEY,
  collected_at TEXT NOT NULL,
  resource_count INTEGER NOT NULL,
  label_count INTEGER NOT NULL
) STRICT;

CREATE TABLE labels (
  snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  hash TEXT NOT NULL,
  document TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, hash)
) STRICT;

CREATE TABLE resources (
  snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
  key TEXT NOT NULL,
  payload TEXT NOT NULL CHECK (json_valid(payload)),
  label_hash TEXT,
  PRIMARY KEY (snapshot_id, key),
  FOREIGN KEY (snapshot_id, label_hash) REFERENCES labels(snapshot_id, hash)
) STRICT;

CREATE TABLE current_snapshot (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  snapshot_id TEXT NOT NULL REFERENCES snapshots(id),
  published_at TEXT NOT NULL
) STRICT;
