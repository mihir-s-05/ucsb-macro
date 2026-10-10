const { test, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const plans = require('../ucsb_macros/plans.js');

const meal = () => ({ name: 'Lunch', date: '2026-10-09', meal: 'Lunch',
  items: [{ id: 9, name: 'Chicken', serving: '1 piece', servings: 2,
    calories: 200, protein_g: 24, carbs_g: null, fat_g: 9,
    hall: 'Portola', source_date: '2026-10-09' }], totals: { calories: 999 } });

beforeEach(() => {
  const store = new Map();
  globalThis.localStorage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
});

test('recalculates totals and preserves unknown macros in CLI-shaped JSON', () => {
  const saved = plans.store(meal())[0];
  assert.equal(saved.totals.calories, 400);
  assert.equal(saved.totals.carbs_g, null);
  assert.equal(saved.items[0].source_date, '2026-10-09');
  assert.equal(plans.read()[0].items[0].protein_g, 24);
});

test('a bad second imported plan leaves existing storage untouched', () => {
  plans.store(meal());
  const broken = meal(); broken.items[0].servings = -1;
  const other = meal(); other.name = 'Dinner';
  assert.throws(() => plans.store([other, broken]), /Servings/);
  assert.deepEqual(plans.read().map(plan => plan.name), ['Lunch']);
});

test('replaces the same named dated meal while retaining other meals', () => {
  plans.store(meal());
  const dinner = meal(); dinner.meal = 'Dinner'; plans.store(dinner);
  const replacement = meal(); replacement.items[0].servings = 3; plans.store(replacement);
  assert.equal(plans.read().length, 2);
  assert.equal(plans.read().find(plan => plan.meal === 'Lunch').totals.calories, 600);
});

test('rejects invalid dates, string macros, and nonfinite numbers', () => {
  const dated = meal(); dated.date = '2026-02-30'; assert.throws(() => plans.validate(dated), /date/);
  const strings = meal(); strings.items[0].calories = '200'; assert.throws(() => plans.validate(strings), /macro/);
  const infinite = meal(); infinite.items[0].protein_g = Infinity; assert.throws(() => plans.validate(infinite), /macro/);
});

test('storage quota failures are surfaced to the UI', () => {
  globalThis.localStorage.setItem = () => { throw Error('Storage quota exceeded'); };
  assert.throws(() => plans.store(meal()), /quota/);
});
