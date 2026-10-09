import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup

from ucsb_macros import app, plans
from ucsb_macros.cli import prep_rows
from ucsb_macros.labels import label_document

FIXTURES = Path(__file__).parent / "fixtures"


class NetNutritionParsing(unittest.TestCase):
    def test_label_preserves_source_serving_and_macro_values(self):
        data = app.parse_label((FIXTURES / "chicken-label.html").read_text(encoding="utf-8"))
        self.assertEqual(data["label_name"], "Grilled Achiote Chicken Thigh")
        self.assertEqual(data["serving"], "Piece (118g)")
        self.assertEqual([data[k] for k in plans.MACROS], [200, 24, 2, 9])

    def test_menu_discovers_item_ids_and_station(self):
        items = app.parse_items((FIXTURES / "menu-items.html").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["id"], 35440061)
        self.assertEqual(items[0]["menu_serving"], "1/2 Cup")
        self.assertEqual(items[0]["category"], "Greens & Grains")

    def test_dates_and_meals_come_from_available_menu_links(self):
        menus = app.parse_menus((FIXTURES / "menu-dates.html").read_text(encoding="utf-8"))
        lunch = next(m for m in menus if m["date"] == "2026-10-08" and m["meal"] == "Lunch")
        self.assertEqual(lunch["id"], 258591)
        self.assertTrue(any(m["meal"] == "Brunch" for m in menus))

    def test_missing_label_does_not_become_zero_macros(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            app.parse_label("<html>Session expired</html>")
        label = (FIXTURES / "chicken-label.html").read_text(encoding="utf-8").replace("&nbsp;24g", "&nbsp;&lt;1g")
        self.assertIsNone(app.parse_label(label)["protein_g"])


class FullNutritionLabel(unittest.TestCase):
    def setUp(self):
        self.raw = (FIXTURES / "chicken-label.html").read_text(encoding="utf-8")
        self.landing = '<link rel="stylesheet" href="Stylesheets/Nutrition_Label_FDA2018.css?t=123">'

    def test_original_label_content_and_styles_are_preserved(self):
        result = label_document(self.raw, self.landing)
        output = BeautifulSoup(result["document"], "html.parser")
        original = BeautifulSoup(self.raw, "html.parser")
        self.assertEqual(result["name"], "Grilled Achiote Chicken Thigh")
        self.assertEqual(output.select_one("#nutritionLabel").get_text(), original.select_one("#nutritionLabel").get_text())
        self.assertEqual(output.select_one(".cbo_nn_LabelIngredients").get_text(), original.select_one(".cbo_nn_LabelIngredients").get_text())
        self.assertEqual(output.select_one("link")["href"], "https://nutrition.info.dining.ucsb.edu/NetNutrition/Stylesheets/Nutrition_Label_FDA2018.css?t=123")
        self.assertEqual(output.select_one(".cbo_nn_LargeLabelHeader").get_text(), "Nutrition Information")

    def test_label_cannot_run_upstream_scripts_or_embed_other_pages(self):
        raw = self.raw.replace("<div id='nutritionLabel'>", "<div id='nutritionLabel' onclick='bad()'>")
        raw = raw.replace("</div></div></div>", "<script>bad()</script><iframe src='https://example.com'><div>bad</div></iframe><img src=x onerror='bad()'></div></div></div>")
        landing = self.landing + '<link rel="stylesheet" href="https://example.com/bad.css">'
        output = BeautifulSoup(label_document(raw, landing)["document"], "html.parser")
        self.assertFalse(output.select("script,iframe,img,[onclick],[onerror]"))
        self.assertEqual(len(output.select("link")), 1)
        self.assertIn("script-src 'none'", output.select_one('meta[http-equiv="Content-Security-Policy"]')["content"])

    def test_missing_label_is_reported_and_wrong_menu_item_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unavailable"):
            label_document("<html>Session expired</html>", self.landing)
        with patch.object(app, "menu", return_value={"items": [{"id": 123}]}), patch.object(app, "NetNutrition") as client:
            with self.assertRaisesRegex(ValueError, "not in the selected menu"):
                app.nutrition_label(15, 16, 42, 999)
            client.assert_not_called()


class LocalPlanning(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = plans.STORE
        plans.STORE = Path(self.temp.name) / "plans.json"
        self.item = {"id": 123, "name": "Chicken", "serving": "Piece", "servings": 1.5,
                     "calories": 200, "protein_g": 24, "carbs_g": 2, "fat_g": 9}

    def tearDown(self):
        plans.STORE = self.original
        self.temp.cleanup()

    def test_fractional_servings_and_unknown_totals(self):
        self.assertEqual(plans.totals([self.item]), {"calories": 300, "protein_g": 36, "carbs_g": 3, "fat_g": 13.5})
        missing = {**self.item, "protein_g": None}
        self.assertIsNone(plans.totals([self.item, missing])["protein_g"])

    def test_replace_same_plan_preserve_other_date_and_backup(self):
        data = {"name": "Lunch", "date": "2026-10-08", "meal": "Lunch", "items": [self.item]}
        plans.save_plan(data)
        plans.save_plan({**data, "date": "2026-10-09"})
        plans.save_plan({**data, "items": [{**self.item, "servings": 2}]})
        values = plans.list_plans()
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0]["totals"]["calories"], 400)
        self.assertTrue(plans.STORE.with_suffix(".json.bak").exists())

    def test_invalid_servings_rejected(self):
        for count in (-1, float("nan"), 101):
            with self.assertRaises(ValueError):
                plans.save_plan({"name": "Invalid", "items": [{**self.item, "servings": count}]})

    def test_prep_merges_portions_but_keeps_different_recipe_values(self):
        values = [{"items": [self.item, self.item, {**self.item, "calories": 220}]}]
        result = prep_rows(values)
        self.assertEqual(len(result), 2)
        original = next(i for i in result if i["calories"] == 200)
        self.assertEqual(original["servings"], 3)
        self.assertEqual(original["totals"]["protein_g"], 72)


if __name__ == "__main__":
    unittest.main()
