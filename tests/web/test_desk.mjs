import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
(0, eval)(readFileSync(path.join(here, '../../web/desk.js'), 'utf8'));
const D = globalThis.ScanDesk;

const v = { ref: 'pattern:TATASTEEL|double_bottom|10:20', ts: '2026-10-19T10:45:00+05:30', symbol: 'TATASTEEL', model: 'claude-opus-5-5', latency_ms: 1640, cost_inr: 0.52,
  context: { symbol: 'TATASTEEL', side: 'long', blind_to: 'news · index level · order book', trigger: { label: 'double bottom', score: 4 },
    ticket: { entry: 174.9, stop: 172.35, target: 177.45, rr: 1.0, size: { qty: 750, lots: 1, risk_inr: 2000 } }, setup_record: { hit_rate_pct: 67.3, triggers_20d: 193 } },
  output: { call: 'take', confidence: 7, reasons: [{ mark: 'ok', text: 'a' }, { mark: 'no', text: 'b' }, { mark: 'warn', text: 'c' }], exit_rule: 'x', book_half_at: null } };

test('headline and confidence line', () => {
  assert.deepEqual(D.headline(v), { call: 'TAKE', cls: 'take', symbol: 'TATASTEEL', side: 'BUY', label: 'double bottom · 10:45' });
  assert.equal(D.confidenceLine(v), '7/10 · setup 67% · 193 cases · score 4');
  assert.equal(D.headline({ ts: '2026-10-19T10:45:00+05:30', symbol: 'X', error: 'budget reached' }).call, 'NO VERDICT');
});

test('ticket, reasons, footer', () => {
  const t = D.ticket(v, 2000);
  assert.equal(t.entry, '174.90'); assert.equal(t.rr, '1 : 1.0'); assert.equal(t.size, '750 · 1 lot'); assert.equal(t.riskLabel, 'Size @₹2,000');
  assert.deepEqual(D.reasons(v).map(r => r.mark + r.cls), ['✓up', '✗dn', '!am']);
  assert.deepEqual(D.footer(v), { left: 'blind to: news · index level · order book', right: '1.6 s · ₹0.52 · opus 5 5' });
});

test('budget meter', () => {
  assert.deepEqual(D.budget(23, 100), { pct: 23, text: '₹23 of ₹100', stopped: false, briefsStopped: false });
  assert.equal(D.budget(75, 100).briefsStopped, true); assert.equal(D.budget(100, 100).stopped, true); assert.equal(D.budget(5, 0).pct, 0);
});

test('new verdicts: seeding marks without firing, then only unseen with output', () => {
  const seen = new Set();
  assert.deepEqual(D.newVerdicts(seen, [v], { seed: true }), []);
  assert.deepEqual(D.newVerdicts(seen, [v]), []);
  const w = { ...v, ref: 'alert:9', output: null };
  const x = { ...v, ref: 'alert:10' };
  assert.deepEqual(D.newVerdicts(seen, [w, x]).map(r => r.ref), ['alert:10']);
  const n = D.notification(x);
  assert.equal(n.title, 'Desk · TAKE 7/10 · TATASTEEL BUY'); assert.equal(n.at, '10:45'); assert.match(n.body, /entry 174.90/);
});
