import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/inside.js'), 'utf8'));
const I = globalThis.ScanInside;
const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) < eps, `${a} != ${b}`);

// a long: entry 100, stop 98, target 104; bars for minutes 0..7, minute 3 missing
const ROW = {
  sym: 'AAA', side: 'long', entry: 100, stop: 98, target: 104, hit: 'target', x: '09:23', xp: 104,
  m: [0, 1, 2, 4, 5, 6, 7], o: [99, 99.5, 100, 100, 100, 101, 103], h: [100, 100.5, 100.5, 100.5, 101.5, 103.5, 104.5],
  l: [98.5, 99, 99.5, 99.5, 99.8, 100.8, 102.8], c: [99.5, 100, 100, 100, 101, 103, 104], v: [10, 10, 10, 10, 10, 10, 10]
};
const SHORT = { sym: 'BBB', side: 'short', entry: 200, stop: 202, target: 196, hit: 'stop', x: '09:25', xp: 202, m: [0, 5, 6], o: [201, 200, 200], h: [201, 200.5, 201], l: [200, 199.5, 200], c: [200, 200, 201], v: [5, 5, 5] };

test('hhmm and mins: a minute index and its clock time', () => {
  assert.equal(I.hhmm(0), '09:15'); assert.equal(I.hhmm(5), '09:20'); assert.equal(I.hhmm(5.99), '09:20'); assert.equal(I.hhmm(365), '15:20');
  assert.equal(I.hhmm(-3), '09:15'); assert.equal(I.hhmm(999), '15:30');
  assert.equal(I.mins('09:20'), 5); assert.equal(I.mins('15:20'), 365); assert.equal(I.mins('09:15'), 0);
});

test('prep: the per-minute series carry the last price over a missing bar', () => {
  const r = I.prep(ROW);
  assert.equal(r.long, true); assert.equal(r.xm, 8);
  assert.equal(r.P[0], 99); assert.equal(r.P[1], 99.5); assert.equal(r.P[3], 100); assert.equal(r.P[4], 100);   // minute 3 has no bar: the price stays
  assert.equal(r.P[8], 104); assert.equal(r.P[375], 104);                                                       // and after the last bar
  assert.equal(r.HI[2], 100.5); assert.equal(r.LO[2], 98.5); assert.equal(r.HI[8], 104.5);
  near(r.VW[1], (100 + 98.5 + 99.5) / 3); near(r.VW[2], ((100 + 98.5 + 99.5) / 3 + (100.5 + 99 + 100) / 3) / 2);
  near(r.f0, 2 / 6); near(r.riskPct, 2); near(r.rewPct, 4);
  assert.equal(r.O[3], null); assert.equal(r.C[4], 100);
  const s = I.prep(SHORT);
  assert.equal(s.long, false); near(s.f0, 2 / 6); assert.equal(s.xm, 10);
});

test('prep: an open row never closes, and a row without bars starts at its entry', () => {
  assert.equal(I.prep({ ...ROW, hit: null, x: null, xp: null }).xm, Infinity);
  const bare = I.prep({ side: 'long', entry: 100, stop: 98, target: 104, hit: null, m: [], o: [], h: [], l: [], c: [], v: [] });
  assert.equal(bare.P[0], 100); assert.equal(bare.P[375], 100);
});

test('stateAt: nothing before the alarm, the live price after it, the exit once closed', () => {
  const r = I.prep(ROW);
  const before = I.stateAt(r, 3, 5);
  assert.equal(before.fired, false); assert.equal(before.pnl, 0); assert.equal(before.done, false);
  const mid = I.stateAt(r, 6.5, 5);                        // half way through the 09:21 bar: between 101 and 103
  assert.equal(mid.fired, true); assert.equal(mid.done, false); near(mid.px, 102); near(mid.pnl, 2); near(mid.f, 4 / 6);
  const done = I.stateAt(r, 9, 5);
  assert.equal(done.done, true); near(done.pnl, 4); assert.equal(done.f, 1);
  const s = I.prep(SHORT), st = I.stateAt(s, 20, 5);
  assert.equal(st.done, true); near(st.pnl, -1); assert.equal(st.f, 0);
  const open = I.prep({ ...ROW, hit: null, x: null, xp: null }), late = I.stateAt(open, 300, 5);
  assert.equal(late.done, false); near(late.px, 104); near(late.pnl, 4); assert.equal(late.f, 1);   // clamped at the target's end
  const flat = I.prep({ ...ROW, hit: 'none', x: '15:20', xp: 101 }), end = I.stateAt(flat, 370, 5);
  assert.equal(end.done, true); near(end.pnl, 1); near(end.f, 3 / 6);
});

test('edge: the whole day once it is over, the last stored minute while it is live', () => {
  assert.equal(I.edge({ live: false, last: 100 }), 375);
  assert.equal(I.edge({ live: true, last: 20 }), 21);
  assert.equal(I.edge({ live: true, last: null }), 0);
  assert.equal(I.edge({ live: true, last: 400 }), 375);
});

