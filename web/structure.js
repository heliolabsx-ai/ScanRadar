/* Market structure and chart-view helpers: pure functions (tested in node). Rendering lives in index.html.
   Swings are fractals of strength k on 5-minute bars; a 5m close through the last unbroken swing is a
   BOS (with the trend) or a CHoCH (against it). */
(function (root) {
  const MIN0 = 9 * 60 + 15;
  const minOf = ts => { const m = /[T ](\d\d):(\d\d)/.exec(String(ts || '')); return m ? (+m[1]) * 60 + (+m[2]) - MIN0 : 0; };
  const dayOf = ts => String(ts || '').slice(0, 10);

  // 1-minute candles (any number of sessions, in time order; `stub` = the forming as-of bar) → bars of `tf` minutes,
  // anchored at 09:15 per session. Each bar: day, slot, m (start minute), o h l c v oi, n (minutes seen), i (index), stub.
  function resample(mins, tf) {
    const out = []; let cur = null;
    (mins || []).forEach(b => {
      const day = b.day || dayOf(b.ts), m = b.m != null ? b.m : minOf(b.ts), slot = Math.floor(m / tf);
      if (!cur || cur.day !== day || cur.slot !== slot) {
        cur = { day, slot, m: slot * tf, o: b.o, h: b.h, l: b.l, c: b.c, v: b.v || 0, oi: b.oi, n: 1, i: out.length, stub: !!b.stub };
        out.push(cur);
      } else {
        cur.h = Math.max(cur.h, b.h); cur.l = Math.min(cur.l, b.l); cur.c = b.c; cur.v += b.v || 0; cur.oi = b.oi != null ? b.oi : cur.oi; cur.n++; cur.stub = !!b.stub;
      }
    });
    return out;
  }

  // Fractal swings of strength k, confirmed k bars after the swing; BOS / CHoCH on a close through the last
  // unbroken swing. The forming bar (stub) never confirms a swing and never breaks a level.
  function structure(bars, k) {
    const sw = [], ev = []; let lastH = null, lastL = null, trend = null, prevH = null, prevL = null;
    const n = (bars || []).length;
    const isH = j => { for (let d = 1; d <= k; d++) if (bars[j].h <= bars[j - d].h || bars[j].h <= bars[j + d].h) return false; return true; };
    const isL = j => { for (let d = 1; d <= k; d++) if (bars[j].l >= bars[j - d].l || bars[j].l >= bars[j + d].l) return false; return true; };
    for (let i = 0; i < n; i++) {
      if (bars[i].stub) break;
      const j = i - k;
      if (j >= k) {
        if (isH(j)) { const s = { i: j, px: bars[j].h, t: 'H', at: i, tag: prevH == null ? 'H' : bars[j].h > prevH ? 'HH' : 'LH', broken: false }; prevH = bars[j].h; sw.push(s); lastH = s; }
        if (isL(j)) { const s = { i: j, px: bars[j].l, t: 'L', at: i, tag: prevL == null ? 'L' : bars[j].l < prevL ? 'LL' : 'HL', broken: false }; prevL = bars[j].l; sw.push(s); lastL = s; }
      }
      const c = bars[i].c;
      if (lastH && !lastH.broken && c > lastH.px) { ev.push({ i, from: lastH.i, px: lastH.px, dir: 'up', kind: trend === 'down' ? 'CHoCH' : 'BOS' }); lastH.broken = true; trend = 'up'; }
      if (lastL && !lastL.broken && c < lastL.px) { ev.push({ i, from: lastL.i, px: lastL.px, dir: 'down', kind: trend === 'up' ? 'CHoCH' : 'BOS' }); lastL.broken = true; trend = 'down'; }
    }
    return { sw, ev, trend, upLevel: lastH && !lastH.broken ? lastH.px : null, dnLevel: lastL && !lastL.broken ? lastL.px : null,
             upSwing: lastH && !lastH.broken ? lastH : null, dnSwing: lastL && !lastL.broken ? lastL : null };
  }

  // The index in `bars` (any timeframe) of the bar holding 5m bar j's start minute (or its last minute with `end`).
  function mapIndex(bars, b5, j, end, tfOverride) {
    const b = b5[j]; if (!b) return -1;
    const tf = tfOverride || (bars.length > 1 && bars[0].day === bars[1].day ? Math.max(1, bars[1].m - bars[0].m) : 1);
    const target = Math.floor((b.m + (end ? 4 : 0)) / tf);
    let k = -1;
    for (let q = 0; q < bars.length; q++) {
      if (bars[q].day === b.day && bars[q].slot <= target) k = q;
      else if (bars[q].day > b.day) break;
    }
    return k;
  }

  function niceStep(x) {
    if (!(x > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(x))), f = x / p;
    return (f < 1.5 ? 1 : f < 3.5 ? 2.5 : f < 7.5 ? 5 : 10) * p;
  }

  // Visible window in bar units. n = bars loaded, i0 = index of the first bar of the first shown session.
  function fitView(n, i0, perDay, days, tf, full) {
    const a = i0 < 0 ? 0 : i0, b = Math.max(a + perDay * days, n);
    if (tf === 1 && days === 1 && !full) { const e = Math.max(n, a + 120); return { a: e - 120.5, b: e + 0.5 }; }
    return { a: a - 0.5, b: b + 0.5 };
  }

  // Minutes between time-axis labels so they sit at least 64 px apart.
  function axisEvery(tf, slot) { return [15, 30, 60, 120, 180, 375].find(e => e / tf * slot >= 64) || 375; }

  function positionWords(px, vwap, pdh, pdl, trend) {
    const w = [];
    if (px == null) return w;
    if (vwap != null) w.push(px >= vwap ? 'above VWAP' : 'below VWAP');
    if (pdh != null && pdl != null) w.push(px > pdh ? 'above PDH' : px < pdl ? 'below PDL' : 'inside yesterday');
    if (trend) w.push(`structure ${trend === 'up' ? 'bullish' : 'bearish'}`);
    return w;
  }

  root.ScanStructure = { resample, structure, mapIndex, niceStep, fitView, axisEvery, positionWords, minOf, dayOf };
})(typeof globalThis !== 'undefined' ? globalThis : this);
