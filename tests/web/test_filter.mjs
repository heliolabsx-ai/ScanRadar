import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// evaluate in this realm (not a vm context) so arrays/objects compare with deepEqual
new Function(readFileSync(new URL('../../web/filter.js', import.meta.url), 'utf8'))();
const g = globalThis;
const F = g.ScanFilter;
const canon = o => JSON.stringify(Object.fromEntries(Object.entries(o).sort()));   // objects come from the vm realm
const row = (o = {}) => ({ symbol: 'X', sector: 'IT', price: 1200, rvol: 2.1, oi_cls: 'lb', dir: 1, above_vwap: true, picked: false, rr: 1.8, ...o });

test('defaults pass everything and are inactive', () => {
  const f = F.defaults();
  assert.equal(F.isActive(f), false);
  assert.equal(F.pass(row(), f), true);
  assert.equal(F.pass(row({ sector: 'OTHERS', price: 9, rvol: 0.1, oi_cls: 'nt', above_vwap: false, rr: 0 }), f), true);
});

test('sector, price band, rvol and rr thresholds', () => {
  assert.equal(F.pass(row(), { ...F.defaults(), sectors: ['BANK'] }), false);
  assert.equal(F.pass(row(), { ...F.defaults(), sectors: ['BANK', 'IT'] }), true);
  assert.equal(F.pass(row({ price: 400 }), { ...F.defaults(), pmin: 500 }), false);
  assert.equal(F.pass(row({ price: 6000 }), { ...F.defaults(), pmax: 5000 }), false);
  assert.equal(F.pass(row({ rvol: 1.2 }), { ...F.defaults(), rvol: 1.5 }), false);
  assert.equal(F.pass(row({ rr: 1.2 }), { ...F.defaults(), rr: 1.5 }), false);
});

test('OI build-up must agree with the direction; VWAP alignment; picks only', () => {
  const f = { ...F.defaults(), oi: 'buildup' };
  assert.equal(F.pass(row({ dir: 1, oi_cls: 'lb' }), f), true);
  assert.equal(F.pass(row({ dir: 1, oi_cls: 'sb' }), f), false);
  assert.equal(F.pass(row({ dir: -1, oi_cls: 'sb' }), f), true);
  assert.equal(F.pass(row({ dir: -1, oi_cls: 'sc' }), f), false);
  const v = { ...F.defaults(), vwap: 'aligned' };
  assert.equal(F.pass(row({ dir: 1, above_vwap: false }), v), false);
  assert.equal(F.pass(row({ dir: -1, above_vwap: false }), v), true);
  assert.equal(F.pass(row({ picked: false }), { ...F.defaults(), picks: true }), false);
  assert.equal(F.pass(row({ picked: true }), { ...F.defaults(), picks: true }), true);
});

test('alerts: use the ranked row when present, else sector and price only', () => {
  const rows = { X: row({ rvol: 0.5 }) };
  const f = { ...F.defaults(), rvol: 1.5 };
  assert.equal(F.passAlert({ symbol: 'X', price: 1200 }, rows, { X: 'IT' }, f), false);
  assert.equal(F.passAlert({ symbol: 'Y', price: 1200 }, rows, { Y: 'IT' }, f), true);           // no row: rvol unknown, not applied
  assert.equal(F.passAlert({ symbol: 'Y', price: 1200 }, rows, { Y: 'IT' }, { ...F.defaults(), sectors: ['BANK'] }), false);
  assert.equal(F.passAlert({ symbol: 'Y', price: 100 }, rows, { Y: 'IT' }, { ...F.defaults(), pmin: 500 }), false);
});

test('serialisation round-trips and tolerates garbage', () => {
  const f = { ...F.defaults(), sectors: ['IT'], pmin: 500, picks: true };
  assert.equal(canon(F.load(F.save(f))), canon(f));
  assert.equal(canon(F.load('not json')), canon(F.defaults()));
  assert.equal(canon(F.load(null)), canon(F.defaults()));
  assert.equal(F.isActive(f), true);
});
