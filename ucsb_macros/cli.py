"""Command-line menus, macros, meal plans, and prep portions."""
import argparse
import csv
import io
import json
import sys
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from . import app, plans


def today():
    try:
        return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def choose(entries, query, kind):
    exact = [x for x in entries if str(x["id"]) == query or x["name"].casefold() == query.casefold()]
    matches = exact or [x for x in entries if query.casefold() in x["name"].casefold()]
    if len(matches) != 1:
        raise ValueError(f"{kind} '{query}' matched {len(matches)} choices. Available: " + ", ".join(x["name"] for x in entries))
    return matches[0]


def selection(args):
    hall = choose(app.catalog()["halls"], args.hall, "Location")
    section = choose(hall["sections"], args.section, "Section") if args.section else next(
        (s for s in hall["sections"] if "daily menu" in s["name"].lower()), hall["sections"][0])
    return hall, section, app.options(hall["id"], section["id"])


def get_menu(args):
    hall, section, options = selection(args)
    selected = [m for m in options["menus"] if m["date"] == "everyday" or (
        m["date"] == args.date and m["meal"].casefold() == args.meal.casefold())]
    if len(selected) != 1:
        raise ValueError("That date/meal is unavailable. Use the 'menus' command to see published choices.")
    return app.menu(hall["id"], section["id"], selected[0]["id"]), hall, section


def fmt(value):
    return "—" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def output(data, rows, headings, args):
    if args.format == "json":
        result = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    elif args.format == "csv":
        stream = io.StringIO(newline="")
        writer = csv.writer(stream)
        writer.writerow(headings)
        writer.writerows([["" if v is None else v for v in row] for row in rows])
        result = stream.getvalue()
    else:
        strings = [[fmt(v) for v in row] for row in rows]
        widths = [max(len(h), *(len(row[i]) for row in strings)) if strings else len(h) for i, h in enumerate(headings)]
        result = "  ".join(h.ljust(w) for h, w in zip(headings, widths)) + "\n"
        result += "  ".join("─" * w for w in widths) + "\n"
        result += "\n".join("  ".join(v.ljust(w) for v, w in zip(row, widths)) for row in strings) + "\n"
    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"Wrote {args.output}", file=sys.stderr)
    else:
        print(result, end="")


def menu_rows(data):
    return [[i["id"], i["name"], i.get("serving", ""), *(i.get(f) if i.get(f) is not None else i.get("macro_display", {}).get(f) for f in plans.MACROS)] for i in data["items"]]


def find_plan(args):
    values = [p for p in plans.list_plans() if p["name"].casefold() == args.name.casefold()
              and (not args.date or p["date"] == args.date)]
    if len(values) != 1:
        raise ValueError("Plan name matched zero or several plans. Use 'plan list' and specify --date if needed.")
    return values[0]


def prep_rows(values):
    groups = {}
    for plan in values:
        for item in plan["items"]:
            # Only merge foods with identical serving sizes and macro snapshots.
            key = (item["name"], item["serving"], *(item.get(f) for f in plans.MACROS))
            if key not in groups:
                groups[key] = {"food": item["name"], "serving": item["serving"], "servings": 0,
                               **{f: item.get(f) for f in plans.MACROS}}
            groups[key]["servings"] += item["servings"]
    result = []
    for item in groups.values():
        result.append({**item, "totals": plans.totals([item])})
    return sorted(result, key=lambda i: i["food"])


