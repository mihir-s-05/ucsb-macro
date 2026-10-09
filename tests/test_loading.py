import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import requests

from ucsb_macros import app

FIXTURES = Path(__file__).parent / "fixtures"


class Loading(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_dir = app.DATA_DIR
        app.DATA_DIR = Path(self.temp.name)
        app._cache.clear()
        app._key_locks.clear()
        app._retry_after.clear()

    def wait_for_refresh(self):
        deadline = time.monotonic() + 3
        while app._refreshing and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse(app._refreshing)

    def tearDown(self):
        self.wait_for_refresh()
        app.DATA_DIR = self.original_dir
        app._cache.clear()
        app._key_locks.clear()
        app._retry_after.clear()
        self.temp.cleanup()

    def test_slow_build_does_not_block_other_keys_and_duplicate_requests_share_it(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def build():
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(3))
            return {"value": "menu"}

        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(app.cached, ("slow",), build)
            try:
                self.assertTrue(entered.wait(1))
                duplicate = pool.submit(app.cached, ("slow",), build)
                other = pool.submit(app.cached, ("other",), lambda: {"value": "ready"})
                self.assertEqual(other.result(timeout=1), {"value": "ready"})
            finally:
                release.set()
            self.assertEqual(first.result(timeout=1), duplicate.result(timeout=1))
        self.assertEqual(len(calls), 1)

    def test_expired_menu_is_immediate_and_refreshes_without_losing_its_timestamp(self):
        key = ("expired",)
        old = {"fetched_at": "old", "items": [1]}
        app._cache[key] = (time.time() - app.CACHE_SECONDS - 1, old)
        entered, release = threading.Event(), threading.Event()
        calls = []

        def build():
            calls.append(1)
            entered.set()
            self.assertTrue(release.wait(3))
            return {"fetched_at": "new", "items": [2]}

        try:
            result = app.cached(key, build, stale=True)
            self.assertEqual(result["items"], [1])
            self.assertEqual(result["fetched_at"], "old")
            self.assertTrue(result["cached"])
            self.assertTrue(result["refreshing"])
            self.assertTrue(entered.wait(1))
            self.assertEqual(app.cached(key, build, stale=True), result)
        finally:
            release.set()
        self.wait_for_refresh()
        self.assertEqual(app.cached(key, build), {"fetched_at": "new", "items": [2]})
        self.assertEqual(len(calls), 1)

    def test_failed_refresh_preserves_snapshot_and_backs_off(self):
        key = ("offline",)
        timestamp = time.time() - app.CACHE_SECONDS - 1
        app._cache[key] = (timestamp, {"fetched_at": "old"})
        def offline():
            raise requests.RequestException("Offline")
        app.refresh_cache(key, offline, app.CACHE_SECONDS)
        self.assertEqual(app._cache[key][0], timestamp)
        with patch.object(app.threading, "Thread") as thread:
            result = app.cached(key, lambda: self.fail("Unexpected retry"), stale=True)
        thread.assert_not_called()
        self.assertEqual(result["fetched_at"], "old")
        self.assertFalse(result["refreshing"])

    def test_cache_survives_a_process_restart(self):
        key = ("disk",)
        app.cached(key, lambda: {"value": "saved"})
        app._cache.clear()
        self.assertEqual(app.cached(key, lambda: self.fail("Unexpected fetch")), {"value": "saved"})

    def test_parallel_downloads_keep_sessions_food_values_and_order_separate(self):
        raw = (FIXTURES / "chicken-label.html").read_text(encoding="utf-8")
        barrier = threading.Barrier(4)
        instances = []

        class FakeClient:
            def __init__(self):
                self.landing = '<link rel="stylesheet" href="Stylesheets/Nutrition_Label_FDA2018.css">'
                self.active = threading.Lock()
                self.first_label = True
                self.closed = False
                self.selection = None
                instances.append(self)

            def select(self, root, child):
                self.selection = (root, child)
                return {}

            def panels(self, endpoint, data):
                self.menu = data["menuOid"]
                return {"itemPanel": "menu"}

            def request(self, method, endpoint, data):
                if not self.active.acquire(blocking=False):
                    raise AssertionError("Two workers used one session")
                try:
                    if self.selection != (15, 16) or self.menu != 42:
                        raise AssertionError("Wrong hall or menu selected")
                    if self.first_label:
                        self.first_label = False
                        barrier.wait(timeout=2)
                    item_id = data["detailOid"]
                    return raw.replace("Grilled Achiote Chicken Thigh", f"Food {item_id}").replace(">200<", f">{item_id}<")
                finally:
                    self.active.release()

            def close(self):
                self.closed = True

        items = [{"id": i, "name": f"Food {i}", "menu_serving": "Piece", "category": "Station"} for i in range(1, 9)]
        choices = {"hall": "Portola", "section": "Daily Menu", "menus": [{"id": 42, "meal": "Lunch"}]}
        with patch.object(app, "NetNutrition", FakeClient), patch.object(app, "options", return_value=choices), patch.object(app, "parse_items", return_value=items):
            result = app.menu(15, 16, 42)
        self.assertEqual([i["id"] for i in result["items"]], list(range(1, 9)))
        self.assertEqual([i["label_name"] for i in result["items"]], [f"Food {i}" for i in range(1, 9)])
        self.assertEqual([i["calories"] for i in result["items"]], list(range(1, 9)))
        self.assertEqual(len(instances), 4)
        self.assertTrue(all(c.closed for c in instances))


if __name__ == "__main__":
    unittest.main()
