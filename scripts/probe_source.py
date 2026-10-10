"""Small anonymous NetNutrition probe for a GitHub-hosted runner."""
import json
import time

from ucsb_macros.app import NetNutrition, parse_units, parse_menus, parse_items, parse_label


def main():
    started = time.monotonic()
    client = NetNutrition()
    try:
        hall = next(h for h in parse_units(client.landing, "unitsSelectUnit") if "Portola" in h["name"])
        panels = client.select(hall["id"], hall["id"])
        section = next(s for s in parse_units(panels["childUnitsPanel"], "childUnitsSelectUnit") if "Daily" in s["name"])
        panels = client.select(hall["id"], section["id"])
        menus = parse_menus(panels["menuPanel"])
        meal = next((m for m in menus if m["meal"] == "Lunch"), menus[0])
        panels = client.panels("/Menu/SelectMenu", {"menuOid": meal["id"]})
        items = parse_items(panels["itemPanel"])
        raw = client.request("POST", "/NutritionDetail/ShowItemNutritionLabel", {"detailOid": items[0]["id"]})
        label = parse_label(raw)
        assert label["label_name"] and label["calories"] is not None
        print(json.dumps({"ok": True, "hall": hall["name"], "meal": meal, "items": len(items),
                          "label": label, "seconds": round(time.monotonic() - started, 2)}))
    finally:
        client.close()


if __name__ == "__main__":
    main()
