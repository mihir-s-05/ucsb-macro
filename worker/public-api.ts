import { ApiError, readResource } from "./data";

type Named = { id: number; name: string };
type Hall = Named & { sections: Named[] };
type MenuChoice = { id: number; date: string; meal: string; date_label: string };
type Item = Named & { serving: string; calories: number | null; protein_g: number | null;
  carbs_g: number | null; fat_g: number | null; [key: string]: unknown };
type Menu = MenuChoice & { hall: string; section: string; items: Item[]; source: string; fetched_at: string };

function parameter(query: URLSearchParams, name: string, required = false): string {
  const value = (query.get(name) ?? "").trim();
  if ((required && !value) || value.length > 300) throw new ApiError(400, `Provide a valid ${name} (1–300 characters).`);
  return value;
}

function choose<T extends Named>(entries: T[], query: string, kind: string): T {
  const lower = query.toLowerCase();
  const exact = entries.filter(e => String(e.id) === query || e.name.toLowerCase() === lower);
  const matches = exact.length ? exact : entries.filter(e => e.name.toLowerCase().includes(lower));
  if (!matches.length) throw new ApiError(404, `${kind} '${query}' was not found.`, entries.map(({ id, name }) => ({ id, name })));
  if (matches.length > 1) throw new ApiError(409, `${kind} '${query}' is ambiguous. Use an exact name or ID.`, matches.map(({ id, name }) => ({ id, name })));
  return matches[0];
}

function date(value: string): string {
  const parsed = new Date(`${value}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(parsed.valueOf()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new ApiError(400, "Use a valid date in YYYY-MM-DD format.");
  }
  return value;
}

function today(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Los_Angeles", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

export async function publicApi(url: URL, env: Env): Promise<object> {
  const route = url.pathname.slice("/api/v1/".length);
  if (!["halls", "menus", "menu", "item", "label"].includes(route)) throw new ApiError(404, "Not found.");
  const q = url.searchParams;
  const catalog = await readResource<{ halls: Hall[] }>(env, "catalog");
  if (route === "halls") return catalog;
  const hall = choose(catalog.halls, parameter(q, "hall", true), "Dining location");
  const sectionQuery = parameter(q, "section");
  const section = sectionQuery ? choose(hall.sections, sectionQuery, "Section")
    : hall.sections.find(s => s.name.toLowerCase().includes("daily menu")) ?? hall.sections[0];
  if (!section) throw new ApiError(404, "This location has no published sections.");
  const snapshot = catalog.snapshot.id;
  const options = await readResource<{ hall: string; section: string; menus: MenuChoice[] }>(env, `options:${hall.id}:${section.id}`, snapshot);
  const requestedDate = parameter(q, "date");
  if (requestedDate) date(requestedDate);
  if (route === "menus") {
    return { ...options, hall_id: hall.id, section_id: section.id,
      menus: requestedDate ? options.menus.filter(m => m.date === "everyday" || m.date === requestedDate) : options.menus };
  }
  const menuId = parameter(q, "menu");
  if (menuId && !/^\d+$/.test(menuId)) throw new ApiError(400, "Use a numeric menu ID.");
  const meal = parameter(q, "meal") || "Lunch";
  const selected = options.menus.filter(m => menuId ? String(m.id) === menuId
    : m.date === "everyday" || (m.date === (requestedDate || today()) && m.meal.toLowerCase() === meal.toLowerCase()));
  if (selected.length !== 1) throw new ApiError(404, "That date/meal is unavailable. GET /api/v1/menus lists published choices.");
  const prefix = `${hall.id}:${section.id}:${selected[0].id}`;
  const menu = await readResource<Menu>(env, `menu:${prefix}`, snapshot);
  if (route === "menu") {
    const search = parameter(q, "search").toLowerCase();
    return { ...menu, hall_id: hall.id, section_id: section.id,
      items: menu.items.filter(i => i.name.toLowerCase().includes(search)) };
  }
  const item = choose(menu.items, parameter(q, "item", true), "Food");
  const result = { hall: menu.hall, hall_id: hall.id, section: menu.section, section_id: section.id,
    menu_id: menu.id, date: menu.date, meal: menu.meal, source: menu.source, fetched_at: menu.fetched_at,
    item, snapshot: menu.snapshot };
  if (route === "item") return result;
  const label = await readResource<{ name: string }>(env, `label:${prefix}:${item.id}`, snapshot);
  return { ...result, name: label.name, document: label.document };
}
