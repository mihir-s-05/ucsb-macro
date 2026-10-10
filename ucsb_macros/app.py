"""Local UCSB NetNutrition adapter. Anonymous sessions; no browser automation needed."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup
from . import plans
from .storage import data_dir
from .labels import DOCUMENT_VERSION, label_document

BASE = "https://nutrition.info.dining.ucsb.edu/NetNutrition/1"
USER_AGENT = "Gaucho/0.1 (+https://github.com/mihir-s-05/ucsb-macro)"
HERE = Path(__file__).resolve().parent
CACHE_SECONDS = 900
CATALOG_SECONDS = 86400
MAX_STALE_SECONDS = 86400
FETCH_WORKERS = 4
_cache: dict[tuple, tuple[float, object]] = {}
_lock = threading.RLock()
_key_locks: dict[tuple, threading.RLock] = {}
_refreshing: set[tuple] = set()
_retry_after: dict[tuple, float] = {}
_upstream_slots = threading.BoundedSemaphore(FETCH_WORKERS)
DATA_DIR = data_dir()


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def cache_file(key):
    return DATA_DIR / "cache" / (hashlib.sha256(json.dumps(["v2", key]).encode()).hexdigest() + ".json")


def cache_entry(key):
    with _lock:
        entry = _cache.get(key)
        if entry:
            return entry
        try:
            disk = json.loads(cache_file(key).read_text(encoding="utf-8"))
            entry = (float(disk["timestamp"]), disk["value"])
            _cache[key] = entry
            return entry
        except (OSError, ValueError, KeyError, TypeError):
            return None


def refresh_cache(key, build, ttl):
    try:
        cached(key, build, ttl=ttl)
    except (requests.RequestException, ValueError, OSError):
        # Keep the dated snapshot available if the source is temporarily down.
        with _lock:
            _retry_after[key] = time.time() + 60
    finally:
        with _lock:
            _refreshing.discard(key)


def cached(key, build, *, ttl=CACHE_SECONDS, stale=False):
    entry = cache_entry(key)
    if entry:
        age = time.time() - entry[0]
        if 0 <= age < ttl:
            return entry[1]
        if stale and ttl <= age < ttl + MAX_STALE_SECONDS and isinstance(entry[1], dict):
            with _lock:
                if key not in _refreshing and time.time() >= _retry_after.get(key, 0):
                    _refreshing.add(key)
                    threading.Thread(target=refresh_cache, args=(key, build, ttl), daemon=True).start()
                refreshing = key in _refreshing
            return {**entry[1], "cached": True, "refreshing": refreshing}
    with _lock:
        key_lock = _key_locks.setdefault(key, threading.RLock())
    # Duplicate requests share a build; unrelated keys never wait for its network I/O.
    with key_lock:
        entry = cache_entry(key)
        if entry and 0 <= time.time() - entry[0] < ttl:
            return entry[1]
        value = build()
        timestamp = time.time()
        file = cache_file(key)
        file.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=file.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"timestamp": timestamp, "value": value}, stream, ensure_ascii=False)
        try:
            temporary.replace(file)
        finally:
            temporary.unlink(missing_ok=True)
        with _lock:
            _cache[key] = (timestamp, value)
        return value


class NetNutrition:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Referer": BASE,
            "X-Requested-With": "XMLHttpRequest",
        })
        self.landing = self.request("GET", "")

    def request(self, method, endpoint, data=None):
        with _upstream_slots:
            r = self.session.request(method, BASE + endpoint, data=data, timeout=25)
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text

    def panels(self, endpoint, data):
        raw = self.request("POST", endpoint, data)
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("NetNutrition returned a page instead of menu data. Try again.") from exc
        if result.get("success") is not True or "panels" not in result:
            raise ValueError("NetNutrition could not load that selection.")
        return {p["id"]: p["html"] for p in result["panels"]}

    def select(self, root_id, child_id):
        panels = self.panels("/Unit/SelectUnitFromUnitsList", {"unitOid": root_id})
        if child_id != root_id:
            panels = self.panels("/Unit/SelectUnitFromChildUnitsList", {"unitOid": child_id})
        return panels

    def close(self):
        self.session.close()


def parse_units(html, function):
    soup = BeautifulSoup(html, "html.parser")
    units = []
    for a in soup.select("a[onclick]"):
        match = re.search(r"\b" + re.escape(function) + r"\((\d+)\)", a["onclick"])
        if match:
            units.append({"id": int(match[1]), "name": text(a)})
    return units


def catalog(*, stale=False):
    def build():
        client = NetNutrition()
        try:
            halls = parse_units(client.landing, "unitsSelectUnit")
            for hall in halls:
                panels = client.select(hall["id"], hall["id"])
                hall["sections"] = parse_units(panels.get("childUnitsPanel", ""), "childUnitsSelectUnit") or [
                    {"id": hall["id"], "name": "Menu"}
                ]
            if not halls:
                raise ValueError("Could not find dining locations. The source layout may have changed.")
            return {"halls": halls, "source": BASE}
        finally:
            client.close()
    return cached(("catalog",), build, ttl=CATALOG_SECONDS, stale=stale)


def validate_unit(root_id, child_id, *, stale=False):
    hall = next((h for h in catalog(stale=stale)["halls"] if h["id"] == root_id), None)
    section = next((s for s in hall["sections"] if s["id"] == child_id), None) if hall else None
    if not section:
        raise ValueError("Choose a location and section from the current catalog.")
    return hall, section


def parse_menus(html):
    soup = BeautifulSoup(html, "html.parser")
    menus = []
    for a in soup.select("a[onclick]"):
        match = re.search(r"menuListSelectMenu\((\d+)\)", a["onclick"])
        if not match:
            continue
        card = a.find_parent("section", class_="card")
        date_label = text(card.find("header")) if card else ""
        try:
            date = datetime.strptime(date_label, "%A, %B %d, %Y").date().isoformat()
        except ValueError:
            date = date_label
        menus.append({"id": int(match[1]), "meal": text(a), "date": date, "date_label": date_label})
    return menus


def options(root_id, child_id, *, stale=False):
    hall, section = validate_unit(root_id, child_id, stale=stale)
    def build():
        client = NetNutrition()
        try:
            panels = client.select(root_id, child_id)
            menus = parse_menus(panels.get("menuPanel", ""))
            if not menus and 'showNutrition_' in panels.get("itemPanel", ""):
                menus = [{"id": 0, "meal": "Everyday menu", "date": "everyday", "date_label": "Everyday menu"}]
            return {"hall": hall["name"], "section": section["name"], "menus": menus}
        finally:
            client.close()
    return cached(("options", root_id, child_id), build, stale=stale)


def parse_items(html):
    soup = BeautifulSoup(html, "html.parser")
    result = []
    category = "Menu"
    for row in soup.select("tr"):
        button = row.select_one("[role=button]") if "cbo_nn_itemGroupRow" in row.get("class", []) else None
        if button:
            category = text(button).rstrip("").strip()
        link = row.select_one("a[id^=showNutrition_]")
        if not link:
            continue
        cells = row.find_all("td", recursive=False)
        # Some layouts put item names outside a standard row; fail visibly if absent.
        item_id = int(link["id"].split("_")[1])
        result.append({
            "id": item_id, "name": text(link), "category": category,
            "menu_serving": text(cells[2]) if len(cells) > 2 else "",
        })
    return result


def parse_label(html):
    soup = BeautifulSoup(html, "html.parser")
    header = soup.select_one(".cbo_nn_LabelHeader")
    if not header:
        raise ValueError("Nutrition label unavailable for this item.")
    serving = None
    for node in soup.select(".cbo_nn_LabelBottomBorderLabel .inline-div-right"):
        if text(node):
            serving = text(node)
            break
    calories_node = soup.select_one(".cbo_nn_LabelSubHeader .inline-div-right")
    calorie_text = text(calories_node).replace(",", "")
    def number(value):
        match = re.fullmatch(r"(?:<\s*)?(\d+(?:\.\d+)?)", value)
        return float(match[1]) if match and not value.startswith("<") else None
    nutrients = {}
    displays = {}
    for node in soup.select(".inline-div-left"):
        value = text(node)
        match = re.fullmatch(r"(Total Fat|Total Carbohydrate|Protein|Dietary Fiber|Total Sugars|Sodium)\s+(<\s*)?(\d+(?:\.\d+)?)\s*(g|mg)", value)
        if match:
            nutrients[match[1]] = None if match[2] else float(match[3])
            displays[match[1]] = ("<" if match[2] else "") + match[3]
    return {
        "label_name": text(header), "serving": serving,
        "calories": number(calorie_text), "protein_g": nutrients.get("Protein"),
        "carbs_g": nutrients.get("Total Carbohydrate"), "fat_g": nutrients.get("Total Fat"),
        "fiber_g": nutrients.get("Dietary Fiber"), "sugar_g": nutrients.get("Total Sugars"),
        "sodium_mg": nutrients.get("Sodium"),
        "macro_display": {"calories": calorie_text, "protein_g": displays.get("Protein"),
                          "carbs_g": displays.get("Total Carbohydrate"), "fat_g": displays.get("Total Fat")},
    }


def fetch_label(client, root_id, child_id, menu_id, item_id, *, stale=False):
    def build():
        raw = client.request("POST", "/NutritionDetail/ShowItemNutritionLabel", {"detailOid": item_id})
        return label_document(raw, client.landing)
    return cached(("label", DOCUMENT_VERSION, root_id, child_id, menu_id, item_id), build, stale=stale)


def load_label_batch(items, root_id, child_id, menu_id, seed=None):
    # One session per worker, with its own hall/menu selection and sequential requests.
    client = seed
    try:
        if client is None:
            client = NetNutrition()
            client.select(root_id, child_id)
            if menu_id:
                client.panels("/Menu/SelectMenu", {"menuOid": menu_id})
        for item in items:
            try:
                label = fetch_label(client, root_id, child_id, menu_id, item["id"])
                item.update(parse_label(label["document"]))
            except (requests.RequestException, ValueError) as exc:
                item.update({"serving": item["menu_serving"], "calories": None,
                             "protein_g": None, "carbs_g": None, "fat_g": None,
                             "error": str(exc)})
    except (requests.RequestException, ValueError) as exc:
        for item in items:
            item.update({"serving": item["menu_serving"], "calories": None,
                         "protein_g": None, "carbs_g": None, "fat_g": None,
                         "error": str(exc)})
    finally:
        if client is not None and seed is None:
            client.close()


def menu(root_id, child_id, menu_id, *, stale=False):
    choices = options(root_id, child_id, stale=stale)
    selected = next((m for m in choices["menus"] if m["id"] == menu_id), None)
    if not selected:
        raise ValueError("Choose a meal from the available menus.")
    def build():
        client = NetNutrition()
        try:
            panels = client.select(root_id, child_id)
            if menu_id:
                panels = client.panels("/Menu/SelectMenu", {"menuOid": menu_id})
            items = parse_items(panels.get("itemPanel", ""))
            if not items:
                raise ValueError("No menu items were found for that meal.")
            workers = min(FETCH_WORKERS, len(items))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(load_label_batch, items[index::workers], root_id, child_id, menu_id,
                                       client if index == 0 else None) for index in range(workers)]
                for future in futures:
                    future.result()
            return {"hall": choices["hall"], "section": choices["section"], **selected,
                    "items": items, "source": BASE,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "calorie_basis": "Rounded individual nutrition labels"}
        finally:
            client.close()
    return cached(("menu", root_id, child_id, menu_id), build, stale=stale)


def nutrition_label(root_id, child_id, menu_id, item_id):
    data = menu(root_id, child_id, menu_id, stale=True)
    if not any(item["id"] == item_id for item in data["items"]):
        raise ValueError("That food is not in the selected menu.")
    def build():
        client = NetNutrition()
        try:
            client.select(root_id, child_id)
            if menu_id:
                client.panels("/Menu/SelectMenu", {"menuOid": menu_id})
            raw = client.request("POST", "/NutritionDetail/ShowItemNutritionLabel", {"detailOid": item_id})
            return label_document(raw, client.landing)
        finally:
            client.close()
    return cached(("label", DOCUMENT_VERSION, root_id, child_id, menu_id, item_id), build, stale=True)


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path)
        try:
            if path.path == "/":
                self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
                return
            if path.path == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
                return
            if path.path == "/api/catalog":
                result = catalog(stale=True)
            elif path.path == "/api/plans":
                result = {"plans": plans.list_plans()}
            elif path.path in ("/api/options", "/api/menu", "/api/label"):
                query = parse_qs(path.query)
                root_id, child_id = int(query["hall"][0]), int(query["section"][0])
                if path.path == "/api/options":
                    result = options(root_id, child_id, stale=True)
                elif path.path == "/api/menu":
                    result = menu(root_id, child_id, int(query["menu"][0]), stale=True)
                else:
                    result = nutrition_label(root_id, child_id, int(query["menu"][0]), int(query["item"][0]))
            else:
                self.send(404, b"Not found", "text/plain")
                return
            self.send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        except (KeyError, ValueError) as exc:
            self.send(400, json.dumps({"error": str(exc)}).encode(), "application/json")
        except requests.RequestException:
            self.send(502, json.dumps({"error": "UCSB's nutrition site did not respond. Please try again."}).encode(), "application/json")

    def do_POST(self):
        if self.path != "/api/plans":
            self.send(404, b"Not found", "text/plain")
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 500_000:
                raise ValueError("Invalid plan size.")
            data = json.loads(self.rfile.read(size))
            saved = plans.save_plan(data)
            self.send(200, json.dumps(saved, ensure_ascii=False).encode(), "application/json; charset=utf-8")
        except (ValueError, TypeError, KeyError) as exc:
            self.send(400, json.dumps({"error": str(exc)}).encode(), "application/json")

    def log_message(self, fmt, *args):
        print(fmt % args, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"UCSB macros: http://127.0.0.1:{args.port}", flush=True)
    print("Menus cache for 15 minutes and refresh in the background. Ctrl+C stops the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
