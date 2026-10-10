# UCSB macros

The `gaucho` CLI and a simple local web app for UCSB dining menus, nutrition, and meal planning. It fetches every item's individual nutrition label and presents calories, protein, carbs, fat, and serving size in one view.

No account or API key is needed. The adapter uses an anonymous session and the same NetNutrition endpoints as the dining website. This is an unofficial integration, so upstream changes can require parser updates.

## Quick start

To install `gaucho` as a global command with `uv`, run this from the source folder:

```sh
uv tool install .
gaucho --help
gaucho serve
```

The command runs from any directory. After changing the source, rebuild and reinstall it with `uv tool install --force --reinstall-package ucsb-macros .`.

Python 3.10 or newer. A virtual environment is recommended. In Windows PowerShell:

```sh
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\gaucho.exe serve
```

Open **http://127.0.0.1:8765**. The server listens only on your machine. Stop it with Ctrl+C. An alternate port is available with `--port 8766`.

On macOS/Linux, activate with `source .venv/bin/activate`, then run `python -m pip install -e .` and `gaucho serve`. You can also activate the Windows environment with `.\.venv\Scripts\Activate.ps1` if your PowerShell policy allows it, then use the shorter `gaucho` commands below.

To run straight from the source folder without installing the CLI entry point:

```sh
python -m pip install -r requirements.txt
python cli.py serve
```

On Windows you can also double-click `start.cmd` after installing dependencies.

## Web app

- Select a dining location, section, date, and meal. Daily menus, salad bars, condiments, beverages, and other available sections are discovered from UCSB.
- Every food appears with calories, protein, carbs, fat, and its listed serving. Search foods or sort by station, protein, calories, or name.
- Click a food name to open its full NetNutrition label, with the original styling, nutrient values, and ingredients. Close it with the × button or Escape.
- Enter serving counts to build a plate. Counts support fractions and prep batches. The plate stays with you while switching menus.
- Name the plan, choose a planned date and meal/prep label, and save it locally. Saving the same name, date, and label updates that plan.
- Load saved plans and export menus or plans to CSV.
- Choose a date range in **Prep portions** to combine serving counts across saved meals, and export that portion list to CSV.

The date on a saved plan is your planning date. Each food separately retains its source menu date, dining hall, and fetch time. Planning a food for a future date does not mean UCSB will serve it on that date.

## CLI

Run `gaucho --help` or `gaucho plan --help` for commands. `python cli.py` and `python -m ucsb_macros` accept the same arguments.

```sh
# List locations and sections, including eateries
gaucho halls

# List the dates and meals currently published for Portola
gaucho menus --hall Portola

# Today's lunch, sorted by protein
gaucho menu --hall Portola --meal Lunch --sort protein

# A specific source menu; use a date shown by the menus command
gaucho menu --hall Carrillo --date 2026-10-08 --meal Dinner

# Everyday items in a section
gaucho menu --hall Portola --section "Salad Bar"

# Search, or export structured data
gaucho menu --hall Portola --search chicken
gaucho menu --hall "De La Guerra" --format json --output menu.json
gaucho menu --hall Portola --format csv --output menu.csv

# Save a plate using unique food names (partial names also work) or item IDs
gaucho plan save "Chicken lunch" --hall Portola --date 2026-10-08 --meal Lunch --item "Grilled Achiote Chicken Thigh=1" --item "Basmati Rice=2"

# A prep batch: source menu date and planned date can differ
gaucho plan save "Lunch prep" --hall Portola --date 2026-10-08 --meal Lunch --plan-date 2026-10-09 --label "Batch prep" --item "Grilled Achiote Chicken Thigh=4" --item "Basmati Rice=4"

gaucho plan list
gaucho plan show "Chicken lunch"
gaucho plan prep --from 2026-10-08 --to 2026-10-14
gaucho plan export --format csv --output week.csv
```

