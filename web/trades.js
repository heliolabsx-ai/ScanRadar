/* Trades tab helpers: pure functions (tested in node). Rendering lives in index.html.
   Cash shares only. Sizing mirrors core/sizing.py. */
(function (root) {
  const r2 = n => Math.round(n * 100) / 100;
  const fmt2 = n => n == null ? '—' : Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // "₹1,95,703": en-IN grouping, no decimals; null -> "—".
  function inr(n) {
    if (n == null || isNaN(+n)) return '—';
    const v = Math.round(Math.abs(+n));
    return (+n < 0 && v > 0 ? '-' : '') + '₹' + v.toLocaleString('en-IN');
  }

  // Whole shares for a trade: min(max_loss / distance, capital x position_cap / entry). Same as core.sizing.shares.
  function size(entry, stop, settings, side = 'long') {
    const out = (n, limit, dist = 0) => ({ shares: n, risk_inr: r2(n * dist), position_inr: r2(n * entry), limited_by: limit });
    if (settings.product === 'delivery' && side === 'short') return out(0, 'delivery_no_short');
    const dist = Math.round(Math.abs(entry - stop) * 1e4) / 1e4;
    if (dist === 0) return out(0, 'zero_distance');
    const byLoss = Math.floor(settings.max_loss / dist);
    const byCap = Math.floor(settings.capital * settings.position_cap / entry);
    return byCap < byLoss ? out(byCap, 'position_cap', dist) : out(byLoss, 'max_loss', dist);
  }

  // "2026-11-03" -> "3 Nov 2026"
  function dateText(iso) {
    const [y, m, d] = String(iso).split('-');
    return `${+d} ${MONTHS[+m - 1] || m} ${y}`;
  }

  // Six tiles above the trades table, from the server's ledger and settings.
  function tiles(ledger, settings, applied, rulesFrom = '2026-11-03') {
    const L = ledger || {}, s = settings || {};
    const num = k => +L[k] || 0;
    const room = L.room_inr;
    const used = room != null && room > 0 ? s.day_loss - room : num('committed') + num('realised_losses');
    const pct = s.day_loss > 0 ? Math.max(0, Math.min(100, Math.round(used / s.day_loss * 100))) : 0;
    const realised = num('realised');
    return [
      { key: 'capital', label: 'Capital', value: inr(s.capital), sub: `${s.product} · ${Math.round(s.position_cap * 100)} % max in one name`, cls: '' },
      { key: 'max_loss', label: 'Max loss per trade', value: inr(s.max_loss), sub: 'sets every share count', cls: '' },
      { key: 'day_risk', label: 'Day risk', value: `${inr(used)} / ${inr(s.day_loss)}`, sub: `${inr(room != null ? room : Math.max(s.day_loss - used, 0))} of room`, pct, cls: pct >= 100 ? 'dn' : pct >= 80 ? 'am' : '' },
      { key: 'trades', label: 'Trades', value: `${num('taken')} / ${s.max_trades}`, sub: applied ? `gate ${L.gate_state || 'OPEN'}` : `rules not applied (before ${dateText(rulesFrom)})`, cls: applied && L.gate_state === 'CLOSED' ? 'dn' : '' },
      { key: 'pnl', label: 'Realised P&L', value: inr(realised), sub: 'at stop / T1 fills', cls: realised >= 0 ? 'up' : 'dn' },
      { key: 'blocked_won', label: 'Blocked that won', value: `${num('blocked_won')} of ${num('blocked_total')}`, sub: "the gate's honest cost", cls: '' },
    ];
  }

  const ts = r => { const t = Date.parse(r.ts); return isNaN(t) ? 0 : t; };
  // live: newest first; replay: time order. Ties break on id.
  function orderRows(rows, mode) {
    const dir = mode === 'live' ? -1 : 1;
    return (rows || []).slice().sort((a, b) => dir * ((ts(a) - ts(b)) || ((a.id || 0) - (b.id || 0))));
  }

  const MC = { target: 'target_pct', stop: 'stop_pct', none: 'none_pct' };
  const mc = odds => (odds && odds.mc) || null;
  // "39/42": chance of T1 first / stop first, rounded.
  function oddsPair(odds) {
    const m = mc(odds);
    return m ? `${Math.round(m.target_pct)}/${Math.round(m.stop_pct)}` : '—';
  }
  function oddsBar(odds) {
    const m = mc(odds);
    const get = k => m && m[MC[k]] != null ? r2(m[MC[k]]) : 0;
    return { target: get('target'), stop: get('stop'), none: get('none') };
  }

  const WORD = { flat: 'cannot resolve by close', coin: 'coin flip', edge: 'odds favour T1', bad: 'odds favour stop' };
  const verdictClass = odds => odds && WORD[odds.verdict] ? odds.verdict : 'none';
  const verdictWord = odds => WORD[verdictClass(odds)] || 'no history';

  function gateLabel(row) {
    const g = row && row.gate;
    const text = g === 'PASS' || g === 'BLOCK' ? g : '—';
    return { text, cls: g === 'PASS' ? 'pass' : g === 'BLOCK' ? 'block' : 'off', reason: (row && row.gate_reason) || '' };
  }

  // One-line order ticket: "BUY 229 KFINTECH @ 854.60 · stop 845.90 · T1 861.90 · T2 872.00 · risk ₹1,992 · 1 : 0.8"
  function ticket(row, settings) {
    const sz = row.size || size(row.entry, row.stop, settings, row.side);
    const parts = [`${row.side === 'short' ? 'SELL' : 'BUY'} ${(sz.shares || 0).toLocaleString('en-IN')} ${row.symbol} @ ${fmt2(row.entry)}`,
      `stop ${fmt2(row.stop)}`, `T1 ${fmt2(row.target)}`];
    if (row.target2 != null) parts.push(`T2 ${fmt2(row.target2)}`);
    parts.push(`risk ${inr(sz.risk_inr)}`, `1 : ${row.rr != null ? Number(row.rr).toFixed(1) : '—'}`);
    if (row.gate === 'BLOCK') parts.push(`BLOCKED: ${row.gate_reason || ''}`);
    return parts.join(' · ');
  }

  // Dashboard view filter on trade rows: sector, price band and R:R apply; RVOL / OI / VWAP / picks do not.
  function filterRows(rows, filter, sectorsOf) {
    const f = { ...filter, rvol: null, oi: 'any', vwap: 'any', picks: false };
    return (rows || []).filter(r => root.ScanFilter.pass({ symbol: r.symbol, sector: (sectorsOf || {})[r.symbol], price: r.entry, rr: r.rr, rvol: null, oi_label: null, above_vwap: null, picked: false }, f));
  }

  root.ScanTrades = { size, tiles, orderRows, oddsPair, oddsBar, verdictWord, verdictClass, gateLabel, ticket, filterRows, inr };
})(typeof window !== 'undefined' ? window : globalThis);
