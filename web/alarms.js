/* Alarms board helpers: pure functions (tested in node). Rendering lives in index.html. */
(function (root) {
  const hhmm = ts => { const m = /[T ](\d\d:\d\d)/.exec(String(ts == null ? '' : ts)); return m ? m[1] : ''; };
  const hhmmss = ts => { const m = /[T ](\d\d:\d\d:\d\d)/.exec(String(ts == null ? '' : ts)); return m ? m[1] : hhmm(ts); };
  const fmtTime = hhmm;
  const tsOf = r => String((r && r.ts) || '');

  // Rows per column, newest first; every column key present.
  function group(rows, columns) {
    const out = {};
    columns.forEach(k => { out[k] = []; });
    (rows || []).forEach(r => { if (out[r.col]) out[r.col].push(r); });
    columns.forEach(k => out[k].sort((a, b) => (tsOf(a) < tsOf(b) ? 1 : tsOf(a) > tsOf(b) ? -1 : 0)));
    return out;
  }

  // Unread badges: seen = {col: newest ts seen}. Visible clears them; hidden counts rows newer than seen.
  function unread(seen, rows, columns, visible) {
    const g = group(rows, columns), counts = {}, next = {};
    columns.forEach(k => {
      const prev = seen && seen[k];
      if (visible) {
        counts[k] = 0;
        if (g[k].length) next[k] = tsOf(g[k][0]); else if (prev) next[k] = prev;
      } else {
        counts[k] = prev ? g[k].filter(r => tsOf(r) > prev).length : g[k].length;
        if (prev) next[k] = prev;
      }
    });
    return { counts, seen: next };
  }

  // Levels high to low with one NOW entry at the price's place.
  function ladder(levels, price) {
    const items = (levels || []).map(([name, value]) => ({ name, value, now: false })).sort((a, b) => b.value - a.value);
    const now = { name: 'NOW', value: price, now: true };
    const at = items.findIndex(x => x.value < price);
    if (at < 0) items.push(now); else items.splice(at, 0, now);
    return items;
  }

  // Which levels are worth drawing on the chart: near the visible range or the last price, plus STOP/TGT.
  function chartLevels(levels, lo, hi, last) {
    const m = 0.5 * (hi - lo), near = [], far = [];
    (levels || []).forEach(([name, v]) => {
      const ok = name === 'STOP' || name === 'TGT' || (v > lo - m && v < hi + m) || (last && Math.abs(v / last - 1) < 0.012);
      (ok ? near : far).push([name, v]);
    });
    return { near, far };
  }

  // Plain words for where the price sits; a clause is left out when its inputs are missing.
  // Middle clause: above R2 / below S2 at the ends, else between the nearest ladder level at or below and the nearest above.
  function positionWords(price, vwap, prev_high, prev_low, ladderOut) {
    const lv = (ladderOut || []).filter(l => !l.now && l.value != null);
    const out = [], val = n => { const x = lv.find(l => l.name === n); return x ? x.value : null; };
    if (vwap != null) out.push(price >= vwap ? 'above VWAP' : 'below VWAP');
    const r2 = val('R2'), s2 = val('S2');
    if (r2 != null && price > r2) out.push('above R2');
    else if (s2 != null && price < s2) out.push('below S2');
    else {
      let lo = null, hi = null;
      lv.forEach(l => {
        if (l.value <= price) { if (!lo || l.value > lo.value) lo = l; }
        else if (!hi || l.value < hi.value) hi = l;
      });
      if (lo && hi) out.push('between ' + lo.name + ' and ' + hi.name);
    }
    if (prev_high != null && price > prev_high) out.push('above PDH');
    else if (prev_low != null && price < prev_low) out.push('below PDL');
    else if (prev_high != null && prev_low != null) out.push("inside yesterday's range");
    return out;
  }

  function deliveryMark(row) {
    const d = row && row.delivery;
    if (d && d.ok) return { text: 'sent ' + hhmmss(d.sent_at), cls: 'ok' };
    if (d && d.error === 'not configured') return { text: 'not configured', cls: 'warn' };
    if (d && d.error) return { text: 'failed: ' + d.error, cls: 'bad' };
    if (row && (row.origin === 'replay' || row.source === 'replay')) return { text: 'replay', cls: 'mut' };   // origin from /api/alarms
    return { text: 'not recorded', cls: 'mut' };
  }

  function pastDots(past) {
    return (past || []).map(([day, result, minutes]) => ({
      cls: result === 'target' ? 'ok' : result === 'stop' ? 'bad' : 'mut',
      title: day + ' ' + result + ' ' + minutes + ' min',
    }));
  }

  function timelineRows(timeline, at) {
    return (timeline || []).filter(r => !at || r.at <= at).map(r => ({ at: r.at, kind: r.kind, text: r.text }))
      .sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  }

  // Dashboard view filter on board rows, as on the Trades tab: sector, price band and R:R apply; RVOL / OI / VWAP /
  // picks do not. A row without an R:R (a break note) is not hidden by the R:R floor: it has no trade to measure.
  function filterRows(rows, filter, sectorsOf) {
    const F = root.ScanFilter; if (!F || !filter) return rows || [];
    const f = { ...filter, rvol: null, oi: 'any', vwap: 'any', picks: false };
    return (rows || []).filter(r => F.pass({ symbol: r.symbol, sector: (sectorsOf || {})[r.symbol], price: r.price, rr: r.rr == null ? Infinity : r.rr, rvol: null, oi_label: null, above_vwap: null, picked: false }, f));
  }

  root.ScanAlarms = { group, unread, ladder, chartLevels, positionWords, deliveryMark, pastDots, timelineRows, fmtTime, filterRows };
})(typeof window !== 'undefined' ? window : globalThis);