`plan prep` adds up serving counts across saved plans into a food/portion list. Foods merge only when their names, serving sizes, and macro snapshots match. It describes prepared-food portions; it is not an ingredient shopping list. Names and numeric IDs are discovered from current menus; IDs in old exports may no longer be valid. If a name matches several entries, the CLI asks you to specify it more precisely through an error with the available choices.

The default menu date is today in California and the default meal is lunch. Use `menus` first when lunch is unavailable, such as a brunch day. Table, JSON, and CSV output are supported. Missing or less-than nutrition values are represented as unknown, never silently converted to zero. Plan totals are unknown for any macro that is missing from a selected food.

## Local API

The web app runs against a small adapter that returns normalized JSON:

```text
GET  /api/catalog
GET  /api/options?hall=15&section=16
GET  /api/menu?hall=15&section=16&menu=<discovered menu ID>
GET  /api/label?hall=15&section=16&menu=<menu ID>&item=<item ID>
GET  /api/plans
POST /api/plans
```

Use IDs from `catalog` and `options`; do not hardcode menu or item IDs. `POST /api/plans` stores a plan with `name`, `date`, `meal`, and an `items` array. Each item includes its name, listed serving, serving count, per-serving macros, and source metadata. The Python module `ucsb_macros.app` exposes the same catalog, options, and menu functions for other tools.

`/api/label` returns the food's name and a `document` containing NetNutrition's full label HTML and links to its stylesheets. The web app displays it in an isolated frame without upstream scripts. Labels are cached alongside menus.

## Storage and hosting

Saved plans, their backup, and menu caches live in a per-user data folder: `%LOCALAPPDATA%\Gaucho` on Windows, `~/Library/Application Support/Gaucho` on macOS, and `$XDG_DATA_HOME/gaucho` (or `~/.local/share/gaucho`) on Linux. They remain separate from the installed tool. The browser and CLI share this location. Set `GAUCHO_DATA_DIR` to choose another folder; the earlier `UCSB_MACROS_DATA_DIR` setting also works.

Menus and labels cache for 15 minutes, including across CLI invocations. The web app shows cached snapshots up to a day past expiry immediately and refreshes them in the background; the status shows the saved time while refreshing. The CLI waits for fresh data after expiry. Dining locations and sections cache for 24 hours.

Fresh menu loads use up to four separate sessions to fetch labels concurrently. Each session selects its own hall and menu and makes requests sequentially, so NetNutrition's selection state stays separate. Requests for the same data share a fetch, and other cached requests remain responsive. The full label popup reuses the labels downloaded for the menu. Cached data includes a `fetched_at` timestamp; saved plans retain the values from when they were created and do not refresh automatically.

The local server shares saved plans with the CLI. The hosted app at **https://gaucho.mihir-s.com** uses browser storage for personal meal plans. It has no public plan-writing endpoint. Plans stay in that browser until exported; clearing browser data removes them.

## Hosted menus

The TypeScript Worker serves the existing interface through its static assets binding and reads menus and full labels from a dedicated Cloudflare D1 database. It never scrapes UCSB in response to a visitor. The API retains the catalog, options, menu, and label paths above, plus `GET /api/health` for snapshot age. Successful API responses cache for 60 seconds.

The **Refresh menus** GitHub workflow collects today and the next six days wherever published, plus the everyday sections. It runs at minute 17 every six hours UTC and can also be started manually. It uses at most four concurrent upstream requests and a user agent identifying this repository. A runner cache reuses future/everyday labels for up to a day; today's labels refresh each run. Every item includes its label fetch time. Identical label documents are stored once within each snapshot.

Each run writes a new snapshot ID. Small SQL batches through the D1 API keep the database available while staging the data. A guarded, single SQL statement updates `current_snapshot` only after all expected rows exist. API requests join that pointer and their payload in one statement. Older snapshots are pruned after publication. Any collection or upload failure leaves the published snapshot intact.

