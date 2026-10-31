import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// evaluate in this realm (not a vm context) so arrays/objects compare with deepEqual
new Function(readFileSync(new URL('../../web/filter.js', import.meta.url), 'utf8'))();
new Function(readFileSync(new URL('../../web/trades.js', import.meta.url), 'utf8'))();
new Function(readFileSync(new URL('../../web/notify.js', import.meta.url), 'utf8'))();
const g = globalThis;
const N = g.ScanNotify;
const al = (ts, scan, symbol, extra = {}) => ({ ts: `2026-10-19T${ts}:00+05:30`, scan, symbol, price: 100, side: 'long', ...extra });

test('first load seeds without notifying', () => {
  const seen = new Set();
  const fresh = N.newAlerts(seen, [al('10:05', 's', 'X'), al('10:06', 's', 'Y')], { seed: true });
  assert.deepEqual(fresh, []);
  assert.equal(seen.size, 2);
});

test('only unseen alerts are returned, newest first, and get marked seen', () => {
  const seen = new Set();
  N.newAlerts(seen, [al('10:05', 's', 'X')], { seed: true });
  const fresh = N.newAlerts(seen, [al('10:07', 's', 'Z'), al('10:06', 't', 'Y'), al('10:05', 's', 'X')]);
  assert.deepEqual(fresh.map(a => a.symbol), ['Z', 'Y']);
  assert.deepEqual(N.newAlerts(seen, [al('10:07', 's', 'Z')]), []);
});

test('new picks: a symbol entering the top 3 of either side, once', () => {
  const seen = new Set();
  const snap1 = { bulls: [{ symbol: 'A', picked: true }, { symbol: 'B', picked: false }], bears: [{ symbol: 'C', picked: true }] };
  assert.deepEqual(N.newPicks(seen, snap1, { seed: true }), []);
  const snap2 = { bulls: [{ symbol: 'A', picked: true }, { symbol: 'B', picked: true }], bears: [{ symbol: 'D', picked: true }] };
  assert.deepEqual(N.newPicks(seen, snap2).map(p => [p.symbol, p.side]), [['B', 'bull'], ['D', 'bear']]);
  assert.deepEqual(N.newPicks(seen, snap2), []);
});

test('message text for an alert and a pick', () => {
  const m = N.alertMessage(al('10:05', 'Bounce failure short', 'LODHA', { price: 1056, side: 'short' }));
  assert.equal(m.title, 'ScanRadar · Bounce failure short');
  assert.equal(m.body, 'LODHA  SELL  ₹1,056.00  ·  10:05');
  const p = N.pickMessage({ symbol: 'TECHM', side: 'bull', pct: 6.9, oi_label: 'LONG BUILD-UP' }, '10:12');
  assert.equal(p.title, 'ScanRadar · new pick');
  assert.equal(p.body, 'TECHM  BULL  +6.90%  ·  LONG BUILD-UP  ·  10:12');
});

test('alert and pattern messages carry the replay odds when present', () => {
  const odds = { mc: { target_pct: 39.4, stop_pct: 41.5 }, verdict: 'coin' };
  const m = N.alertMessage(al('10:05', 'B0101', 'KFINTECH', { price: 854.6, odds }));
  assert.equal(m.body, 'KFINTECH  BUY  ₹854.60  ·  10:05  ·  odds 39/42');
  assert.ok(!N.alertMessage(al('10:05', 'B0101', 'KFINTECH', { odds: null })).body.includes('odds'));
  const r = { symbol: 'LT', side: 'short', entry: 3793.7, triggered_at: '2026-10-28T13:25:00+05:30', odds };
  const p = N.patternMessage(r, 'Triple top');
  assert.equal(p.title, 'ScanRadar · Triple top');
  assert.equal(p.body, 'LT  SELL  ₹3,793.70  ·  13:25  ·  odds 39/42');
  assert.equal(p.at, '13:25');
  assert.equal(N.patternMessage({ ...r, odds: null }, 'Triple top').body, 'LT  SELL  ₹3,793.70  ·  13:25');
});