test('tiles: target, stopped, still open and the average after costs', () => {
  const rows = [I.prep(ROW), I.prep(SHORT), I.prep({ ...ROW, hit: null, x: null, xp: null })];
  const at = t => I.tiles(rows, rows.map(r => I.stateAt(r, t, 5)), 0.1);
  const a = at(20);
  assert.deepEqual([a.n, a.target, a.stop, a.open], [3, 1, 1, 1]); near(a.avg, (4 - 1 + 4) / 3 - 0.1);
  const early = at(6);
  assert.deepEqual([early.target, early.stop, early.open], [0, 0, 3]);
  assert.equal(I.tiles([], [], 0.1).avg, null);
});

test('pill and phase: the words on a row and under the clock', () => {
  const r = I.prep(ROW);
  assert.deepEqual(I.pill(r, I.stateAt(r, 9, 5)), { cls: 't', text: 'target 09:23' });
  assert.deepEqual(I.pill(r, I.stateAt(r, 6, 5)), { cls: '', text: 'open' });
  const s = I.prep(SHORT);
  assert.deepEqual(I.pill(s, I.stateAt(s, 12, 5)), { cls: 's', text: 'stop 09:25' });
  const f = I.prep({ ...ROW, hit: 'none', x: '15:20', xp: 101 });
  assert.deepEqual(I.pill(f, I.stateAt(f, 370, 5)), { cls: '', text: 'flat 15:20' });
  const d = { fire: 5, close: 365, flat_by: '15:20' };
  assert.equal(I.phase(2, d, false), 'first candle forming'); assert.equal(I.phase(100, d, false), 'alarms running');
  assert.equal(I.phase(100, d, true), 'live · alarms running'); assert.equal(I.phase(365, d, false), 'day closed out at 15:20');
});

test('bubble: the Telegram text as a chat bubble', () => {
  const sum = I.bubble({ kind: 'summary', at: '09:20', quiet: true, delivery: { ok: true, at: '09:20:03', error: null },
    text: '<b>Inside open · 09:20</b>\nmarket: 48 % of stocks above VWAP · mixed\n<b>LONG · target PDH</b> (16)\n★ KEI 3,771.00\n09:20  ·  scanradar.tech' }, 'live');
  assert.equal(sum.cls, 'sum');
  assert.equal(sum.html, '<b>Inside open · 09:20</b><span class="q">quiet</span><br>market: 48 % of stocks above VWAP · mixed<br><b>LONG · target PDH</b> (16)<br>★ KEI 3,771.00<span class="tm">09:20 · sent 09:20:03</span>');
  const tgt = I.bubble({ kind: 'target', at: '11:19', quiet: false, delivery: null, text: '<b>TARGET</b> · <b>SIEMENS</b>\nPDL 3,570.50 reached at 11:19 · +1.29 %\nInside open short  ·  scanradar.tech' }, 'replay');
  assert.equal(tgt.cls, 'bt');
  assert.equal(tgt.html, '<b>TARGET</b> · <b>SIEMENS</b><br>PDL 3,570.50 reached at 11:19 · +1.29 %<span class="tm">11:19 · replay</span>');
  assert.match(I.bubble({ kind: 'stop', at: '09:59', text: '<b>STOP</b>', delivery: { ok: false, error: 'time<out' } }, 'live').html, /failed: time&lt;out<\/span>$/);
  assert.match(I.bubble({ kind: 'near', at: '10:01', text: 'x', delivery: null }, 'live').html, /10:01 · not recorded<\/span>$/);
  const res = I.bubble({ kind: 'result', at: '15:20', quiet: true, delivery: null, text: '<b>Inside open · day result</b>\n37 alarms · 8 target' }, null);
  assert.equal(res.cls, 'sum'); assert.equal(res.html, '<b>Inside open · day result</b><span class="q">quiet</span><br>37 alarms · 8 target<span class="tm">15:20</span>');
});

test('shown, comboKey and recNote: the two Show only switches', () => {
  const rows = [{ star: true, mkt: 'with' }, { star: true, mkt: 'against' }, { star: false, mkt: 'with' }, { star: false, mkt: '' }];
  const count = F => rows.filter(r => I.shown(r, F)).length;
  assert.deepEqual([count({ star: false, mkt: false }), count({ star: true, mkt: false }), count({ star: false, mkt: true }), count({ star: true, mkt: true })], [4, 2, 2, 1]);
  assert.deepEqual([I.comboKey({}), I.comboKey({ star: true }), I.comboKey({ mkt: true }), I.comboKey({ star: true, mkt: true })], ['all', 'star', 'mkt', 'both']);
  const rec = { min: 10, max: 42, gross: 0.08 };
  assert.equal(I.recNote('all', rec, 0.1), 'Between 10 and 42 alarms a morning, all at 09:20. Before costs +0.08 % a trade. Costs counted at 0.10 % a trade. A replay of stored sessions, not a forecast.');
  assert.match(I.recNote('both', rec, 0.1), /^The strongest three of the side the market leaned to; nothing on a mixed morning\. Costs counted at 0\.10 %/);
  assert.match(I.recNote('mkt', rec, 0.1), /not a forecast\.$/); assert.match(I.recNote('star', rec, 0.1), /closed furthest from VWAP/);
});
