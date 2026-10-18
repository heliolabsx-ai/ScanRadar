import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// evaluate in this realm (not a vm context) so arrays/objects compare with deepEqual
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
