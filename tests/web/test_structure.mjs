import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/structure.js'), 'utf8'));
const X = globalThis.ScanStructure;

const bar = (day, hm, o, h, l, c, extra = {}) => ({ ts: `${day}T${hm}:00+05:30`, o, h, l, c, v: 10, oi: 5, ...extra });
const hm = m => { const t = 9 * 60 + 15 + m; return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };

test('resample keeps sessions apart, anchors at 09:15 and marks the forming bar', () => {
  const mins = [];
  for (let m = 0; m < 7; m++) mins.push(bar('2026-11-03', hm(m), 10 + m, 11 + m, 9 + m, 10.5 + m));
  for (let m = 0; m < 3; m++) mins.push(bar('2026-11-04', hm(m), 20, 21, 19, 20.5, m === 2 ? { stub: true } : {}));
  const b = X.resample(mins, 5);
  assert.equal(b.length, 3);
  assert.deepEqual(b.map(x => [x.day, x.slot, x.m, x.n, x.i]), [['2026-11-03', 0, 0, 5, 0], ['2026-11-03', 1, 5, 2, 1], ['2026-11-04', 0, 0, 3, 2]]);
  assert.equal(b[0].h, 15); assert.equal(b[0].l, 9); assert.equal(b[0].c, 14.5); assert.equal(b[0].v, 50);
  assert.equal(b[2].stub, true); assert.equal(b[1].stub, false);
});

// A zigzag on 5m bars: up, up, lower high, break of the last higher low (CHoCH), then a lower low (BOS down).
function zigzag() {
  const path = [100, 104, 108, 104, 100, 103, 106, 110, 114, 110, 106, 109, 112, 116, 120, 116, 112, 114, 116, 113, 110, 107, 104, 106, 108, 105, 102, 99, 96];
  return path.map((p, k) => ({ day: '2026-11-04', m: k * 5, slot: k, o: p, h: p + 1, l: p - 1, c: p, i: k, stub: false }));
}

test('structure finds the swings k bars late and names BOS / CHoCH', () => {
  const b = zigzag(), st = X.structure(b, 2);
  // swing high at bar 2 (109) confirmed at bar 4; swing low at bar 4 (99) confirmed at 6
  assert.deepEqual(st.sw.slice(0, 2).map(s => [s.t, s.i, s.px, s.at, s.tag]), [['H', 2, 109, 4, 'H'], ['L', 4, 99, 6, 'L']]);
  assert.deepEqual(st.ev.map(e => [e.kind, e.dir, e.from, e.i, e.px]), [
    ['BOS', 'up', 2, 7, 109],       // close 110 at bar 7 through the 109 swing high
    ['BOS', 'up', 8, 13, 115],      // close 116 through the 115 swing high (bar 8)
    ['CHoCH', 'down', 16, 20, 111], // close 110 at bar 20 below the higher low 111 (bar 16)
    ['BOS', 'down', 22, 26, 103],   // close 102 through the 103 swing low (bar 22)
  ]);
  assert.equal(st.trend, 'down');
  assert.deepEqual(st.sw.map(s => s.tag).slice(2), ['HH', 'HL', 'HH', 'HL', 'LH', 'LL', 'LH']);
  assert.equal(st.upLevel, 109); assert.equal(st.dnLevel, null);
});

test('structure: no event before a swing is confirmed, the forming bar neither swings nor breaks', () => {
  const b = zigzag().slice(0, 6);                  // bar 2 is the high but bar 4 confirms it; bars 5 cannot break anything yet
  const st = X.structure(b, 2);
  assert.deepEqual(st.ev, []); assert.equal(st.trend, null); assert.equal(st.upLevel, 109);
  const b2 = zigzag().slice(0, 8); b2[7] = { ...b2[7], stub: true };
  assert.deepEqual(X.structure(b2, 2).ev, []);      // the 110 close is on the forming bar: not yet a break
  assert.deepEqual(X.structure([], 3), { sw: [], ev: [], trend: null, upLevel: null, dnLevel: null, upSwing: null, dnSwing: null });
});

test('mapIndex puts a 5m event on the right 1m / 15m bar', () => {
  const b5 = [{ day: 'd', m: 0, slot: 0 }, { day: 'd', m: 5, slot: 1 }, { day: 'd', m: 10, slot: 2 }];
  const b1 = Array.from({ length: 15 }, (_, m) => ({ day: 'd', m, slot: m }));
  assert.equal(X.mapIndex(b1, b5, 1, false), 5); assert.equal(X.mapIndex(b1, b5, 1, true), 9);
  const b15 = [{ day: 'd', m: 0, slot: 0 }, { day: 'd', m: 15, slot: 1 }];
  assert.equal(X.mapIndex(b15, b5, 2, true), 0);
  assert.equal(X.mapIndex(b1.slice(0, 7), b5, 1, true), 6);   // as-of cut: the last bar at or before the minute
  assert.equal(X.mapIndex(b1, b5, 9, true), -1);
  assert.equal(X.mapIndex([{ day: 'd', m: 0, slot: 0 }], b5, 2, true, 15), 0);   // a one-bar session: the timeframe comes from the caller
});

test('view helpers: fitView, niceStep, axisEvery, positionWords', () => {
  assert.deepEqual(X.fitView(375, 0, 375, 1, 1, false), { a: 254.5, b: 375.5 });
  assert.deepEqual(X.fitView(375, 0, 375, 1, 1, true), { a: -0.5, b: 375.5 });
  assert.deepEqual(X.fitView(150, 75, 75, 1, 5, false), { a: 74.5, b: 150.5 });
  assert.deepEqual(X.fitView(40, 0, 75, 1, 5, false), { a: -0.5, b: 75.5 });   // a short morning still shows the whole session width
  assert.equal(X.niceStep(0.37), 0.5); assert.equal(X.niceStep(2.1), 2.5); assert.equal(X.niceStep(0.8), 1); assert.equal(X.niceStep(0), 1);
  assert.equal(X.axisEvery(5, 10), 60); assert.equal(X.axisEvery(1, 2.6), 30); assert.equal(X.axisEvery(15, 3), 375);
  assert.deepEqual(X.positionWords(511, 506.58, 510.85, 499, 'up'), ['above VWAP', 'above PDH', 'structure bullish']);
  assert.deepEqual(X.positionWords(500, null, 510.85, 499, null), ['inside yesterday']);
  assert.deepEqual(X.positionWords(null, 1, 2, 3, 'up'), []);
});
