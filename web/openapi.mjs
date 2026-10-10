const ref = name => ({ $ref: `#/components/schemas/${name}` });
const string = { type: 'string' };
const integer = { type: 'integer', minimum: 0 };
const macro = { type: ['number', 'null'], minimum: 0, description: 'Per listed serving. Null means unknown, including values reported as less than a number.' };
const object = (properties, required = Object.keys(properties)) => ({ type: 'object', properties, required });
const array = items => ({ type: 'array', items });
const snapshot = object({ id: string, collected_at: { type: 'string', format: 'date-time' },
  published_at: { type: 'string', format: 'date-time' }, age_seconds: integer, stale: { type: 'boolean' } });
const choice = object({ id: integer, meal: string, date: { type: 'string', description: 'YYYY-MM-DD or everyday' }, date_label: string });
const item = object({ id: integer, name: string, serving: string, calories: macro, protein_g: macro, carbs_g: macro, fat_g: macro,
  fiber_g: macro, sugar_g: macro, sodium_mg: macro,
  category: string, label_fetched_at: { type: 'string', format: 'date-time' },
  macro_display: { type: 'object', additionalProperties: string, description: 'Original values for display, such as <1.' } },
  ['id', 'name', 'serving', 'calories', 'protein_g', 'carbs_g', 'fat_g']);
const context = { hall: string, hall_id: integer, section: string, section_id: integer,
  date: { type: 'string', description: 'YYYY-MM-DD or everyday' }, meal: string, source: string,
  fetched_at: { type: 'string', format: 'date-time' }, snapshot: ref('Snapshot') };
const parameters = {
  hall: ['Dining location name, unique substring, or ID.', 'Portola'],
  section: ['Section name, unique substring, or ID. Defaults to the daily menu, or the first section for eateries.', 'Salad Bar'],
  date: ['Menu date in YYYY-MM-DD format. Defaults to today in California for daily menus. Everyday sections ignore the date when selecting a meal.', '2026-10-09'],
  meal: ['Meal name, case insensitive. Defaults to Lunch. Use menus to discover Brunch and other choices.', 'Lunch'],
  menu: ['Numeric menu ID from menus. Overrides date and meal selection.', '258592'],
  item: ['Food name, unique substring, or ID from a menu.', 'Char Grilled Rock Fish w/Lemon Thyme'],
  search: ['Case-insensitive substring filter on food names.', 'chicken'],
};
function operation(summary, operationId, schema, params = []) {
  return { summary, operationId,
    parameters: params.map(name => ({ name, in: 'query', required: name === 'hall' || name === 'item',
      description: parameters[name][0], schema: { type: 'string', maxLength: 300 }, example: parameters[name][1] })),
    responses: { '200': { description: 'Published data. Nutrition values are per serving.',
      content: { 'application/json': { schema: ref(schema) } } },
      ...Object.fromEntries([400, 404, 409, 503].map(status => [status, { description: {
        400: 'Invalid or missing query parameter.', 404: 'Location, section, menu, or food is unavailable.',
        409: 'Ambiguous name, or snapshot changed during the request. Use an exact name/ID or retry.',
        503: 'Data is temporarily unavailable.' }[status], content: { 'application/json': { schema: ref('Error') } } }])) } };
}
const selection = ['hall', 'section', 'date', 'meal', 'menu'];
export default {
  openapi: '3.1.0', info: { title: 'Gaucho REST API', version: '1.0.0',
    description: 'Public, read-only UCSB dining menus and nutrition from Gaucho’s published D1 snapshot. No API key. GET, HEAD and OPTIONS are supported, with cross-origin access. Refreshes every six hours; successful responses cache for 60 seconds. Saved plans remain in the browser and are not exposed through this API.' },
  servers: [{ url: 'https://gaucho.mihir-s.com/api/v1' }], security: [],
  externalDocs: { url: 'https://gaucho.mihir-s.com/docs', description: 'Examples and API reference' },
  paths: {
    '/halls': { get: operation('List dining locations and their sections', 'listHalls', 'Halls') },
    '/menus': { get: operation('List published dates and meals for a location/section', 'listMenus', 'Menus', ['hall', 'section', 'date']) },
    '/menu': { get: operation('Get a menu with serving sizes and macros for every food', 'getMenu', 'Menu', [...selection, 'search']) },
    '/item': { get: operation('Get one food’s serving size and macros', 'getItem', 'ItemResponse', [...selection, 'item']) },
    '/label': { get: operation('Get a food and its full NetNutrition label HTML', 'getLabel', 'Label', [...selection, 'item']) },
    '/health': { get: operation('Get snapshot age and freshness', 'getHealth', 'Health') },
  }, components: { schemas: {
    Snapshot: snapshot, MenuChoice: choice, Item: item,
    Halls: object({ halls: array(object({ id: integer, name: string, sections: array(object({ id: integer, name: string })) })), snapshot: ref('Snapshot') }),
    Menus: object({ hall: string, hall_id: integer, section: string, section_id: integer, menus: array(ref('MenuChoice')), snapshot: ref('Snapshot') }),
    Menu: object({ ...context, id: integer, date_label: string, items: array(ref('Item')) }),
    ItemResponse: object({ ...context, menu_id: integer, item: ref('Item') }),
    Label: { allOf: [ref('ItemResponse'), object({ name: string, document: { type: 'string', description: 'Full label HTML, with original styles and ingredients. Render in a sandboxed iframe.' } })] },
    Health: object({ ok: { type: 'boolean', description: 'False when the snapshot is older than 12 hours.' }, snapshot: ref('Snapshot'), plan_storage: { const: 'browser' } }),
    Error: object({ error: string, choices: array(object({ id: integer, name: string })) }, ['error']),
  } },
};