def run(args):
    if args.command == "serve":
        server = ThreadingHTTPServer(("127.0.0.1", args.port), app.Handler)
        print(f"UCSB macros: http://127.0.0.1:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
        return
    if args.command == "halls":
        data = app.catalog()
        output(data, [[h["id"], h["name"], "; ".join(s["name"] for s in h["sections"])] for h in data["halls"]],
               ["ID", "Location", "Sections"], args)
        return
    if args.command == "menus":
        _, _, data = selection(args)
        output(data, [[m["id"], m["date"], m["meal"]] for m in data["menus"]], ["Menu ID", "Date", "Meal"], args)
        return
    if args.command == "menu":
        data, _, _ = get_menu(args)
        data = {**data, "items": [i for i in data["items"] if args.search.casefold() in i["name"].casefold()]}
        if args.sort == "protein":
            data["items"].sort(key=lambda i: i.get("protein_g") if i.get("protein_g") is not None else -1, reverse=True)
        elif args.sort == "calories":
            data["items"].sort(key=lambda i: i.get("calories") if i.get("calories") is not None else float("inf"))
        elif args.sort == "name":
            data["items"].sort(key=lambda i: i["name"])
        output(data, menu_rows(data), ["ID", "Food", "Serving", "Calories", "Protein g", "Carbs g", "Fat g"], args)
        return
    values = plans.list_plans()
    if args.action == "list":
        output(values, [[p["name"], p["date"], p["meal"], *(p["totals"][f] for f in plans.MACROS)] for p in values],
               ["Plan", "Date", "Meal", "Calories", "Protein g", "Carbs g", "Fat g"], args)
    elif args.action == "show":
        plan = find_plan(args)
        output(plan, [[i["name"], i["serving"], i["servings"], *(None if i[f] is None else round(i[f]*i["servings"], 2) for f in plans.MACROS)] for i in plan["items"]]
               + [["TOTAL", "", "", *(plan["totals"][f] for f in plans.MACROS)]],
               ["Food", "Serving", "Count", "Calories", "Protein g", "Carbs g", "Fat g"], args)
    elif args.action == "save":
        data, hall, section = get_menu(args)
        items = []
        for specification in args.item:
            try:
                query, amount = specification.rsplit("=", 1)
                servings = float(amount)
            except ValueError as exc:
                raise ValueError("Use --item 'Food name=servings' or --item 'Item ID=servings'.") from exc
            item = choose(data["items"], query.strip(), "Food")
            items.append({**item, "key": f"{hall['id']}:{section['id']}:{data['id']}:{item['id']}",
                          "servings": servings, "hall": data["hall"], "section": data["section"],
                          "meal": data["meal"], "source_date": data["date"], "fetched_at": data["fetched_at"]})
        by_key = {}
        for item in items:
            if item["key"] in by_key:
                by_key[item["key"]]["servings"] += item["servings"]
            else:
                by_key[item["key"]] = item.copy()
        saved = plans.save_plan({"name": args.name, "date": args.plan_date or args.date,
                                 "meal": args.label or data["meal"], "items": list(by_key.values())})
        output(saved, [[saved["name"], saved["date"], *(saved["totals"][f] for f in plans.MACROS)]],
               ["Saved plan", "Date", "Calories", "Protein g", "Carbs g", "Fat g"], args)
    elif args.action == "export":
        rows = [[p["name"], p["date"], p["meal"], i["name"], i["serving"], i["servings"],
                 *(None if i[f] is None else round(i[f]*i["servings"], 2) for f in plans.MACROS), i["hall"], i["source_date"]]
                for p in values for i in p["items"]]
        output(values, rows, ["Plan", "Date", "Meal", "Food", "Serving", "Count", "Calories", "Protein g", "Carbs g", "Fat g", "Source hall", "Source menu date"], args)
    elif args.action == "prep":
        values = [p for p in values if (not args.from_date or p["date"] >= args.from_date)
                  and (not args.to_date or p["date"] <= args.to_date)]
        data = prep_rows(values)
        output(data, [[i["food"], i["serving"], i["servings"], *(i["totals"][f] for f in plans.MACROS)] for i in data],
               ["Food to portion", "One serving", "Total servings", "Calories", "Protein g", "Carbs g", "Fat g"], args)


def common(parser):
    parser.add_argument("--format", choices=["table", "json", "csv"], default="table")
    parser.add_argument("--output", help="Save output to this file")


def select_args(parser, with_meal=False):
    parser.add_argument("--hall", required=True, help="Location name, substring, or numeric ID")
    parser.add_argument("--section", help="Section name, substring, or ID; defaults to Daily Menu")
    if with_meal:
        parser.add_argument("--date", default=today(), help="Menu date YYYY-MM-DD (default: today in California)")
        parser.add_argument("--meal", default="Lunch")


def main():
    parser = argparse.ArgumentParser(prog="gaucho", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    halls = commands.add_parser("halls", help="List dining locations and their sections"); common(halls)
    menus = commands.add_parser("menus", help="List currently published menus"); common(menus); select_args(menus)
    meal = commands.add_parser("menu", help="Fetch every item's macros for a menu"); common(meal); select_args(meal, True)
    meal.add_argument("--search", default="")
    meal.add_argument("--sort", choices=["station", "protein", "calories", "name"], default="station")
    serve = commands.add_parser("serve", help="Start the local web app")
    serve.add_argument("--port", type=int, default=8765)
    plan = commands.add_parser("plan", help="Save, inspect, export, and total prep portions")
    actions = plan.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list"); common(listing)
    show = actions.add_parser("show"); common(show); show.add_argument("name"); show.add_argument("--date")
    save = actions.add_parser("save"); common(save); select_args(save, True); save.add_argument("name")
    save.add_argument("--item", action="append", required=True, help="Food name or ID followed by =servings; repeat for several foods")
    save.add_argument("--plan-date", help="Date to plan the meal for; may differ from source menu date")
    save.add_argument("--label", help="Meal/prep label (default: source meal)")
    export = actions.add_parser("export"); common(export)
    prep = actions.add_parser("prep", help="Aggregate saved plans into a prep portion list"); common(prep)
    prep.add_argument("--from", dest="from_date"); prep.add_argument("--to", dest="to_date")
    args = parser.parse_args()
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        run(args)
    except (ValueError, requests.RequestException, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
