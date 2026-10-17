/* View filter for the dashboard: narrows which ranked rows and alerts are shown.
   Pure functions, no DOM, so the node tests in tests/web/ can exercise them.
   Nothing here changes the ranking, the engine, or Telegram alerts. */
(function (root) {
  const DEFAULTS = Object.freeze({ sectors: [], pmin: null, pmax: null, rvol: null, oi: 'any', vwap: 'any', picks: false, rr: null });
  const num = v => (v === null || v === undefined || v === '' || isNaN(+v)) ? null : +v;

  function defaults() { return { ...DEFAULTS, sectors: [] }; }

  function isActive(f) {
    return f.sectors.length > 0 || f.pmin != null || f.pmax != null || f.rvol != null || f.oi !== 'any' || f.vwap !== 'any' || !!f.picks || f.rr != null;
  }

  function pass(row, f) {
    if (f.sectors.length && !f.sectors.includes(row.sector)) return false;
    if (f.pmin != null && row.price < f.pmin) return false;
    if (f.pmax != null && row.price > f.pmax) return false;
    if (f.rvol != null && row.rvol < f.rvol) return false;
    if (f.rr != null && row.rr < f.rr) return false;
    if (f.picks && !row.picked) return false;
    if (f.oi === 'buildup' && !((row.dir > 0 && row.oi_cls === 'lb') || (row.dir < 0 && row.oi_cls === 'sb'))) return false;
    if (f.vwap === 'aligned' && !((row.dir > 0 && row.above_vwap) || (row.dir < 0 && !row.above_vwap))) return false;
    return true;
  }

  // alerts: a ranked row (by symbol) gives every field; otherwise only sector and price are known
  function passAlert(alert, rowsBySymbol, sectorsOf, f) {
    const row = rowsBySymbol[alert.symbol];
    if (row) return pass(row, f);
    const sector = sectorsOf[alert.symbol];
    if (f.sectors.length && !f.sectors.includes(sector)) return false;
    if (f.pmin != null && alert.price != null && alert.price < f.pmin) return false;
    if (f.pmax != null && alert.price != null && alert.price > f.pmax) return false;
    return true;
  }

  function save(f) { return JSON.stringify(f); }

  function load(raw) {
    try {
      const o = JSON.parse(raw);
      if (!o || typeof o !== 'object') return defaults();
      return {
        sectors: Array.isArray(o.sectors) ? o.sectors.filter(s => typeof s === 'string') : [],
        pmin: num(o.pmin), pmax: num(o.pmax), rvol: num(o.rvol), rr: num(o.rr),
        oi: o.oi === 'buildup' ? 'buildup' : 'any',
        vwap: o.vwap === 'aligned' ? 'aligned' : 'any',
        picks: !!o.picks,
      };
    } catch (e) { return defaults(); }
  }

  root.ScanFilter = { defaults, isActive, pass, passAlert, save, load };
})(typeof window !== 'undefined' ? window : globalThis);
