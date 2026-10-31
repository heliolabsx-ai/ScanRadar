/* Desk tab helpers: pure functions (tested in node). Rendering lives in index.html. */
(function (root) {
  const fmt2 = n => n == null ? '—' : Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const MARK = { ok: '✓', no: '✗', warn: '!' };
  const CLS = { ok: 'up', no: 'dn', warn: 'am' };

  // Header line pieces for a verdict card.
  function headline(v) {
    const c = v.context || {}, o = v.output || {};
    const side = c.side === 'long' ? 'BUY' : 'SELL';
    const call = o.call ? o.call.toUpperCase() : (v.error ? 'NO VERDICT' : '…');
    return { call, cls: o.call || 'off', symbol: c.symbol || v.symbol || '', side, label: ((c.trigger || {}).label || '') + ' · ' + String(v.ts || '').slice(11, 16) };
  }

  // "7/10 · setup 67% · 193 cases" from the record the model was given.
  function confidenceLine(v) {
    const o = v.output || {}, r = (v.context || {}).setup_record || {};
    const parts = [];
    if (o.confidence != null) parts.push(`${o.confidence}/10`);
    if (r.hit_rate_pct != null) parts.push(`setup ${Math.round(r.hit_rate_pct)}%`);
    const n = r.triggers_20d != null ? r.triggers_20d : r.fires_20d;
    if (n != null) parts.push(`${n} cases`);
    const sc = (v.context || {}).trigger || {};
    if (sc.score != null) parts.push(`score ${sc.score}`);
    return parts.join(' · ');
  }

  const inr = n => '₹' + Math.round(+n || 0).toLocaleString('en-IN');
  const LIMITED = { zero_distance: 'stop equals entry', delivery_no_short: 'delivery: no shorts', position_cap: 'position cap', max_loss: 'max loss' };
  // Size in cash shares: new rows carry {shares, position_inr, limited_by}; rows before the switch carry {qty, lots}.
  function sizeText(s) {
    if (s.shares != null) return s.shares > 0 ? `${Number(s.shares).toLocaleString('en-IN')} sh · ${inr(s.position_inr)}` : `0 sh · ${LIMITED[s.limited_by] || 'no size'}`;
    if (s.qty != null) return `${Number(s.qty).toLocaleString('en-IN')} sh`;
    return '—';
  }

  function ticket(v, maxLoss) {
    const t = (v.context || {}).ticket || {};
    const s = t.size || {};
    return { entry: fmt2(t.entry), stop: fmt2(t.stop), target: fmt2(t.target), rr: t.rr != null ? `1 : ${Number(t.rr).toFixed(1)}` : '—',
      size: sizeText(s), riskLabel: `Size @₹${(maxLoss || s.risk_inr || 0).toLocaleString('en-IN')}` };
  }

  // "odds 39/42 · coin flip · gate BLOCK: 5 trades today": the replay odds and the gate the model was shown; '' when neither.
  function oddsLine(v) {
    const c = v.context || {}, T = root.ScanTrades, parts = [];
    if (c.odds && T) parts.push(`odds ${T.oddsPair(c.odds)}`, T.verdictWord(c.odds));
    if (c.gate && c.gate.state) parts.push(`gate ${c.gate.state}${c.gate.reason ? ': ' + c.gate.reason : ''}`);
    return parts.join(' · ');
  }

  function reasons(v) {
    return ((v.output || {}).reasons || []).map(r => ({ mark: MARK[r.mark] || '·', cls: CLS[r.mark] || 'mut', text: r.text }));
  }

  function footer(v) {
    const parts = [`blind to: ${(v.context || {}).blind_to || 'news · index level'}`];
    const right = [];
    if (v.latency_ms != null) right.push(`${(v.latency_ms / 1000).toFixed(1)} s`);
    if (v.cost_inr != null) right.push(`₹${Number(v.cost_inr).toFixed(2)}`);
    if (v.model) right.push(String(v.model).replace('claude-', '').replace(/-/g, ' '));
    return { left: parts.join(' · '), right: right.join(' · ') };
  }

  function budget(spend, budgetInr) {
    const pct = budgetInr > 0 ? Math.min(100, Math.round(spend / budgetInr * 100)) : 0;
    return { pct, text: `₹${Math.round(spend)} of ₹${Math.round(budgetInr)}`, stopped: budgetInr > 0 && spend >= budgetInr, briefsStopped: budgetInr > 0 && spend >= budgetInr * 0.7 };
  }

  // Which verdicts are new since the set `seen` (keys), for notifications. Mutates `seen`.
  function newVerdicts(seen, verdicts, opts) {
    const seed = !!(opts && opts.seed), out = [];
    for (const v of verdicts || []) {
      const k = v.ref || `${v.symbol}|${v.ts}`;
      if (seen.has(k)) continue;
      seen.add(k);
      if (!seed && v.output) out.push(v);
    }
    return out;
  }

  function notification(v) {
    const h = headline(v), t = ticket(v);
    return { title: `Desk · ${h.call} ${v.output && v.output.confidence != null ? v.output.confidence + '/10' : ''} · ${h.symbol} ${h.side}`.replace(/\s+/g, ' '),
      body: `${h.label}  ·  entry ${t.entry} · stop ${t.stop} · target ${t.target}`, symbol: h.symbol, at: String(v.ts || '').slice(11, 16) };
  }

  root.ScanDesk = { headline, confidenceLine, ticket, oddsLine, reasons, footer, budget, newVerdicts, notification };
})(typeof window !== 'undefined' ? window : globalThis);
