import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ucsb_macros import plans


def meal(name="Lunch"):
    return {"name": name, "date": "2026-10-09", "meal": "Lunch", "items": [
        {"id": 9, "name": "Chicken", "serving": "1 piece", "servings": 2,
         "calories": 200, "protein_g": 24, "carbs_g": None, "fat_g": 9,
         "hall": "Portola", "source_date": "2026-10-09"}], "totals": {"calories": 999}}


class PlanTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Path(self.temp.name) / "plans.json"
        self.patch = patch.object(plans, "STORE", self.store)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_browser_json_import_recomputes_totals_and_preserves_snapshots(self):
        imported = plans.import_plans(json.loads(json.dumps([meal()])))[0]
        self.assertEqual(imported["totals"]["calories"], 400)
        self.assertIsNone(imported["totals"]["carbs_g"])
        self.assertEqual(imported["items"][0]["source_date"], "2026-10-09")
        self.assertEqual(plans.list_plans()[0], imported)

    def test_invalid_second_plan_does_not_partially_import(self):
        plans.save_plan(meal())
        before = self.store.read_bytes()
        broken = meal("Broken"); broken["items"][0]["servings"] = -1
        with self.assertRaises(ValueError):
            plans.import_plans([meal("Dinner"), broken])
        self.assertEqual(self.store.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
