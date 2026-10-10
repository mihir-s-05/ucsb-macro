"""Shared local meal-plan storage and serving-based totals."""
import json
import math
import threading
from datetime import datetime, timezone
from .storage import data_dir

STORE = data_dir() / "plans.json"
MACROS = ("calories", "protein_g", "carbs_g", "fat_g")
_lock = threading.RLock()


def totals(items):
    result = {}
    for field in MACROS:
        result[field] = None if any(item.get(field) is None for item in items) else round(
            sum(item[field] * item["servings"] for item in items), 2)
    return result


def list_plans():
    with _lock:
        if not STORE.exists():
            return []
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return sorted(data, key=lambda p: (p.get("date", ""), p["name"]))


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("Invalid meal plan record.")
    name = str(data.get("name", "")).strip()
    if not 1 <= len(name) <= 100:
        raise ValueError("Give the plan a name (1–100 characters).")
    date = str(data.get("date", ""))
    if date:
        datetime.strptime(date, "%Y-%m-%d")
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 300:
        raise ValueError("Add at least one food to the plate (maximum 300).")
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid food record.")
        servings = float(item.get("servings", 0))
        if not math.isfinite(servings) or not 0 < servings <= 100:
            raise ValueError("Servings must be greater than 0 and at most 100.")
        entry = {k: str(item.get(k, ""))[:300] for k in ("key", "name", "serving", "hall", "section", "meal", "source_date", "fetched_at")}
        if not entry["name"]:
            raise ValueError("Food names are required.")
        entry["id"] = int(item["id"])
        entry["servings"] = servings
        for field in MACROS:
            value = item.get(field)
            if value is not None and (not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid macro value.")
            entry[field] = value
        cleaned.append(entry)
    return {"name": name, "date": date, "meal": str(data.get("meal", ""))[:80],
            "items": cleaned, "totals": totals(cleaned), "updated_at": datetime.now(timezone.utc).isoformat()}


def save_plan(data):
    return import_plans([data])[0]


def import_plans(data):
    incoming = data if isinstance(data, list) else [data]
    if not 1 <= len(incoming) <= 500:
        raise ValueError("Import 1–500 meal plans at a time.")
    # Validate the complete import before changing any stored plan.
    saved = [validate(plan) for plan in incoming]
    with _lock:
        current = list_plans()
        by_key = {(p["name"], p["date"], p["meal"]): p for p in current}
        for plan in saved:
            by_key[(plan["name"], plan["date"], plan["meal"])] = plan
        current = list(by_key.values())
        STORE.parent.mkdir(parents=True, exist_ok=True)
        if STORE.exists():
            STORE.with_suffix(".json.bak").write_bytes(STORE.read_bytes())
        temp = STORE.with_suffix(".json.tmp")
        temp.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(STORE)
    return saved
