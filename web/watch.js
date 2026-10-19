/* Watch board helpers: pure functions (tested in node). Rendering lives in index.html. */
(function (root) {
  const KEY = 'watch';
  const DEFAULT = ['TECHM', 'TATASTEEL', 'HDFCBANK', 'RELIANCE', 'SBIN', 'DIXON'];
  const fmt2 = n => Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function load(raw) { try { const v = JSON.parse(raw || 'null'); return Array.isArray(v) && v.length ? v.filter(s => typeof s === 'string') : DEFAULT.slice(); } catch (e) { return DEFAULT.slice(); } }
  function save(list) { return JSON.stringify(list); }

  // Odometer: digits of the formatted price with a flag on the ones that changed since the previous print.
  function digits(prev, next) {
    const a = prev == null ? '' : fmt2(prev), b = fmt2(next);
    const pad = a.padStart(b.length, ' ');
    return b.split('').map((ch, i) => ({ ch, changed: pad[i] !== ch }));
  }

  // Heat: 0..0.55 fill strength from the day's % move (saturates at 2.5 %).
  function heat(pct) { return Math.min(0.55, Math.abs(pct || 0) / 2.5); }

  // Sort with hysteresis: re-rank on a sort change, when the set changes, or every `every` minutes.
  function order(rows, sortBy, cache, minuteIdx, every) {
    const by = { pct: (a, b) => (b.pct || 0) - (a.pct || 0), rank: (a, b) => (a.rank || 999) - (b.rank || 999), rvol: (a, b) => (b.rvol || 0) - (a.rvol || 0),
      events: (a, b) => ((b.events || []).length - (a.events || []).length) || ((b.pct || 0) - (a.pct || 0)) }[sortBy] || ((a, b) => 0);
    const key = rows.map(r => r.symbol).sort().join(',');
    const stale = !cache || cache.key !== key || cache.sortBy !== sortBy || (minuteIdx - cache.at) >= (every || 5);
    const next = stale ? { key, sortBy, at: minuteIdx, syms: [...rows].sort(by).map(r => r.symbol) } : cache;
    const pos = {}; next.syms.forEach((s, i) => pos[s] = i);
    return { cache: next, rows: [...rows].sort((a, b) => (pos[a.symbol] ?? 999) - (pos[b.symbol] ?? 999)) };
  }

  // Event chips for a symbol from the day's pattern rows and desk verdicts (newest first, max 3).
  function events(symbol, prows, verdicts) {
    const out = [];
    (prows || []).filter(r => r.symbol === symbol).forEach(r => {
      const conf = r.confidence && r.confidence.hit_rate != null ? ` · conf ${Math.round(r.confidence.hit_rate)}%` : '';
      const label = (root.ScanPatterns && root.ScanPatterns.kindLabel(r.kind)) || r.kind;
      out.push({ k: r.state === 'triggered' ? 'trig' : r.state === 'failed' ? 'fail' : 'form', t: `${label} · ${r.state.toUpperCase()}${r.triggered_at ? ' ' + String(r.triggered_at).slice(11, 16) : ''}${conf}`, ts: String(r.triggered_at || r.formed_at) });
    });
    (verdicts || []).filter(v => v.symbol === symbol && v.output).forEach(v => out.push({ k: v.output.call, t: `DESK ${v.output.call.toUpperCase()} ${v.output.confidence}/10`, ts: String(v.ts) }));
    return out.sort((a, b) => b.ts.localeCompare(a.ts)).slice(0, 3);
  }

  function position(row, v) { const lo = row.prev_low || row.prev_close * 0.985, hi = row.prev_high || row.prev_close * 1.015; return Math.max(2, Math.min(98, (v - lo) / ((hi - lo) || 1) * 100)); }

  function groups(snap, prows, sectorsOf) {
    const picks = snap ? [...snap.bulls, ...snap.bears].filter(r => r.picked).map(r => r.symbol) : [];
    const trig = [...new Set((prows || []).filter(r => r.triggered_at).map(r => r.symbol))];
    const bySector = {}; Object.entries(sectorsOf || {}).forEach(([s, sec]) => (bySector[sec] = bySector[sec] || []).push(s));
    return { picks, trig, bySector };
  }

  root.ScanWatch = { load, save, digits, heat, order, events, position, groups, DEFAULT };
})(typeof window !== 'undefined' ? window : globalThis);
