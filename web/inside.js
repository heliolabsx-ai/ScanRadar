/* Inside open page helpers: pure functions (tested in node). Rendering lives in inside-open.html.
   A minute index counts from 09:15; the replay clock `t` is a fractional minute index (0 .. 375). */
(function (root) {
  const END = 375, OPEN = 555;
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const sg = (x, d = 2) => (x >= 0 ? '+' : '−') + Math.abs(x).toFixed(d);
  const hhmm = m => { const t = OPEN + Math.max(0, Math.min(END, Math.floor(m + 1e-6))); return String(Math.floor(t / 60)).padStart(2, '0') + ':' + String(t % 60).padStart(2, '0'); };
  const mins = s => (+String(s).slice(0, 2)) * 60 + (+String(s).slice(3, 5)) - OPEN;

  // One row of /api/inside-open with its per-minute series: P[k] = the price at minute k (the close of bar k-1,
  // carried over a missing bar), HI/LO = the day's extremes so far, VW = the session VWAP so far.
  // xm = the minute the trade was closed (Infinity while it is open); f0 = where the entry sits between stop (0) and target (1).
  function prep(r) {
    const O = new Array(END).fill(null), H = O.slice(), L = O.slice(), C = O.slice(), V = O.slice();
    (r.m || []).forEach((m, i) => { if (m >= 0 && m < END) { O[m] = r.o[i]; H[m] = r.h[i]; L[m] = r.l[i]; C[m] = r.c[i]; V[m] = r.v[i]; } });
    const P = new Float64Array(END + 1), HI = new Float64Array(END + 1), LO = new Float64Array(END + 1), VW = new Float64Array(END + 1);
    let last = (r.o && r.o.length) ? r.o[0] : r.entry, hi = -Infinity, lo = Infinity, pv = 0, vv = 0;
    P[0] = last; HI[0] = last; LO[0] = last; VW[0] = last;
    for (let k = 0; k < END; k++) {
      if (C[k] != null) { last = C[k]; hi = Math.max(hi, H[k]); lo = Math.min(lo, L[k]); pv += (H[k] + L[k] + C[k]) / 3 * V[k]; vv += V[k]; }
      P[k + 1] = last; HI[k + 1] = isFinite(hi) ? hi : last; LO[k + 1] = isFinite(lo) ? lo : last; VW[k + 1] = vv > 0 ? pv / vv : last;
    }
    const long = r.side === 'long', span = long ? r.target - r.stop : r.stop - r.target;
    const f0 = (long ? r.entry - r.stop : r.stop - r.entry) / span;
    return Object.assign({}, r, {
      long, O, H, L, C, V, P, HI, LO, VW, f0, xm: r.hit == null || !r.x ? Infinity : mins(r.x),
      riskPct: Math.abs(r.entry - r.stop) / r.entry * 100, rewPct: Math.abs(r.target - r.entry) / r.entry * 100
    });
  }

  // The row at clock `t`: price (moving inside the minute), whether the alarm has fired, whether the trade is closed,
  // its result in percent and its place between stop (0) and target (1).
  function stateAt(r, t, fire) {
    const k = Math.min(END, Math.floor(t)), fr = Math.min(1, t - k);
    const px = k >= END ? r.P[END] : r.P[k] + (r.P[k + 1] - r.P[k]) * fr;
    const fired = t >= fire, done = fired && t >= r.xm;
    const cur = done ? r.xp : px;
    const pnl = fired ? (r.long ? cur / r.entry - 1 : 1 - cur / r.entry) * 100 : 0;
    let f = r.long ? (cur - r.stop) / (r.target - r.stop) : (r.stop - cur) / (r.stop - r.target);
    f = Math.max(0, Math.min(1, f));
    if (done && r.hit === 'target') f = 1; else if (done && r.hit === 'stop') f = 0;
    return { k, px, fired, done, pnl, f };
  }

  // The furthest minute the clock may reach: the whole day once it is over, the last stored minute's close while it is live.
  const edge = d => (d && d.live) ? Math.max(0, Math.min(END, (d.last == null ? -1 : d.last) + 1)) : END;

  // The four result tiles from the shown rows and their states at the clock.
  function tiles(rows, states, cost) {
    let nt = 0, ns = 0, sum = 0;
    rows.forEach((r, i) => { const s = states[i]; sum += s.pnl; if (s.done && r.hit === 'target') nt++; if (s.done && r.hit === 'stop') ns++; });
    const n = rows.length;
    return { n, target: nt, stop: ns, open: n - nt - ns, avg: n ? sum / n - cost : null };
  }

  const pill = (r, s) => s.done && r.hit === 'target' ? { cls: 't', text: 'target ' + r.x } : s.done && r.hit === 'stop' ? { cls: 's', text: 'stop ' + r.x }
    : s.done ? { cls: '', text: 'flat ' + r.x } : { cls: '', text: 'open' };

  const phase = (t, d, following) => t < d.fire ? 'first candle forming' : t >= d.close ? 'day closed out at ' + d.flat_by
    : following ? 'live · alarms running' : 'alarms running';

  // A Telegram message of the day as a chat bubble. The text is the message as sent (Telegram HTML: <b> only, already
  // escaped); its last line "… · scanradar.tech" is dropped because the bubble shows the time itself.
  const KIND = { summary: 'sum', result: 'sum', target: 'bt', stop: 'bs', near: '' };
  function bubble(m, origin) {
    const lines = String(m.text || '').split('\n');
    if (lines.length > 1 && /scanradar\.tech\s*$/.test(lines[lines.length - 1])) lines.pop();
    let html = lines.join('<br>');
    if (m.quiet) html = html.replace('</b>', '</b><span class="q">quiet</span>');
    const d = m.delivery;
    const mark = d ? (d.ok ? 'sent ' + (d.at || '') : 'failed: ' + esc(d.error || 'unknown')) : origin === 'live' ? 'not recorded' : origin ? 'replay' : '';
    return { cls: KIND[m.kind] || '', html: html + `<span class="tm">${esc(m.at)}${mark ? ' · ' + mark.trim() : ''}</span>` };
  }

  const comboKey = F => F.star && F.mkt ? 'both' : F.star ? 'star' : F.mkt ? 'mkt' : 'all';
  function recNote(key, rec, cost) {
    const tail = ` Costs counted at ${cost.toFixed(2)} % a trade. A replay of stored sessions, not a forecast.`;
    if (key === 'all') return `Between ${rec.min} and ${rec.max} alarms a morning, all at 09:20.` + (rec.gross == null ? '' : ` Before costs ${sg(rec.gross)} % a trade.`) + tail;
    if (key === 'mkt') return 'Only the side the market leaned to at 09:20; nothing on a mixed morning.' + tail;
    if (key === 'star') return 'The three that closed furthest from VWAP on each side.' + tail;
    return 'The strongest three of the side the market leaned to; nothing on a mixed morning.' + tail;
  }

  const shown = (r, F) => (!F.star || r.star) && (!F.mkt || r.mkt === 'with');

  root.ScanInside = { END, esc, sg, hhmm, mins, prep, stateAt, edge, tiles, pill, phase, bubble, comboKey, recNote, shown };
})(typeof window !== 'undefined' ? window : globalThis);
