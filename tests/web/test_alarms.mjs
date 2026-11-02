import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/alarms.js'), 'utf8'));
const A = globalThis.ScanAlarms;

const COLS = ['pdh_brk', 'pat', 'pdl_fade'];
const R = (ref, col, ts) => ({ ref, col, ts, symbol: ref });

test('group: newest first, every column present', () => {
  const rows = [R('a', 'pat', '2026-11-02T09:40:00+05:30'), R('b', 'pat', '2026-11-02T10:05:00+05:30'), R('c', 'pdh_brk', '2026-11-02T09:20:00+05:30')];
  const g = A.group(rows, COLS);
  assert.deepEqual(Object.keys(g), COLS);
  assert.deepEqual(g.pat.map(r => r.ref), ['b', 'a']);
  assert.deepEqual(g.pdh_brk.map(r => r.ref), ['c']);
  assert.deepEqual(g.pdl_fade, []);
  assert.deepEqual(A.group([], COLS), { pdh_brk: [], pat: [], pdl_fade: [] });
});

test('unread: visible clears, hidden counts newer rows, stale keys dropped', () => {
  const rows = [R('a', 'pat', '2026-11-02T09:40:00+05:30'), R('b', 'pat', '2026-11-02T10:05:00+05:30'), R('c', 'pdh_brk', '2026-11-02T09:20:00+05:30')];
  const seen = { pat: '2026-11-02T09:40:00+05:30', gone: '2026-11-01T09:00:00+05:30' };
  const hidden = A.unread(seen, rows, COLS, false);
  assert.deepEqual(hidden.counts, { pdh_brk: 1, pat: 1, pdl_fade: 0 });
  assert.deepEqual(hidden.seen, { pat: '2026-11-02T09:40:00+05:30' });
  const vis = A.unread(seen, rows, COLS, true);
  assert.deepEqual(vis.counts, { pdh_brk: 0, pat: 0, pdl_fade: 0 });
  assert.deepEqual(vis.seen, { pdh_brk: '2026-11-02T09:20:00+05:30', pat: '2026-11-02T10:05:00+05:30' });
});

test('ladder: NOW inserted at its place', () => {
  const lv = [['S1', 90], ['R1', 110], ['PDH', 105], ['S2', 80]];
  assert.deepEqual(A.ladder(lv, 100).map(x => x.name), ['R1', 'PDH', 'NOW', 'S1', 'S2']);
  assert.deepEqual(A.ladder(lv, 100).find(x => x.now), { name: 'NOW', value: 100, now: true });
  assert.equal(A.ladder(lv, 120)[0].name, 'NOW');
  assert.equal(A.ladder(lv, 70).at(-1).name, 'NOW');
  assert.equal(A.ladder(lv, 100).filter(x => x.now).length, 1);
});

test('chartLevels: near, far, STOP always near', () => {
  // range 100..110 -> margin 5 : near for 95 < v < 115
  const lv = [['R1', 112], ['S2', 80], ['STOP', 60], ['TGT', 200], ['PDH', 150.5]];
  const out = A.chartLevels(lv, 100, 110, 150);
  assert.deepEqual(out.near, [['R1', 112], ['STOP', 60], ['TGT', 200], ['PDH', 150.5]]);
  assert.deepEqual(out.far, [['S2', 80]]);
  // within 1.2% of last is near even when outside the band
  assert.deepEqual(A.chartLevels([['X', 101]], 10, 11, 100).near, [['X', 101]]);
});

test('positionWords: three shapes', () => {
  const lad = A.ladder([['R1', 110], ['R2', 120], ['S2', 80], ['S1', 90]], 115);
  assert.deepEqual(A.positionWords(115, 112, 118, 95, lad), ['above VWAP', 'between R1 and R2', "inside yesterday's range"]);
  const above = A.ladder([['R1', 110], ['R2', 120], ['S2', 80]], 125);
  assert.deepEqual(A.positionWords(125, 130, 118, 95, above), ['below VWAP', 'above R2', 'above PDH']);
  const below = A.ladder([['R1', 110], ['R2', 120], ['S2', 80]], 70);
  assert.deepEqual(A.positionWords(70, 65, 118, 95, below), ['above VWAP', 'below S2', 'below PDL']);
});

test('positionWords: between any ladder pair, e.g. S1 and the pivot', () => {
  const lad = A.ladder([['R1', 110], ['R2', 120], ['P', 100], ['S1', 90], ['S2', 80]], 95);
  assert.deepEqual(A.positionWords(95, 97, 118, 85, lad), ['below VWAP', 'between S1 and P', "inside yesterday's range"]);
  const mixed = A.ladder([['R1', 110], ['R2', 120], ['PDH', 105], ['VWAP', 102], ['S2', 80]], 103);
  assert.deepEqual(A.positionWords(103, 102, 105, 85, mixed), ['above VWAP', 'between VWAP and PDH', "inside yesterday's range"]);
});

test('deliveryMark: five cases', () => {
  assert.deepEqual(A.deliveryMark({ delivery: { ok: true, sent_at: '2026-11-02T15:15:07+05:30' } }), { text: 'sent 15:15:07', cls: 'ok' });
  assert.deepEqual(A.deliveryMark({ delivery: { ok: false, error: 'not configured' } }), { text: 'not configured', cls: 'warn' });
  assert.deepEqual(A.deliveryMark({ delivery: { ok: false, error: 'HTTP 400' } }), { text: 'failed: HTTP 400', cls: 'bad' });
  assert.deepEqual(A.deliveryMark({ delivery: null, source: 'replay' }), { text: 'replay', cls: 'mut' });
  assert.deepEqual(A.deliveryMark({ delivery: null, source: 'alert' }), { text: 'not recorded', cls: 'mut' });
});

test('pastDots: result classes and titles', () => {
  assert.deepEqual(A.pastDots([['2026-10-23', 'target', 75], ['2026-10-22', 'stop', 12], ['2026-10-21', 'open', 60]]), [
    { cls: 'ok', title: '2026-10-23 target 75 min' },
    { cls: 'bad', title: '2026-10-22 stop 12 min' },
    { cls: 'mut', title: '2026-10-21 open 60 min' }]);
  assert.deepEqual(A.pastDots(null), []);
});

test('timelineRows: cut at the as-of minute and sorted', () => {
  const tl = [{ at: '10:05', kind: 'k', text: 'c' }, { at: '09:20', kind: 'k', text: 'a' }, { at: '09:40', kind: 'k', text: 'b' }];
  assert.deepEqual(A.timelineRows(tl, '09:40').map(r => r.text), ['a', 'b']);
  assert.deepEqual(A.timelineRows(null, '09:40'), []);
});

test('fmtTime: HH:MM from an IST iso string', () => {
  assert.equal(A.fmtTime('2026-11-02T15:15:00+05:30'), '15:15');
});
