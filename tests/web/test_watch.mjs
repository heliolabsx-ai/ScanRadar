import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/patterns.js'), 'utf8'));
(0, eval)(readFileSync(path.join(here, '../../web/filter.js'), 'utf8'));
(0, eval)(readFileSync(path.join(here, '../../web/trades.js'), 'utf8'));
(0, eval)(readFileSync(path.join(here, '../../web/watch.js'), 'utf8'));
const W = globalThis.ScanWatch;

test('list persistence falls back to the default', () => {
  assert.deepEqual(W.load(null), W.DEFAULT); assert.deepEqual(W.load('[]'), W.DEFAULT); assert.deepEqual(W.load('["A","B"]'), ['A', 'B']);
  assert.deepEqual(JSON.parse(W.save(['X'])), ['X']);
});
test('odometer flags only the digits that changed', () => {
  const d = W.digits(1756.4, 1756.9);
  assert.equal(d.map(x => x.ch).join(''), '1,756.90');
  assert.deepEqual(d.map(x => x.changed), [false, false, false, false, false, false, true, false]);
  assert.ok(W.digits(null, 12).every(x => x.changed));
});
test('heat saturates', () => { assert.equal(W.heat(0), 0); assert.equal(W.heat(-1.25), 0.5); assert.equal(W.heat(9), 0.55); });
test('order keeps its ranking until the window passes or the set changes', () => {
  const rows = [{ symbol: 'A', pct: 1 }, { symbol: 'B', pct: 2 }];
  let r = W.order(rows, 'pct', null, 10, 5); assert.deepEqual(r.rows.map(x => x.symbol), ['B', 'A']);
  rows[0].pct = 5;
  r = W.order(rows, 'pct', r.cache, 12, 5); assert.deepEqual(r.rows.map(x => x.symbol), ['B', 'A']);      // not yet
  r = W.order(rows, 'pct', r.cache, 15, 5); assert.deepEqual(r.rows.map(x => x.symbol), ['A', 'B']);      // window passed
  r = W.order(rows, 'rank', r.cache, 15, 5); assert.equal(r.cache.sortBy, 'rank');                       // sort change
  r = W.order([...rows, { symbol: 'C', pct: 9 }], 'rank', r.cache, 15, 5); assert.equal(r.cache.syms.length, 3);
});
test('events merge patterns and verdicts newest first', () => {
  const prows = [{ symbol: 'A', kind: 'double_bottom', state: 'triggered', triggered_at: '2026-10-19T10:45:00+05:30', formed_at: '2026-10-19T10:20:00+05:30', confidence: { hit_rate: 62 } },
    { symbol: 'B', kind: 'bull_flag', state: 'forming', formed_at: '2026-10-19T11:00:00+05:30' }];
  const vds = [{ symbol: 'A', ts: '2026-10-19T10:46:00+05:30', output: { call: 'take', confidence: 7 } }];
  const ev = W.events('A', prows, vds);
  assert.deepEqual(ev.map(e => e.k), ['take', 'trig']); assert.match(ev[1].t, /Double bottom · TRIGGERED 10:45 · conf 62%/);
  assert.equal(W.events('B', prows, vds)[0].k, 'form'); assert.deepEqual(W.events('Z', prows, vds), []);
});
test('delta share', () => {
  assert.equal(W.deltaShare({ delta_available: true, avg_vol_1m: 1000, delta: 30000 }), 75);
  assert.equal(W.deltaShare({ delta_available: true, avg_vol_1m: 1000, delta: -90000 }), 0);
  assert.equal(W.deltaShare({ delta_available: false, avg_vol_1m: 1000, delta: 5 }), null);
});
test('position and groups', () => {
  assert.equal(W.position({ prev_low: 90, prev_high: 110, prev_close: 100 }, 100), 50);
  const g = W.groups({ bulls: [{ symbol: 'A', picked: true }, { symbol: 'B', picked: false }], bears: [] }, [{ symbol: 'C', triggered_at: 'x' }], { A: 'IT', C: 'IT', D: 'BANK' });
  assert.deepEqual(g.picks, ['A']); assert.deepEqual(g.trig, ['C']); assert.deepEqual(g.bySector.IT, ['A', 'C']);
});

test('prev-day levels widen the chart range only when they are near the price', () => {
  const row = { prev_high: 101.8, prev_low: 98.2 };
  let r = W.levels(99, 101, 100, row);                      // both within 2 % of 100 -> range grows to hold them
  assert.equal(r.lo, 98.2); assert.equal(r.hi, 101.8);
  assert.deepEqual(r.lines, [{ k: 'PDH', v: 101.8, shown: true }, { k: 'PDL', v: 98.2, shown: true }]);
  r = W.levels(99, 101, 100, { prev_high: 110, prev_low: 90 }); // far away -> range untouched, lines reported as off-chart
  assert.equal(r.lo, 99); assert.equal(r.hi, 101);
  assert.deepEqual(r.lines.map(l => l.shown), [false, false]);
  r = W.levels(99, 101, 100, { prev_high: 100.5, prev_low: 90 }); // a level already inside the range is shown without widening
  assert.equal(r.hi, 101); assert.deepEqual(r.lines.map(l => [l.k, l.shown]), [['PDH', true], ['PDL', false]]);
  r = W.levels(99, 101, 100, { prev_close: 100 });            // no prev-day levels -> nothing to draw
  assert.deepEqual(r.lines, []); assert.equal(r.lo, 99);
});

test('a pattern chip carries the replay odds when the row has them', () => {
  const prows = [{ symbol: 'A', kind: 'double_bottom', state: 'triggered', triggered_at: '2026-10-19T10:45:00+05:30', formed_at: '2026-10-19T10:20:00+05:30',
    odds: { mc: { target_pct: 39.4, stop_pct: 41.5 }, verdict: 'coin' } }];
  assert.ok(W.events('A', prows, [])[0].t.endsWith(' · 39/42'));
  assert.ok(!W.events('A', [{ ...prows[0], odds: null }], [])[0].t.includes('/'));
});
