/* Meal plan JSON shared by the hosted browser and the local CLI. */
(function (root) {
  'use strict';
  const macros = ['calories', 'protein_g', 'carbs_g', 'fat_g'];
  const textFields = ['key', 'name', 'serving', 'hall', 'section', 'meal', 'source_date', 'fetched_at'];
  const storageKey = 'gaucho.plans.v1';

  function validate(plan) {
    if (!plan || typeof plan !== 'object' || Array.isArray(plan)) throw Error('Invalid meal plan.');
    const name = String(plan.name ?? '').trim();
    const date = String(plan.date ?? '');
    if (!name || name.length > 100) throw Error('Give the plan a name (1–100 characters).');
    if (date && (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !Number.isFinite(Date.parse(date + 'T00:00:00Z'))
      || new Date(date + 'T00:00:00Z').toISOString().slice(0, 10) !== date)) throw Error('Invalid plan date.');
    if (!Array.isArray(plan.items) || !plan.items.length || plan.items.length > 300) throw Error('A plan needs 1–300 foods.');
    const items = plan.items.map(item => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) throw Error('Invalid food record.');
      if (!Number.isSafeInteger(item.id)) throw Error('Invalid food ID.');
      if (typeof item.servings !== 'number' || !Number.isFinite(item.servings) || item.servings <= 0 || item.servings > 100)
        throw Error('Servings must be greater than 0 and at most 100.');
      const cleaned = Object.fromEntries(textFields.map(field => [field, String(item[field] ?? '').slice(0, 300)]));
      if (!cleaned.name) throw Error('Food names are required.');
      cleaned.id = item.id;
      cleaned.servings = item.servings;
      for (const field of macros) {
        const value = item[field] ?? null;
        if (value !== null && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) throw Error('Invalid macro value.');
        cleaned[field] = value;
      }
      return cleaned;
    });
    const totals = Object.fromEntries(macros.map(field => [field, items.some(item => item[field] === null) ? null :
      Number(items.reduce((sum, item) => sum + item[field] * item.servings, 0).toFixed(2))]));
    return { name, date, meal: String(plan.meal ?? '').slice(0, 80), items, totals,
      updated_at: typeof plan.updated_at === 'string' && Number.isFinite(Date.parse(plan.updated_at))
        ? plan.updated_at : new Date().toISOString() };
  }

  function validateMany(value) {
    const plans = Array.isArray(value) ? value : [value];
    if (!plans.length || plans.length > 500) throw Error('Import 1–500 meal plans at a time.');
    return plans.map(validate);
  }

  function merge(existing, incoming) {
    const key = plan => JSON.stringify([plan.name, plan.date, plan.meal]);
    const plans = new Map(existing.map(plan => [key(plan), plan]));
    for (const plan of incoming) plans.set(key(plan), plan);
    return [...plans.values()].sort((a, b) => a.date.localeCompare(b.date) || a.name.localeCompare(b.name));
  }

  function read() {
    const raw = root.localStorage.getItem(storageKey);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw Error('Stored meal plans must be an array.');
    return parsed.map(validate);
  }

  function store(incoming) {
    const validated = validateMany(incoming);
    const next = merge(read(), validated);
    root.localStorage.setItem(storageKey, JSON.stringify(next));
    return validated;
  }

  const api = { validate, validateMany, merge, read, store };
  root.GauchoPlans = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(globalThis);
