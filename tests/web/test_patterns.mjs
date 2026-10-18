import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
new Function(readFileSync(new URL('../../web/filter.js', import.meta.url), 'utf8'))();
new Function(readFileSync(new URL('../../web/patterns.js', import.meta.url), 'utf8'))();
const P = globalThis.ScanPatterns, F = globalThis.ScanFilter;

const row = (o = {}) => ({ symbol: 'X', kind: 'double_bottom', side: 'long', state: 'forming', score: 3, rr: 1.4,
  checks: [true, true, true, false, false, false], neckline: 100, entry: null, stop: 98, target: 102, age_min: 12,
  triggered_at: null, formed_at: '2026-10-16T10:00:00+05:30', ...o });

test('quick pills filter by side, state, OI check and R:R', () => {
  const rows = [row(), row({ kind: 'double_top', side: 'short', state: 'triggered', rr: 2.0, triggered_at: '2026-10-16T10:25:00+05:30', checks: [true, false, true, true, true, true] }),
                row({ checks: [true, false, true, true, true, true], state: 'triggered', rr: 1.6 })];
  assert.equal(P.filterRows(rows, 'all', F.defaults(), {}).length, 3);
  assert.equal(P.filterRows(rows, 'bottoms', F.defaults(), {}).length, 2);
  assert.equal(P.filterRows(rows, 'tops', F.defaults(), {}).length, 1);
  assert.equal(P.filterRows(rows, 'triggered', F.defaults(), {}).length, 2);
  assert.equal(P.filterRows(rows, 'oi', F.defaults(), {}).length, 1);        // checks[1] true
  assert.equal(P.filterRows(rows, 'rr15', F.defaults(), {}).length, 2);
});

test('the view filter (sector, price) also applies, price from entry or neckline', () => {
  const rows = [row({ symbol: 'A' }), row({ symbol: 'B', neckline: 2000 })];
  const sectors = { A: 'IT', B: 'BANK' };
  assert.deepEqual(P.filterRows(rows, 'all', { ...F.defaults(), sectors: ['IT'] }, sectors).map(r => r.symbol), ['A']);
  assert.deepEqual(P.filterRows(rows, 'all', { ...F.defaults(), pmin: 500 }, sectors).map(r => r.symbol), ['B']);
});

test('age labels and the late marker', () => {
  assert.equal(P.ageLabel(0), 'now');
  assert.equal(P.ageLabel(5), '5m');
  assert.equal(P.ageLabel(65), '1h 5m');
  assert.equal(P.ageLabel(3, '2026-10-16T14:50:00+05:30'), '3m · late');
  assert.equal(P.ageLabel(3, '2026-10-16T14:30:00+05:30'), '3m');
});

test('ordering: triggered newest first, then forming by score, failed last', () => {
  const rows = [row({ symbol: 'F', state: 'failed' }), row({ symbol: 'T1', state: 'triggered', triggered_at: '2026-10-16T10:00:00+05:30' }),
                row({ symbol: 'S2', score: 2 }), row({ symbol: 'S5', score: 5 }), row({ symbol: 'T2', state: 'triggered', triggered_at: '2026-10-16T10:30:00+05:30' })];
  assert.deepEqual(P.sortRows(rows).map(r => r.symbol), ['T2', 'T1', 'S5', 'S2', 'F']);
});

test('labels', () => {
  assert.equal(P.kindLabel('triple_top'), 'Triple top');
  assert.equal(P.actionLabel(row({ state: 'triggered', side: 'long' })), 'BUY NOW');
  assert.equal(P.actionLabel(row({ state: 'forming', side: 'short', neckline: 2412 })), 'SELL if 2,412.00 closes');
  assert.equal(P.actionLabel(row({ state: 'failed' })), '(failed)');
});
