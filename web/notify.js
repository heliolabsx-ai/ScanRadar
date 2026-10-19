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
  // Sound: 'system' lets macOS play the alert sound chosen in System Settings > Notifications (an Apple tune,
  // e.g. Glass or Tink); 'chime' is a soft two-note synthesized chime; 'none' is silent.
  let tone = 'system';
  try { tone = root.localStorage && root.localStorage.getItem('notifyTone') || 'system'; } catch (e) { /* no storage */ }
  function setTone(t) { tone = ['system', 'chime', 'none'].includes(t) ? t : 'system'; try { root.localStorage.setItem('notifyTone', tone); } catch (e) { /* no storage */ } }
  function getTone() { return tone; }
  function chime() {
    try {
      const ac = new (root.AudioContext || root.webkitAudioContext)();
      const t0 = ac.currentTime;
      [[1318.5, 0], [1760, 0.12]].forEach(([f, dt]) => {          // E6 then A6, a glassy fourth
        const o = ac.createOscillator(), gn = ac.createGain();
        o.type = 'sine'; o.frequency.value = f;
        gn.gain.setValueAtTime(0.0001, t0 + dt); gn.gain.exponentialRampToValueAtTime(0.09, t0 + dt + 0.01);
        gn.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 0.7);
        o.connect(gn); gn.connect(ac.destination); o.start(t0 + dt); o.stop(t0 + dt + 0.75);
      });
      setTimeout(() => ac.close(), 1200);
    } catch (e) { /* no audio */ }
  }
  const beep = chime;
  function show(msg, onClick) {
    if (!hasDom || Notification.permission !== 'granted') return null;
    const n = new Notification(msg.title, { body: msg.body, tag: msg.title + msg.body, silent: tone !== 'system' });
    n.onclick = () => { try { root.focus(); } catch (e) {} if (onClick) onClick(msg); n.close(); };
    if (tone === 'chime') chime();
    return n;
  }

  root.ScanNotify = { newAlerts, newPicks, alertMessage, pickMessage, permission, request, show, beep, chime, setTone, getTone };
})(typeof window !== 'undefined' ? window : globalThis);
