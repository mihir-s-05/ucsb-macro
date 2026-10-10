import { before, test } from 'node:test';
import assert from 'node:assert/strict';

// Run against a Worker with published D1 data, locally or after deployment.
const base = process.env.GAUCHO_API_URL || 'http://127.0.0.1:8787';
async function request(path, params = {}, expected = 200) {
  const url = new URL(path, base);
  for (const [name, value] of Object.entries(params)) url.searchParams.set(name, String(value));
  const response = await fetch(url);
  const data = await response.json();
  assert.equal(response.status, expected, `${url}: ${JSON.stringify(data).slice(0, 300)}`);
  assert.equal(response.headers.get('access-control-allow-origin'), '*');
  assert.match(response.headers.get('cache-control'), expected === 200 ? /max-age=60/ : /no-store/);
  return data;
}

let halls, menus, choice, menu, selection;
before(async () => {
  halls = await request('/api/v1/halls');
  menus = await request('/api/v1/menus', { hall: 'Portola' });
  choice = menus.menus.find(m => m.meal === 'Lunch');
  assert.ok(choice, 'The fixture must include a published Portola lunch.');
  selection = { hall: 'Portola', menu: choice.id };
  menu = await request('/api/v1/menu', selection);
  assert.ok(menu.items.length);
});

test('discovers the API and exposes published locations, meals, and freshness', async () => {
  const discovery = await request('/api');
  // Wrangler uses the configured custom-domain Host for local requests.
  assert.equal(new URL(discovery.base_url).pathname, '/api/v1');
  assert.equal(new URL(discovery.docs).pathname, '/docs');
  if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) {
    assert.equal(new URL(discovery.base_url).origin, new URL(base).origin);
  }
  assert.deepEqual(discovery.endpoints, ['halls', 'menus', 'menu', 'item', 'label', 'health']);
  assert.ok(halls.halls.some(h => h.name.includes('Carrillo')));
  assert.ok(halls.halls.some(h => h.name.includes('De La Guerra')));
  assert.ok(menus.menus.every(m => Number.isInteger(m.id) && m.date && m.meal));
  const health = await request('/api/v1/health');
  assert.equal(health.snapshot.id, halls.snapshot.id);
  assert.equal(health.ok, !health.snapshot.stale);
  assert.ok(Number.isInteger(health.snapshot.age_seconds));
  assert.ok(Number.isFinite(Date.parse(health.snapshot.published_at)));
  assert.equal(health.plan_storage, 'browser');
});

test('resolves names and IDs, selects dates/meals, filters foods, and retains existing endpoints', async () => {
  const byNames = await request('/api/v1/menu', { hall: 'PORTOLA', date: choice.date, meal: 'lunch' });
  const byIds = await request('/api/v1/menu', { hall: menus.hall_id, section: menus.section_id, menu: choice.id });
  const legacy = await request('/api/menu', { hall: menus.hall_id, section: menus.section_id, menu: choice.id });
  assert.deepEqual(byNames.items, byIds.items);
  assert.deepEqual(byIds.items, legacy.items);
  assert.equal(byNames.snapshot.id, legacy.snapshot.id);
  assert.equal(byNames.hall_id, menus.hall_id);
  const matchingDate = await request('/api/v1/menus', { hall: 'Portola', date: choice.date });
  assert.ok(matchingDate.menus.length);
  assert.ok(matchingDate.menus.every(m => m.date === choice.date));
  const query = menu.items[0].name.slice(0, 5).toUpperCase();
  const filtered = await request('/api/v1/menu', { ...selection, search: query });
  assert.deepEqual(filtered.items, menu.items.filter(i => i.name.toUpperCase().includes(query)));
  const overridden = await request('/api/v1/menu', { ...selection, date: '1900-01-01', meal: 'Unavailable' });
  assert.equal(overridden.id, choice.id);
});

test('serves one food and its original label with per-serving macros', async () => {
  const food = menu.items[0];
  const byId = await request('/api/v1/item', { ...selection, item: food.id });
  const byName = await request('/api/v1/item', { ...selection, item: food.name });
  assert.deepEqual(byId.item, food);
  assert.deepEqual(byName.item, food);
  for (const name of ['calories', 'protein_g', 'carbs_g', 'fat_g']) {
    assert.ok(food[name] === null || (typeof food[name] === 'number' && Number.isFinite(food[name])));
  }
  assert.equal(byId.menu_id, choice.id);
  const label = await request('/api/v1/label', { ...selection, item: food.id });
  const legacy = await request('/api/label', { hall: menus.hall_id, section: menus.section_id, menu: choice.id, item: food.id });
  assert.deepEqual(label.item, food);
  assert.equal(label.document, legacy.document);
  assert.match(label.document, /Nutrition (Facts|Information)/i);
  assert.match(label.document, /id="nutritionLabel"/);
  assert.doesNotMatch(label.document, /<script\b/i);
});

