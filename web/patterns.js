/* Patterns tab helpers: pure functions (tested in node). Rendering lives in index.html. */
(function (root) {
  const LABEL = { double_bottom: 'Double bottom', triple_bottom: 'Triple bottom', double_top: 'Double top', triple_top: 'Triple top',
    inv_head_shoulders: 'Inverse head & shoulders', head_shoulders: 'Head & shoulders', bull_flag: 'Bull flag', bear_flag: 'Bear flag',
    range_break_long: 'Range breakout', range_break_short: 'Range breakdown', asc_triangle: 'Ascending triangle', desc_triangle: 'Descending triangle',
    orb_long: 'Opening range break up', orb_short: 'Opening range break down' };
  const FAMILY = k => ['bull_flag', 'bear_flag', 'range_break_long', 'range_break_short', 'asc_triangle', 'desc_triangle', 'orb_long', 'orb_short'].includes(k) ? 'continuation' : 'reversal';
  const confLabel = c => !c || c.hit_rate == null ? '—' : `${Math.round(c.hit_rate)}%${c.n != null ? ' · ' + c.n : ''}`;
  const fmt2 = n => Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const kindLabel = k => LABEL[k] || k;

  function actionLabel(r) {
    const side = r.side === 'long' ? 'BUY' : 'SELL';
    if (r.state === 'failed') return '(failed)';
    if (r.state === 'triggered') return r.age_min === 0 ? side + ' NOW' : `${side} · triggered ${String(r.triggered_at).slice(11, 16)}`;
    return `${side} if ${fmt2(r.neckline)} closes`;
  }

  function ageLabel(min, triggeredAt) {
    let s = min <= 0 ? 'now' : min < 60 ? `${min}m` : `${Math.floor(min / 60)}h ${min % 60}m`;
    if (triggeredAt && String(triggeredAt).slice(11, 16) >= '14:45') s += ' · late';
    return s;
  }

  // quick: all | bottoms | tops | triggered | oi | rr15 ; vf: the Scanner's view filter (sector + price apply)
  function filterRows(rows, quick, vf, sectorsOf) {
    const F = root.ScanFilter;
    return rows.filter(r => {
      if (quick === 'bottoms' && r.side !== 'long') return false;
      if (quick === 'tops' && r.side !== 'short') return false;
      if (quick === 'reversal' && FAMILY(r.kind) !== 'reversal') return false;
      if (quick === 'continuation' && FAMILY(r.kind) !== 'continuation') return false;
      if (quick === 'triggered' && r.state !== 'triggered') return false;
      if (quick === 'oi' && !(r.checks && r.checks[1])) return false;
      if (quick === 'rr15' && !(r.rr >= 1.5)) return false;
      if (!F || !vf) return true;
      return F.passAlert({ symbol: r.symbol, price: r.entry != null ? r.entry : r.neckline }, {}, sectorsOf || {}, vf);
    });
  }

  function sortRows(rows) {
    const rank = { triggered: 0, forming: 1, retest: 2, failed: 3 };
    return [...rows].sort((a, b) => {
      const d = rank[a.state] - rank[b.state];
      if (d) return d;
      if (a.state === 'triggered') return String(b.triggered_at).localeCompare(String(a.triggered_at));
      if (a.state === 'forming' || a.state === 'retest') return (b.score - a.score) || (a.age_min - b.age_min);
      return String(b.failed_at || '').localeCompare(String(a.failed_at || ''));
    });
  }

  root.ScanPatterns = { kindLabel, actionLabel, ageLabel, filterRows, sortRows, LABEL, FAMILY, confLabel };
})(typeof window !== 'undefined' ? window : globalThis);
