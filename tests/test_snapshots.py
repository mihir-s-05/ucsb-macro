import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ucsb_macros.collector import PUBLISH_SQL, PRUNE_SQL, write_snapshot, sql_batches


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        schema = Path(__file__).parents[1] / "migrations/0001_snapshots.sql"
        self.db.executescript(schema.read_text())
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.db.close()
        self.folder.cleanup()

    def stage(self, name, date):
        output = Path(self.folder.name) / name
        resources = {"catalog": ({"halls": [{"id": 15, "name": "Portola's menu"}]}, None),
                     "label:15:16:7:9": ({"name": "Chef's dish"}, "hash")}
        manifest = write_snapshot(output, resources, {"hash": '<div id="nutritionLabel">Chef\'s dish</div>'}, {},
                                  snapshot_id=name, collected_at=date)
        self.db.executescript((output / "stage.sql").read_text())
        return manifest

    def current(self):
        return self.db.execute("SELECT snapshot_id FROM current_snapshot").fetchone()

    def test_partial_upload_cannot_change_published_data(self):
        self.stage("old", "2026-10-09T00:00:00Z")
        self.db.execute(PUBLISH_SQL, ["now", "old"])
        self.db.execute("INSERT INTO snapshots VALUES ('partial','2026-10-09T06:00:00Z',2,1)")
        self.db.execute("INSERT INTO resources VALUES ('partial','catalog','{}',NULL)")
        self.db.execute(PUBLISH_SQL, ["now", "partial"])
        self.assertEqual(self.current(), ("old",))
        visible = self.db.execute("SELECT payload FROM resources JOIN current_snapshot ON current_snapshot.snapshot_id=resources.snapshot_id WHERE key='catalog'").fetchone()
        self.assertEqual(json.loads(visible[0])["halls"][0]["id"], 15)

    def test_publication_then_pruning_retains_complete_current_snapshot(self):
        self.stage("old", "2026-10-09T00:00:00Z")
        self.db.execute(PUBLISH_SQL, ["now", "old"])
        self.stage("new", "2026-10-09T06:00:00Z")
        self.assertEqual(self.current(), ("old",))
        self.db.execute(PUBLISH_SQL, ["now", "new"])
        self.db.execute(PRUNE_SQL)
        self.assertEqual(self.current(), ("new",))
        self.assertEqual(self.db.execute("SELECT id FROM snapshots").fetchall(), [("new",)])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM resources").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM labels").fetchone()[0], 1)

    def test_older_run_cannot_replace_newer_snapshot(self):
        self.stage("new", "2026-10-09T06:00:00Z")
        self.db.execute(PUBLISH_SQL, ["now", "new"])
        self.stage("old", "2026-10-09T00:00:00Z")
        self.db.execute(PUBLISH_SQL, ["now", "old"])
        self.assertEqual(self.current(), ("new",))

    def test_upload_batches_preserve_multiline_labels_and_can_be_retried(self):
        output = Path(self.folder.name) / "batched"
        document = "<p>Chef's dish;\nBeans & rice</p>"
        write_snapshot(output, {"label": ({"name": "Dish"}, "hash")}, {"hash": document}, {},
                       snapshot_id="batched")
        batches = list(sql_batches(output / "stage.sql", max_statements=1))
        self.assertEqual(len(batches), 3)
        for _ in range(2):
            for batch in batches:
                self.db.executescript(batch)
        self.assertEqual(self.db.execute("SELECT document FROM labels").fetchone()[0], document)
        self.assertIsNone(self.current())


if __name__ == "__main__":
    unittest.main()
