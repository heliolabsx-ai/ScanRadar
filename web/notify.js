/* Desktop notifications for the dashboard: pure helpers (tested in node) plus a thin
   browser layer. Fires only on the live feed: new rule alerts and symbols newly
   entering the top-3 picks. Nothing here touches the server. */
(function (root) {
  const key = a => `${a.ts.slice(0, 16)}|${a.scan}|${a.symbol}`;
  const hhmm = ts => ts.slice(11, 16);
  const inr = n => '₹' + Number(n).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // rows newest-first; marks everything seen; returns the unseen ones unless seeding
  function newAlerts(seen, rows, opts = {}) {
    const fresh = [];
    for (const a of rows) {
      const k = key(a);
      if (!seen.has(k)) { seen.add(k); if (!opts.seed) fresh.push(a); }
    }
    return fresh;
  }

  function newPicks(seen, snap, opts = {}) {
    const fresh = [];
    for (const [side, list] of [['bull', snap.bulls || []], ['bear', snap.bears || []]]) {
      for (const r of list) {
        if (!r.picked) continue;
        const k = `${side}|${r.symbol}`;
        if (!seen.has(k)) { seen.add(k); if (!opts.seed) fresh.push({ ...r, side }); }
      }
    }
    return fresh;
  }

  function alertMessage(a) {
    const side = a.side === 'short' ? 'SELL' : a.side === 'long' ? 'BUY' : '';
    return { title: `ScanRadar · ${a.scan}`, body: [a.symbol, side, a.price != null ? inr(a.price) : ''].filter(Boolean).join('  ') + `  ·  ${hhmm(a.ts)}`, symbol: a.symbol, at: hhmm(a.ts) };
  }

  function pickMessage(r, at) {
    const pct = (r.pct >= 0 ? '+' : '') + Number(r.pct).toFixed(2) + '%';
    return { title: 'ScanRadar · new pick', body: `${r.symbol}  ${r.side.toUpperCase()}  ${pct}  ·  ${r.oi_label}  ·  ${at}`, symbol: r.symbol, at };
  }

  // ---- browser layer (no-ops under node)
  const hasDom = typeof document !== 'undefined' && typeof Notification !== 'undefined';
  function permission() { return hasDom ? Notification.permission : 'unsupported'; }
  async function request() { return hasDom ? Notification.requestPermission() : 'unsupported'; }
  function beep() {
    try {
      const ac = new (root.AudioContext || root.webkitAudioContext)();
      const o = ac.createOscillator(), gn = ac.createGain();
      o.type = 'sine'; o.frequency.value = 880; gn.gain.value = 0.08;
      o.connect(gn); gn.connect(ac.destination); o.start(); o.stop(ac.currentTime + 0.18);
      setTimeout(() => ac.close(), 400);
    } catch (e) { /* no audio */ }
  }
  function show(msg, onClick) {
    if (!hasDom || Notification.permission !== 'granted') return null;
    const n = new Notification(msg.title, { body: msg.body, tag: msg.title + msg.body, silent: true });
    n.onclick = () => { try { root.focus(); } catch (e) {} if (onClick) onClick(msg); n.close(); };
    beep();
    return n;
  }

  root.ScanNotify = { newAlerts, newPicks, alertMessage, pickMessage, permission, request, show, beep };
})(typeof window !== 'undefined' ? window : globalThis);
