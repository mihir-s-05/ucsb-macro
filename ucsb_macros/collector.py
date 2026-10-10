"""Collect a published week and publish it through one guarded snapshot pointer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import app
from .labels import DOCUMENT_VERSION


PUBLISH_SQL = """INSERT INTO current_snapshot(singleton, snapshot_id, published_at)
SELECT 1, id, ? FROM snapshots
WHERE id = ?
AND resource_count = (SELECT COUNT(*) FROM resources WHERE snapshot_id = snapshots.id)
AND label_count = (SELECT COUNT(*) FROM labels WHERE snapshot_id = snapshots.id)
AND NOT EXISTS (SELECT 1 FROM resources WHERE snapshot_id = snapshots.id AND NOT json_valid(payload))
ON CONFLICT(singleton) DO UPDATE SET snapshot_id = excluded.snapshot_id, published_at = excluded.published_at
WHERE (SELECT collected_at FROM snapshots WHERE id = excluded.snapshot_id)
   >= (SELECT collected_at FROM snapshots WHERE id = current_snapshot.snapshot_id)"""

PRUNE_SQL = """DELETE FROM snapshots WHERE id != (SELECT snapshot_id FROM current_snapshot WHERE singleton = 1)
AND collected_at < (SELECT collected_at FROM snapshots WHERE id = (SELECT snapshot_id FROM current_snapshot WHERE singleton = 1))"""


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def quote(value):
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def collect_menu(hall, section, selected, today):
    client = app.NetNutrition()
    try:
        panels = client.select(hall["id"], section["id"])
        if selected["id"]:
            panels = client.panels("/Menu/SelectMenu", {"menuOid": selected["id"]})
        items = app.parse_items(panels.get("itemPanel", ""))
        # Today's labels refresh each run; future/everyday labels refresh at least daily.
        ttl = 6 * 3600 - 60 if selected["date"] == today else 24 * 3600 - 60
        pending = []
        for item in items:
            key = ("label", DOCUMENT_VERSION, hall["id"], section["id"], selected["id"], item["id"])
            entry = app.cache_entry(key)
            if entry and 0 <= time.time() - entry[0] < ttl:
                item.update(app.parse_label(entry[1]["document"]))
            else:
                pending.append(item)
        workers = min(app.FETCH_WORKERS, len(pending))
        if workers:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(app.load_label_batch, pending[index::workers], hall["id"], section["id"],
                                       selected["id"], client if index == 0 else None, ttl)
                           for index in range(workers)]
                for future in futures:
                    future.result()
        failures = [i["name"] for i in items if i.get("error")]
        if failures:
            raise ValueError(f"Labels failed for {hall['name']} / {selected['meal']}: {', '.join(failures[:5])}")
        return {"hall": hall["name"], "section": section["name"], **selected, "items": items,
                "source": app.BASE, "fetched_at": datetime.now(timezone.utc).isoformat(),
                "calorie_basis": "Rounded individual nutrition labels"}
    finally:
        client.close()


def collect_week():
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    through = today + timedelta(days=6)
    resources, labels, used_cache_files = {}, {}, set()
    data = app.catalog()
    resources["catalog"] = (data, None)
    menu_count = item_count = 0
    for hall in data["halls"]:
        for section in hall["sections"]:
            choices = app.options(hall["id"], section["id"])
            selected = [m for m in choices["menus"] if m["date"] == "everyday"
                        or today.isoformat() <= m["date"] <= through.isoformat()]
            resources[f"options:{hall['id']}:{section['id']}"] = ({**choices, "menus": selected}, None)
            for meal in selected:
                menu = collect_menu(hall, section, meal, today.isoformat())
                prefix = f"{hall['id']}:{section['id']}:{meal['id']}"
                for item in menu["items"]:
                    cache_key = ("label", DOCUMENT_VERSION, hall["id"], section["id"], meal["id"], item["id"])
                    entry = app.cache_entry(cache_key)
                    if entry is None:
                        raise ValueError(f"Missing label for {item['name']}")
                    used_cache_files.add(app.cache_file(cache_key))
                    document = entry[1]["document"]
                    digest = hashlib.sha256(document.encode()).hexdigest()
                    labels[digest] = document
                    item["label_fetched_at"] = datetime.fromtimestamp(entry[0], timezone.utc).isoformat()
                    resources[f"label:{prefix}:{item['id']}"] = ({"name": entry[1]["name"]}, digest)
                resources[f"menu:{prefix}"] = (menu, None)
                menu_count += 1
                item_count += len(menu["items"])
                print(compact({"menu": prefix, "date": meal["date"], "items": len(menu["items"])}), flush=True)
    if not menu_count or not item_count:
        raise ValueError("The source returned no foods; the previous snapshot will remain published.")
    # Bound the runner cache to labels in this published window plus catalog/options.
    cache_dir = app.DATA_DIR / "cache"
    for file in cache_dir.glob("*.json"):
        if file not in used_cache_files and time.time() - file.stat().st_mtime > 86400:
            file.unlink()
    return resources, labels, {"from": today.isoformat(), "through": through.isoformat(),
                                "menus": menu_count, "items": item_count, "source_requests": app.source_requests}


def write_snapshot(output, resources, labels, summary, *, snapshot_id=None, collected_at=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    snapshot_id = snapshot_id or str(uuid.uuid4())
    collected_at = collected_at or datetime.now(timezone.utc).isoformat()
    manifest = {"id": snapshot_id, "collected_at": collected_at,
                "resource_count": len(resources), "label_count": len(labels), **summary}
    with (output / "stage.sql").open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"INSERT OR IGNORE INTO snapshots VALUES ({quote(snapshot_id)},{quote(collected_at)},{len(resources)},{len(labels)});\n")
        for digest, document in labels.items():
            stream.write(f"INSERT OR IGNORE INTO labels VALUES ({quote(snapshot_id)},{quote(digest)},{quote(document)});\n")
        for key, (payload, label_hash) in resources.items():
            stream.write(f"INSERT OR IGNORE INTO resources VALUES ({quote(snapshot_id)},{quote(key)},{quote(compact(payload))},{quote(label_hash)});\n")
    (output / "manifest.json").write_text(compact(manifest), encoding="utf-8")
    published_at = datetime.now(timezone.utc).isoformat()
    statement = PUBLISH_SQL.replace("?", quote(published_at), 1).replace("?", quote(snapshot_id), 1)
    (output / "publish.sql").write_text(statement + ";\n" + PRUNE_SQL + ";\nSELECT * FROM current_snapshot;\n", encoding="utf-8")
    return manifest


def d1_query(sql, params=None):
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    account, database = os.environ["CLOUDFLARE_ACCOUNT_ID"], os.environ["CLOUDFLARE_DATABASE_ID"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
    for attempt in range(4):
        response = requests.post(url, headers={"Authorization": "Bearer " + token},
                                 json={"sql": sql, "params": params or []}, timeout=60)
        if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        if not response.ok:
            try:
                errors = response.json().get("errors", [])
            except ValueError:
                errors = []
            raise ValueError(f"D1 HTTP {response.status_code}: " + compact(errors))
        result = response.json()
        if not result.get("success") or not result.get("result") or not all(r.get("success") for r in result["result"]):
            raise ValueError("D1 query failed: " + compact(result.get("errors", [])))
        return result["result"][0]
    raise ValueError("D1 did not respond.")


def sql_batches(path, max_bytes=90000, max_statements=20):
    """Keep labels containing semicolons/newlines intact and stay below D1 query limits."""
    statement, batch, size = "", [], 0
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            statement += line
            if not sqlite3.complete_statement(statement):
                continue
            length = len(statement.encode("utf-8"))
            if length > max_bytes:
                raise ValueError("A menu or label exceeds the D1 upload batch limit.")
            if batch and (size + length > max_bytes or len(batch) >= max_statements):
                yield "".join(batch)
                batch, size = [], 0
            batch.append(statement)
            size += length
            statement = ""
    if statement.strip():
        raise ValueError("Incomplete snapshot SQL.")
    if batch:
        yield "".join(batch)


def stage(path):
    # Query batches keep the database available; bulk import would pause visitor queries.
    count = 0
    for batch in sql_batches(path):
        d1_query(batch)
        count += 1
    print(compact({"uploaded_batches": count}), flush=True)


def publish(manifest):
    d1_query(PUBLISH_SQL, [datetime.now(timezone.utc).isoformat(), manifest["id"]])
    result = d1_query("SELECT snapshot_id FROM current_snapshot WHERE singleton = 1")
    if not result["results"] or result["results"][0]["snapshot_id"] != manifest["id"]:
        raise ValueError("Snapshot verification failed or a newer run was already published; no partial data was published.")
    d1_query(PRUNE_SQL)
    print(compact({"published": manifest}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    collector = commands.add_parser("collect")
    collector.add_argument("--output", default=".local/refresh")
    uploader = commands.add_parser("stage")
    uploader.add_argument("sql", type=Path)
    commands.add_parser("check", help="Verify D1 credentials before contacting UCSB")
    publisher = commands.add_parser("publish")
    publisher.add_argument("manifest", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        collected_at = datetime.now(timezone.utc).isoformat()
        resources, labels, summary = collect_week()
        print(compact(write_snapshot(args.output, resources, labels, summary, collected_at=collected_at)), flush=True)
    elif args.command == "stage":
        stage(args.sql)
    elif args.command == "check":
        d1_query("SELECT singleton FROM current_snapshot LIMIT 1")
        print("D1 credentials verified.", flush=True)
    else:
        publish(json.loads(args.manifest.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
