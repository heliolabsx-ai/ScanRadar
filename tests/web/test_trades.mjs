import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/filter.js'), 'utf8'));
(0, eval)(readFileSync(path.join(here, '../../web/trades.js'), 'utf8'));
const T = globalThis.ScanTrades;
const F = globalThis.ScanFilter;
const S = { capital: 500000, max_loss: 2000, day_loss: 6000, max_trades: 5, position_cap: 0.6, product: 'intraday', flat_by: '15:20' };

test('size matches the server arithmetic', () => {
  assert.deepEqual(T.size(854.6, 845.9, S), { shares: 229, risk_inr: 1992.3, position_inr: 195703.4, limited_by: 'max_loss' });
  assert.equal(T.size(6104, 6100, S, 'short').limited_by, 'position_cap');
  assert.equal(T.size(100, 100, S).limited_by, 'zero_distance');
  assert.equal(T.size(100, 101, { ...S, product: 'delivery' }, 'short').limited_by, 'delivery_no_short');
});
test('tiles', () => {
  const t = T.tiles({ committed: 4766, realised: -1200, realised_losses: 1200, taken: 3, gate_state: 'CLOSED', blocked_total: 4, blocked_won: 1, room_inr: 34 }, S, true);
  assert.deepEqual(t.map(x => x.key), ['capital', 'max_loss', 'day_risk', 'trades', 'pnl', 'blocked_won']);
  assert.equal(t[2].value, '₹5,966 / ₹6,000'); assert.equal(t[3].value, '3 / 5'); assert.equal(t[3].sub, 'gate CLOSED'); assert.equal(t[4].cls, 'dn'); assert.equal(t[5].value, '1 of 4');
  assert.equal(T.tiles({ taken: 0 }, S, false, '2026-11-03')[3].sub, 'rules not applied (before 3 Nov 2026)');
});
test('odds, verdict, gate, ticket, order', () => {
  const odds = { mc: { target_pct: 39.4, stop_pct: 41.5, none_pct: 19.1 }, verdict: 'coin', sentence: 's' };
  assert.equal(T.oddsPair(odds), '39/42'); assert.equal(T.oddsPair(null), '—');
  assert.deepEqual(T.oddsBar(odds), { target: 39.4, stop: 41.5, none: 19.1 });
  assert.equal(T.verdictWord(odds), 'coin flip'); assert.equal(T.verdictWord(null), 'no history'); assert.equal(T.verdictClass(null), 'none');
  const row = { id: 2, symbol: 'KFINTECH', side: 'long', entry: 854.6, stop: 845.9, target: 861.9, target2: 872, rr: 0.84, size: { shares: 229, risk_inr: 1992.3 }, gate: 'BLOCK', gate_reason: '5 trades today', ts: '2026-10-28T11:05:00+05:30' };
  assert.equal(T.ticket(row, S), 'BUY 229 KFINTECH @ 854.60 · stop 845.90 · T1 861.90 · T2 872.00 · risk ₹1,992 · 1 : 0.8 · BLOCKED: 5 trades today');
  assert.deepEqual(T.gateLabel(row), { text: 'BLOCK', cls: 'block', reason: '5 trades today' }); assert.equal(T.gateLabel({ gate: null }).text, '—');
  const a = { ...row, id: 1, ts: '2026-10-28T10:55:00+05:30' };
  assert.deepEqual(T.orderRows([a, row], 'live').map(r => r.id), [2, 1]); assert.deepEqual(T.orderRows([row, a], 'replay').map(r => r.id), [1, 2]);
});

// extras beyond the brief: edges of the helper
test('inr, verdict words, ticket edges', () => {
  assert.equal(T.inr(195703.4), '₹1,95,703'); assert.equal(T.inr(null), '—'); assert.equal(T.inr(-1200), '-₹1,200');
  const v = k => ({ mc: { target_pct: 1, stop_pct: 1, none_pct: 98 }, verdict: k });
  assert.deepEqual(['flat', 'coin', 'edge', 'bad'].map(k => T.verdictWord(v(k))), ['cannot resolve by close', 'coin flip', 'odds favour T1', 'odds favour stop']);
  assert.deepEqual(['flat', 'coin', 'edge', 'bad'].map(k => T.verdictClass(v(k))), ['flat', 'coin', 'edge', 'bad']);
  assert.deepEqual(T.oddsBar(null), { target: 0, stop: 0, none: 0 });
  const short = { symbol: 'X', side: 'short', entry: 100, stop: 101, target: 99, target2: null, rr: 1, size: { shares: 10, risk_inr: 10 }, gate: 'PASS' };
  assert.equal(T.ticket(short, S), 'SELL 10 X @ 100.00 · stop 101.00 · T1 99.00 · risk ₹10 · 1 : 1.0');
  assert.deepEqual(T.gateLabel({ gate: 'PASS' }), { text: 'PASS', cls: 'pass', reason: '' });
  assert.equal(T.tiles({ realised: 50 }, S, true)[4].cls, 'up');
  assert.equal(T.tiles({ committed: 5966, room_inr: 34 }, S, true)[2].pct, 99);
});
test('filterRows applies sector, price band and R:R only', () => {
  const rows = [{ symbol: 'A', entry: 500, rr: 2 }, { symbol: 'B', entry: 3000, rr: 1 }, { symbol: 'C', entry: 3000, rr: 2 }];
  const sectors = { A: 'BANK', B: 'IT', C: 'IT' };
  const f = (o) => ({ ...F.defaults(), ...o });
  assert.equal(T.filterRows(rows, F.defaults(), sectors).length, 3);
  assert.deepEqual(T.filterRows(rows, f({ sectors: ['IT'] }), sectors).map(r => r.symbol), ['B', 'C']);
  assert.deepEqual(T.filterRows(rows, f({ pmin: 1000 }), sectors).map(r => r.symbol), ['B', 'C']);
  assert.deepEqual(T.filterRows(rows, f({ rr: 1.5 }), sectors).map(r => r.symbol), ['A', 'C']);
  // RVOL / OI / VWAP / picks checks never apply to trade rows
  assert.equal(T.filterRows(rows, f({ rvol: 3, oi: 'buildup', vwap: 'aligned', picks: true }), sectors).length, 3);
});