The site shows **Menus updated … ago** and **Refresh overdue** after 12 hours. GitHub can disable scheduled workflows after 60 days without repository activity; check `/api/health` and re-enable the workflow if needed. This version uses that visible staleness signal rather than adding a second scheduler with another credential.

GitHub uses a dedicated **D1 Edit** API token scoped to the Cloudflare account, stored as the repository secret `CLOUDFLARE_API_TOKEN`. The account and database IDs in the workflow are not secrets. The refresh job does not use a developer's Wrangler OAuth login.

For Worker development and deployment:

```sh
npm ci
npm run types
npm run typecheck
npm run test
npm run dev
# Seed the local D1 database before inspecting menus in wrangler dev.
npx wrangler d1 migrations apply gaucho --local
python -m ucsb_macros.collector collect --output .local/refresh
npx wrangler d1 execute gaucho --local --file .local/refresh/stage.sql --yes
npx wrangler d1 execute gaucho --local --file .local/refresh/publish.sql --yes
npm run deploy
```

`npm run build` marks the hosted interface to use browser plans. The Python package serves the same source interface with local plan storage. D1 schema migrations live in `migrations`; generated assets, caches, and local credentials are ignored by Git.

## Transfer meal plans

Use **Export plans JSON** and **Import plans JSON** in the website. The file is an array of plans in the same format as the CLI, including per-serving macro values, serving counts, source dates, and unknown values. Imports validate the entire file before saving and replace a plan only when its name, date, and meal match.

```sh
gaucho plan import gaucho-plans.json
gaucho plan export --format json --output gaucho-plans.json
```

CSV exports remain available for viewing in spreadsheets. Browser plans and CLI plans do not sync automatically.

The source is at [mihir-s-05/ucsb-macro](https://github.com/mihir-s-05/ucsb-macro).

## How the upstream integration works

Base: <https://nutrition.info.dining.ucsb.edu/NetNutrition/1>

| Operation | Method and path relative to the base | Form body | Response |
|---|---|---|---|
| Initialize anonymous session and discover halls | `GET /` (the base URL) | — | HTML and session cookies |
| Select a dining hall/eatery | `POST /Unit/SelectUnitFromUnitsList` | `unitOid=15` | JSON with HTML panels |
| Select daily menu or a section | `POST /Unit/SelectUnitFromChildUnitsList` | `unitOid=16` | JSON with HTML panels |
| Select a meal's menu | `POST /Menu/SelectMenu` | `menuOid=<discovered ID>` | JSON with item HTML |
| Fetch one label | `POST /NutritionDetail/ShowItemNutritionLabel` | `detailOid=<discovered ID>` | Nutrition label HTML |
| Select comparison item | `POST /Menu/SelectItem` | `detailOid=<discovered ID>` | Session selection response |
| Fetch comparison grid | `POST /NutritionDetail/ShowMenuDetailNutritionGrid` | No body | HTML for selected items |

These endpoints were observed in Browser CDP and the site's own JavaScript, then replayed successfully with a new Python session. Cookies must be retained: a cookie-less initial fetch redirected repeatedly during investigation. The comparison endpoint depends on selected items in session state, so it is not a stateless bulk macro API.

The app uses the individual labels. During verification on October 8, 2026, Portola's Grilled Achiote Chicken Thigh label showed **200 calories / 24 g protein / 2 g carbs / 9 g fat per Piece (118 g)**. The comparison grid showed **198 calories** for the same food. This adapter preserves the individual-label calorie basis consistently rather than mixing the two.

## Checks

```sh
python -m unittest discover -s tests -v
```

Tests use saved structural fixtures and local plan storage in a temporary directory. They do not depend on UCSB being online.

Nutrition values come from [UCSB Campus Dining NetNutrition](https://nutrition.info.dining.ucsb.edu/NetNutrition/1). UCSB states that values are approximate and can change with recipes and portions. This tool is independent of UCSB.