test('selects everyday sections independently of daily meal/date selection', async () => {
  const everyday = await request('/api/v1/menu', { hall: 'Carrillo', section: 'Salad Bar', date: '1900-01-01', meal: 'Dinner' });
  assert.equal(everyday.date, 'everyday');
  assert.equal(everyday.section, 'Salad Bar');
  assert.ok(everyday.items.length);
  const choices = await request('/api/v1/menus', { hall: 'Carrillo', section: 'Salad Bar' });
  assert.equal(choices.menus[0].id, everyday.id);
});

test('returns actionable errors for bad parameters and ambiguous names', async () => {
  await request('/api/v1/menu', {}, 400);
  await request('/api/v1/menu', { hall: 'Portola', date: '2026-02-30' }, 400);
  await request('/api/v1/menu', { hall: 'Portola', menu: 'invalid' }, 400);
  await request('/api/v1/menu', { hall: 'x'.repeat(301) }, 400);
  await request('/api/v1/item', selection, 400);
  await request('/api/v1/menu', { hall: 'Missing dining hall' }, 404);
  await request('/api/v1/menu', { hall: 'Portola', date: '1900-01-01' }, 404);
  await request('/api/v1/missing', {}, 404);
  await request('/api/plans', {}, 404);
  const ambiguous = await request('/api/v1/menu', { hall: 'Dining Commons' }, 409);
  assert.match(ambiguous.error, /ambiguous/);
  assert.ok(ambiguous.choices.length > 1);
  assert.ok(ambiguous.choices.every(c => Number.isInteger(c.id) && typeof c.name === 'string'));
});

test('allows cross-origin reads and preflight, supports HEAD, and rejects writes', async () => {
  const response = await fetch(new URL('/api/v1/halls', base), {
    method: 'OPTIONS', headers: { Origin: 'https://example.com', 'Access-Control-Request-Method': 'GET',
      'Access-Control-Request-Headers': 'content-type' },
  });
  assert.equal(response.status, 204);
  assert.equal(response.headers.get('access-control-allow-origin'), '*');
  assert.match(response.headers.get('access-control-allow-headers'), /Content-Type/i);
  assert.equal(response.headers.get('access-control-allow-credentials'), null);
  const head = await fetch(new URL('/api/v1/halls', base), { method: 'HEAD' });
  assert.equal(head.status, 200);
  assert.equal(await head.text(), '');
  assert.match(head.headers.get('content-type'), /application\/json/);
  for (const method of ['POST', 'PUT', 'DELETE']) {
    const denied = await fetch(new URL('/api/v1/menu', base), { method });
    assert.equal(denied.status, 405);
    assert.equal(denied.headers.get('allow'), 'GET, HEAD, OPTIONS');
    assert.match((await denied.json()).error, /read-only/);
  }
  const deniedPreflight = await fetch(new URL('/api/v1/menu', base), {
    method: 'OPTIONS', headers: { 'Access-Control-Request-Method': 'POST' },
  });
  assert.equal(deniedPreflight.status, 405);
});

test('publishes docs, the main-page link, and a consistent OpenAPI reference', async () => {
  const home = await fetch(new URL('/', base)).then(r => r.text());
  assert.match(home, /href="\/docs">API docs/);
  const docs = await fetch(new URL('/docs', base));
  assert.equal(docs.status, 200);
  assert.match(await docs.text(), /Gaucho REST API/);
  const response = await fetch(new URL('/api/openapi.json', base));
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('access-control-allow-origin'), '*');
  const spec = await response.json();
  assert.equal(spec.openapi, '3.1.0');
  assert.deepEqual(Object.keys(spec.paths), ['/halls', '/menus', '/menu', '/item', '/label', '/health']);
  assert.equal(spec.servers[0].url, 'https://gaucho.mihir-s.com/api/v1');
  function references(value) {
    if (!value || typeof value !== 'object') return;
    if (value.$ref) assert.ok(spec.components.schemas[value.$ref.split('/').at(-1)], value.$ref);
    for (const child of Object.values(value)) references(child);
  }
  references(spec);
  for (const route of ['/item', '/label']) {
    assert.ok(spec.paths[route].get.parameters.find(p => p.name === 'item').required);
  }
});
