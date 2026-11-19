// session expired / signed out → back to the login page (covers chart.js too)
{ const f = window.fetch.bind(window);
  window.fetch = async (...a) => { const r = await f(...a); if (r.status === 401) location.href = '/login'; return r; }; }
/* SPY/QQQ desk — read-only dashboard. Polls /api/status every 15 s, /api/ledger + /api/summary
   every 30 s, /api/reports every 5 min. All rendering is from those three payloads. */
(() => {
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = (v, d = 0) => (v < 0 ? '−$' : '$') + Math.abs(+v || 0).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const signed = (v, d = 0) => (v > 0 ? '+' : v < 0 ? '−' : '') + '$' + Math.abs(+v || 0).toLocaleString(undefined, {minimumFractionDigits: d, maximumFractionDigits: d});
const pct = (v, d = 0) => (v > 0 ? '+' : '') + (+v).toFixed(d) + '%';
const num = (v, d = 2) => v == null ? '—' : (+v).toFixed(d);
const hhmm = ts => ts ? ts.slice(11, 16) : '—';
const GATE_LABEL = {entry_window: 'Window', signal_fresh: 'Fresh', event_blackout: 'Events', confidence: 'Confidence', cooldown: 'Cooldown',
  same_direction: 'Correlation', fill_quality: 'Fill quality', drawdown: 'Drawdown', vol_edge: 'Vol edge', chain: 'Chain', dte: 'DTE',
  liquidity: 'Liquidity', spread_cost: 'Spread cost', spread_ratio: 'Pricing', day_risk: 'Budget', debate: 'Debate'};
const GATE_ORDER = ['entry_window','signal_fresh','event_blackout','confidence','cooldown','same_direction','fill_quality','drawdown','vol_edge','dte','liquidity','spread_cost','spread_ratio','day_risk','debate'];
const SETUP_LABEL = {trend_pullback: 'Trend pullback', vwap_momentum: 'VWAP momentum'};
const SHORT = {trend_pullback: 'Pullback', vwap_momentum: 'VWAP mom.'};

let S = null;            // last /api/status
let SUM = null;          // last /api/summary
const OPEN = (() => { try { return JSON.parse(localStorage.getItem('open-off') || '{}'); } catch (e) { return {}; } })();
document.addEventListener('toggle', e => {
  const d = e.target;
  if (d.matches && d.matches('details.offset')) { OPEN[d.dataset.k] = d.open; try { localStorage.setItem('open-off', JSON.stringify(OPEN)); } catch (_) {} }
}, true);
const offDetails = (k, label) => `<details class="offset" data-k="${k}" ${OPEN[k] ? 'open' : ''}><summary>${label}</summary><div class="offset-body">`;
let WHY = {};           // /api/why/<sym> — per-condition checklist when nothing fired
let EXTRA = null;        // /api/history_extra — confidence for setups the engine isn't trading
let actFilter = 'all';
let actDay = null;       // YYYY-MM-DD shown in Activity (null = today ET)

// ── time helpers (everything on the page is ET) ──────────────────────────────
function etParts(d = new Date()) {
  const p = new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short'}).formatToParts(d);
  const g = t => p.find(x => x.type === t)?.value;
  return {h: +g('hour') % 24, m: +g('minute'), s: +g('second'), date: `${g('year')}-${g('month')}-${g('day')}`, wd: g('weekday')};
}
const parseTs = s => s ? new Date(String(s).replace(' ', 'T')) : null;
function dur(ms) {
  if (ms == null || !isFinite(ms)) return '—';
  ms = Math.max(0, ms); const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), ss = s % 60;
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m ${String(ss).padStart(2, '0')}s`;
  return `${m}m ${String(ss).padStart(2, '0')}s`;
}
const hm = s => { const [h, m] = (s || '0:0').split(':').map(Number); return h * 60 + m; };

// Session phase from the status payload + the wall clock.
function phase() {
  if (!S) return null;
  const L = S.limits || {}, now = etParts(), mins = now.h * 60 + now.m + now.s / 60;
  const nextClose = parseTs(S.next_close), nextOpen = parseTs(S.next_open);
  const atET = (hhmmStr, base) => { // same calendar day as `base` (an ET-offset ISO string), at hh:mm ET
    const m = String(base || '').match(/^(\d{4}-\d{2}-\d{2})[ T].*?([+-]\d{2}:\d{2})$/); if (!m) return null;
    return new Date(`${m[1]}T${hhmmStr}:00${m[2]}`);
  };
  if (!S.market_open) {
    return {key: 'closed', eyebrow: 'Market closed', title: nextOpen ? `Opens ${nextOpen.toLocaleDateString(undefined, {weekday: 'long'})} 09:30 ET` : 'Opens next session',
            nextLabel: 'opens in', target: nextOpen, pos: null};
  }
  const es = hm(L.entry_start || '10:00'), ee = hm(L.entry_end || '11:30'), fl = hm(L.flatten || '15:45');
  const pos = Math.min(100, Math.max(0, (mins - 570) / (960 - 570) * 100));
  if (mins < es) return {key: 'pre', eyebrow: 'Market open · no entries yet', title: 'Waiting for the entry window', nextLabel: 'first scan in', target: atET(L.entry_start || '10:00', S.next_close), pos};
  if (mins < ee) return {key: 'entry', eyebrow: 'Entry window', title: `Scanning every ${L.scan_sec || 60}s for a setup`, nextLabel: 'window closes in', target: atET(L.entry_end || '11:30', S.next_close), pos};
  if (mins < fl) return {key: 'manage', eyebrow: 'Entries closed', title: S.open?.length ? `Managing ${S.open.length} open spread${S.open.length > 1 ? 's' : ''}` : 'Flat · watching until the close', nextLabel: 'flatten in', target: atET(L.flatten || '15:45', S.next_close), pos};
  return {key: 'flat', eyebrow: 'Flattening', title: 'Everything closes before the bell', nextLabel: 'bell in', target: nextClose, pos};
}

// ── header + phase ───────────────────────────────────────────────────────────
function renderTop() {
  const age = Date.now() / 1000 - (S?.epoch || 0);
  const set = (id, text, cls) => { const el = $(id); el.innerHTML = text; el.className = 'pill ' + (cls || ''); };
  if (!S) { set('#p-conn', '<span class="dot"></span> no status file', 'bad'); return; }
  set('#p-mode', (S.mode === 'paper' ? '🧪 PAPER' : '🧪 DRY RUN'), S.mode === 'paper' ? 'info' : 'warn');
  set('#p-conn', S.connected ? `<span class="dot live"></span> connected · ${esc(S.key_prefix)}…` : '<span class="dot"></span> DISCONNECTED', S.connected ? 'ok' : 'bad');
  set('#p-mkt', S.market_open ? '● market open' : '○ market closed', S.market_open ? 'ok' : '');
  $('#p-halt').hidden = !S.halted;
  const le = S.llm_error || '', pl = $('#p-llm');
  pl.hidden = !le;
  if (le) {
    const gate = S.limits?.debate_enabled;
    pl.textContent = /credit/i.test(le) ? '🤖 Claude credits out' : '🤖 Claude down';
    pl.className = 'pill ' + (gate ? 'bad' : 'warn');
    pl.title = le + (gate ? ' — the debate gate fails closed: no entries until this clears' : ' — debate gate is OFF; only the EOD review is unavailable');
  }
  set('#p-age', age > 120 ? `⚠ engine silent ${dur(age * 1000)}` : `engine ok · ${age.toFixed(0)}s`, age > 120 ? 'bad' : '');
  const ph = phase();
  $('#phase-eyebrow').textContent = ph.eyebrow;
  $('#phase-title').textContent = ph.title;
  $('#phase-next').textContent = ph.nextLabel;
  $('#phase-count').textContent = ph.target ? dur(ph.target - Date.now()) : '—';
  $('#timeline').classList.toggle('closed', ph.key === 'closed');
  const nowEl = $('#now'); nowEl.hidden = ph.pos == null; if (ph.pos != null) nowEl.style.left = ph.pos + '%';
}

// ── tiles ────────────────────────────────────────────────────────────────────
function renderTiles() {
  if (!S) return;
  const a = S.account || {}, d = S.day || {};
  $('#t-equity').textContent = a.equity != null ? money(a.equity) : '—';
  $('#t-cash').textContent = a.options_bp != null ? `options buying power ${money(a.options_bp)}` : (a.cash != null ? `cash ${money(a.cash)}` : '—');
  const r = +d.realized_usd || 0; const re = $('#t-real'); re.textContent = signed(r); re.className = 'v ' + (r > 0 ? 'up' : r < 0 ? 'down' : '');
  $('#t-real-sub').textContent = d.date ? `${d.date} · after fees` : 'after fees';
  const used = +d.debit_spent_usd || 0, bud = +d.budget_usd || 1;
  $('#t-budget').textContent = `${money(used)} / ${money(bud)}`;
  $('#m-budget').style.width = Math.min(100, used / bud * 100) + '%';
  $('#t-budget-sub').textContent = `${money(Math.max(0, bud - used))} left · max ${money(S.limits?.per_trade_max_usd || 0)} per spread`;
  $('#t-trades').textContent = `${d.trades ?? 0} / ${d.max_trades ?? '—'}`;
  $('#d-trades').innerHTML = Array.from({length: d.max_trades || 0}, (_, i) => `<i class="${i < (d.trades || 0) ? 'on' : ''}"></i>`).join('');
  $('#t-open-sub').textContent = `${S.open?.length || 0} open now · max ${S.limits?.max_open ?? '—'}`;
  const halt = +d.loss_halt_usd || 1, dist = Math.max(0, halt + Math.min(0, r));
  $('#t-halt').textContent = '−' + money(halt);
  $('#m-halt').style.width = Math.min(100, Math.max(0, -r) / halt * 100) + '%';
  $('#t-halt-sub').textContent = r < 0 ? `${money(dist)} of room before new entries stop` : 'no losses today · full room';
}


// ── "why aren't we buying?" — icons + sliders, words only in tooltips ───────
const clamp01 = x => Math.max(0, Math.min(1, x));
// slider: track with threshold tick (or band), fill from threshold to value, dot at value
function gauge(g, ok) {
  if (!g) return '<span class="gz"></span>';
  const pos = v => clamp01((v - g.lo) / (g.hi - g.lo)) * 100;
  const v = pos(g.v);
  let mid = '';
  if (g.want === 'band') {
    const [a, b] = g.t;
    mid = `<span class="gz-band" style="left:${pos(a)}%;width:${pos(b) - pos(a)}%"></span>`;
  } else {
    const t = pos(g.t);
    mid = `<span class="gz-fill" style="left:${Math.min(t, v)}%;width:${Math.abs(v - t)}%"></span><span class="gz-tick" style="left:${t}%"></span>`;
  }
  const off = g.v < g.lo || g.v > g.hi;
  return `<span class="gz ${ok ? 'ok' : 'no'}">${mid}<span class="gz-dot ${off ? 'edge' : ''}" style="left:${v}%"></span></span>`;
}
const pips = (n, of, cls = '') => `<span class="pips ${cls}" title="${n} of ${of}">${Array.from({length: of}, (_, i) => `<i class="${i < n ? 'on' : ''}"></i>`).join('')}</span>`;

function whyNoSignal(sym, s, L, ph) {
  const w = WHY[sym]?.live ? WHY[sym] : null;
  const _e = etParts(), nowHM = String(_e.h).padStart(2, '0') + ':' + String(_e.m).padStart(2, '0');
  const end = L.entry_end || '12:30', start = L.entry_start || '10:05';
  const chips = [];
  if (nowHM > end) chips.push(`<span class="wc no" title="Entry window ${start}–${end} ET is over — no new trades today; open spreads are still managed">⏱ closed ${end}</span>`);
  else if (nowHM < start) chips.push(`<span class="wc wait" title="Entries start at ${start} ET">⏱ opens ${start}</span>`);
  else chips.push(`<span class="wc ok" title="Inside the entry window ${start}–${end} ET">⏱ open</span>`);
  const tr = s.trend;
  chips.push(!tr || tr === 'flat' ? `<span class="wc" title="Daily trend flat (close within 0.1% of the 20-day SMA) — trend-following setups are off">→ flat</span>`
    : `<span class="wc ${tr}" title="20-day trend ${tr === 'bull' ? 'up — only calls' : 'down — only puts'}">${tr === 'bull' ? '▲ calls only' : '▼ puts only'}</span>`);
  if (w) {
    for (const b of w.setups.filter(x => x.traded)) for (const d of b.directions) {
      const miss = d.checks.filter(c => !c.ok).map(c => c.label).join(', ');
      chips.push(`<span class="wc" title="${esc(SETUP_LABEL[b.setup] || b.setup)} on the ${esc(w.bar)} bar: ${d.passed}/${d.total} conditions${miss ? ' — missing ' + esc(miss) : ''}">${esc(SHORT[b.setup] || b.setup)} ${pips(d.passed, d.total, d.passed === d.total ? 'full' : '')}</span>`);
    }
    if (w.last_fire) {
      const lf = w.last_fire, df = w.last_decided_fire, dec = w.last_decision;
      const gate = dec && !dec.accepted ? (GATE_LABEL[dec.gate] || dec.gate || 'gate') : null;
      const tip = `Fired ${w.fires_today}× today (${w.fires_in_window} in window), last at ${lf.et}; the engine decided on ${w.decided_today}` +
        (dec ? ` — latest: ${df.et} bar ${dec.accepted ? 'entered' : `blocked by ${gate}: ${dec.why || ''}`}` : '');
      chips.push(`<span class="wc" title="${esc(tip)}">⚡ ${w.fires_today}× · ${esc(lf.et)}</span>`);
      if (dec) chips.push(`<span class="wc ${dec.accepted ? 'ok' : 'no'}" title="${esc(tip)}">${dec.accepted ? '✓ entered' : '✗ ' + esc(gate)} · ${esc(df.et)}</span>`);
    } else chips.push('<span class="wc" title="The rule has not fired on any closed bar today">⚡ 0×</span>');
  }
  return `<span class="wcs">${chips.join('')}</span>`;
}

function checklist(sym) {
  const w = WHY[sym]; if (!w || !w.live || !w.setups.length) return '';
  const one = b => {
    if (b.blocked) return `<div class="ck off" title="${esc(b.blocked)}"><div class="ck-head"><span>${esc(SHORT[b.setup] || b.setup)}</span><span class="hint">trend flat</span></div></div>`;
    return b.directions.map(d => `<div class="ck ${b.traded ? '' : 'off'}">
      <div class="ck-head"><span>${esc(SHORT[b.setup] || b.setup)} <span class="arrow ${d.direction}">${d.direction === 'bull' ? '▲' : '▼'}</span></span>${pips(d.passed, d.total, d.passed === d.total ? 'full' : '')}</div>
      ${d.checks.map(c => `<div class="gr ${c.ok ? 'ok' : 'no'}" title="${esc(c.tip || c.label)}"><i>${c.ok ? '✓' : '✗'}</i><span class="gl">${esc(c.label)}</span>${gauge(c.g, c.ok)}<b>${esc(c.value)}</b></div>`).join('')}
    </div>`).join('');
  };
  const tr = w.setups.filter(b => b.traded), off = w.setups.filter(b => !b.traded);
  return `<div class="sect"><div class="eyebrow"><span>Conditions · ${esc(w.bar || '—')}</span></div><div class="cks">${tr.map(one).join('')}</div>${off.length ? offDetails('ck-' + sym, `Not traded (${off.length})`) + `<div class="cks">${off.map(one).join('')}</div></div></details>` : ''}</div>`;
}

// engine data freshness: newest closed bar the engine scanned vs the scan time.
// >10 min means it fell back to delayed SIP bars (or slept) — signals would act late.
function freshChip(s) {
  if (!s?.ts || !s.bars) return '<span class="meta"></span>';
  const [h, m] = hhmm(s.ts).split(':').map(Number);
  const closeMin = 9 * 60 + 30 + s.bars * 5;                       // close of the newest bar, ET minutes
  const lag = Math.max(0, h * 60 + m - closeMin);
  const cls = lag > 20 ? 'no' : lag > 10 ? 'wait' : 'ok';
  const bh = String(Math.floor((closeMin - 5) / 60)).padStart(2, '0') + ':' + String((closeMin - 5) % 60).padStart(2, '0');
  return `<span class="wc ${cls} meta-chip" title="Engine scanned at ${hhmm(s.ts)} ET; newest closed bar ${bh} (${s.bars} bars). Data lag ${lag} min${lag > 10 ? ' — late data: signals act late' : ''}.">● ${hhmm(s.ts)} · ${lag}m</span>`;
}

// ── symbol card ──────────────────────────────────────────────────────────────
function confidenceRows(sym, hist, sig, L) {
  const traded = L.setups || Object.keys(hist || {});
  const off = (EXTRA?.disabled || []).filter(st => !traded.includes(st));
  const setups = [...traded, ...off];
  const minC = L.min_confidence ?? 0.35, minN = L.min_history_n ?? 10;
  const p0 = v => v == null ? '—' : (v * 100).toFixed(0) + '%';
  let h = '', opened = false;
  for (const st of setups) for (const dir of ['bull', 'bear']) {
    const isOff = off.includes(st);
    if (isOff && !opened) { h += offDetails('conf-' + sym, `Not traded (${off.length})`); opened = true; }
    const hr = isOff ? EXTRA?.history?.[sym]?.[st]?.[dir] : hist?.[st]?.[dir];
    const active = sig && sig.setup === st && sig.direction === dir;
    const name = `${esc(SHORT[st] || st)} <span class="arrow ${dir}">${dir === 'bull' ? '▲' : '▼'}</span>`;
    if (!hr || hr.n == null) {
      h += `<div class="cr ${isOff ? 'off' : ''}"><span class="lab">${name}</span><span class="track"></span><span class="hint">${isOff && !EXTRA?.ready ? '…' : '—'}</span></div>`;
      continue;
    }
    const lo = (hr.lower95 ?? 0) * 100, hi = (hr.upper95 ?? 0) * 100, rate = (hr.rate ?? 0) * 100;
    const enoughN = hr.n >= minN, pass = enoughN && (hr.lower95 ?? 0) >= minC;
    const unproven = (hr.lower95 ?? 0) < 0.5;
    const mv = hr.avg_move_pct;
    const tip = `${SETUP_LABEL[st] || st} ${dir === 'bull' ? 'calls' : 'puts'}${isOff ? ' (not traded)' : ''}
Hit rate ${rate.toFixed(1)}% — ${hr.hits}/${hr.n} past fires reached +0.30% first
95% range ${p0(hr.lower95)}–${p0(hr.upper95)}${unproven ? ' (includes 50%: edge not proven)' : ''}
Gate: ${p0(hr.lower95)} vs ${(minC * 100).toFixed(0)}% floor → ${pass ? 'pass' : 'fail'}${enoughN ? '' : ` (n < ${minN})`}
Avg move ${mv == null ? '—' : (mv >= 0 ? '+' : '') + mv.toFixed(3) + '%'}`;
    h += `<div class="cr ${active ? 'active' : ''} ${isOff ? 'off' : ''}" title="${esc(tip)}">
      <span class="lab">${active ? '● ' : ''}${name}</span>
      <span class="track">
        <span class="mark floor" style="left:${minC * 100}%"></span>
        <span class="mark half" style="left:50%"></span>
        <span class="range ${dir} ${pass ? '' : 'weak'}" style="left:${lo}%;width:${Math.max(1, hi - lo)}%"></span>
        <span class="pt" style="left:${rate}%"></span></span>
      <b class="rate">${rate.toFixed(0)}%</b>
      <span class="cn">${hr.n}</span>
      <span class="mv ${(mv ?? 0) >= 0 ? 'up' : 'down'}">${mv == null ? '—' : (mv >= 0 ? '+' : '') + mv.toFixed(2) + '%'}</span>
      <span class="ic ${pass ? 'ok' : 'no'}">${pass ? '✓' : '✗'}${unproven ? '<sup>⚠</sup>' : ''}</span>
    </div>`;
  }
  if (opened) h += '</div></details>';
  return h;
}

function symCard(sym) {
  const el = $('#sym-' + sym); if (!el) return;
  const s = S?.signals?.[sym], hist = S?.history?.[sym] || s?.history, L = S?.limits || {};
  const sig = s?.signal, ph = phase();
  const ind = s?.indicators || {};
  el.className = 'card sym ' + (sig ? sig.direction : '');
  const today = etParts().date;
  const stale = s && s.ts && s.ts.slice(0, 10) !== today;

  // 1. signal block
  let sigCls = 'muted', badge = '', big = '', why = '';
  if (!s || stale) {
    big = ph?.key === 'closed' ? 'Market closed' : ph?.key === 'pre' ? 'Waiting for 10:00 ET' : 'No scan yet';
    why = ph?.key === 'closed' ? `Next scan ${S?.next_open ? parseTs(S.next_open).toLocaleDateString(undefined, {weekday: 'long'}) : ''} at ${L.entry_start || '10:00'} ET.` : `First scan at ${L.entry_start || '10:00'} ET, then every ${L.scan_sec || 60}s until ${L.entry_end || '11:30'}.`;
    if (stale) why = `Last scan ${s.ts.slice(0, 10)} ${hhmm(s.ts)} ET. ` + why;
  } else if (!sig) {
    big = 'No signal'; badge = '';
    why = whyNoSignal(sym, s, L, ph);
  } else {
    const isBull = sig.direction === 'bull';
    sigCls = sig.direction;
    big = `${isBull ? '▲' : '▼'} ${isBull ? 'BUY CALL SPREAD' : 'BUY PUT SPREAD'}`;
    const firstFail = (s.gates || []).find(g => !g.ok);
    if (s.accepted) { badge = '<span class="badge go">Entered</span>'; sigCls += ' accepted'; why = `All gates passed · ${esc(SETUP_LABEL[sig.setup] || sig.setup)}.`; }
    else if (s.observe) { badge = '<span class="badge obs">Outside window</span>'; why = `${esc(SETUP_LABEL[sig.setup] || sig.setup)} fired, but entries are only taken ${L.entry_start}–${L.entry_end} ET. Shown for information.`; }
    else if (firstFail) { badge = '<span class="badge no">Refused</span>'; why = `Stopped at <b>${esc(GATE_LABEL[firstFail.gate] || firstFail.gate)}</b>: ${esc(firstFail.text)}`; }
    else { badge = '<span class="badge wait">Evaluating</span>'; why = esc(SETUP_LABEL[sig.setup] || sig.setup); }
  }
  const alsoTxt = sig?.also?.length ? ` · also ${sig.also.map(x => x.replace(':', ' ')).join(', ')}` : '';
  const strength = sig?.strength != null ? `<span class="chip ${sig.direction}">strength ${num(sig.strength, 2)}</span>` : '';

  // 2. indicators
  const rsi = ind.rsi14, rsiPos = rsi != null ? Math.min(100, Math.max(0, rsi)) : null;
  const gapCls = s?.gap_pct != null && Math.abs(s.gap_pct) >= (L.gap_delay_pct ?? 1) ? 'warn' : 'info';
  const trendChip = s ? `<span class="chip ${s.trend === 'bull' ? 'bull' : s.trend === 'bear' ? 'bear' : ''}">20d trend ${s.trend === 'bull' ? '▲ up' : s.trend === 'bear' ? '▼ down' : '— flat'}</span>` : '';
  const gapChip = s?.gap_pct != null ? `<span class="chip ${gapCls}" title="≥${L.gap_delay_pct ?? 1}% gap delays entries to ${L.gap_delay_start || '10:30'}">gap ${pct(s.gap_pct, 2)}</span>` : '';

  // 3. gates (evaluated ones in order, un-reached ones dimmed)
  let gatesHtml = '';
  if (sig && !s.observe) {
    const seen = new Map((s.gates || []).map(g => [g.gate, g]));
    const names = [...GATE_ORDER.filter(n => seen.has(n) || true)];
    const firstFailIdx = (s.gates || []).findIndex(g => !g.ok);
    gatesHtml = '<div class="gates">' + names.filter(n => n !== 'debate' || L.debate_enabled || seen.has('debate')).map(n => {
      const g = seen.get(n);
      if (!g) return `<span class="gate todo" title="not reached">${esc(GATE_LABEL[n] || n)}</span>`;
      return `<span class="gate ${g.ok ? 'ok' : 'fail'}" title="${esc(g.text)}">${g.ok ? '✓' : '✗'} ${esc(GATE_LABEL[n] || n)}</span>`;
    }).join('') + '</div>';
    if (s.gates?.length) gatesHtml += `<details class="more"><summary>Every gate sentence (${s.gates.length})</summary><ul>${s.gates.map(g => `<li class="${g.ok ? '' : 'fail'}">${g.ok ? '✓' : '✗'} ${esc(g.text)}</li>`).join('')}</ul></details>`;
    void firstFailIdx;
  }
  const cand = s?.candidate ? `<div class="sect"><div class="eyebrow">Contract</div><div class="cand">
      <b>${esc(cand_label(s.candidate))}</b><span class="chip">${s.candidate.expiry} · ${s.candidate.dte} DTE</span>
      <span class="chip">debit ${num(s.candidate.net_debit)}</span><span class="chip">mid ${num(s.candidate.net_mid)}</span>
      <span class="chip">width ${s.candidate.width}</span></div></div>` : '';
  const debate = s?.debate ? `<div class="callout ${s.debate.proceed ? 'ok' : ''}" style="margin-top:8px"><b>Debate</b> (${esc(L.debate_model)}): ${s.debate.proceed ? 'PROCEED' : 'SUPPRESS'} · ${(s.debate.confidence * 100).toFixed(0)}% — ${esc(s.debate.reason)}</div>` : '';

  el.innerHTML = `
    <div class="sym-head"><span class="ticker">${sym}</span><span class="price">${s?.price != null ? num(s.price) : '—'}</span>${trendChip}${gapChip}
      ${freshChip(s)}</div>
    <div class="signal ${sigCls}"><div class="big">${big} ${badge} ${strength}</div><div class="why">${why}${esc(alsoTxt)}</div></div>
    ${!sig && s && !stale ? checklist(sym) : ''}
    <div class="ind">
      <span><span class="k">VWAP</span><b>${num(ind.vwap)}</b> <span class="${s?.price != null && ind.vwap != null ? (s.price >= ind.vwap ? 'up' : 'down') : ''}">${s?.price != null && ind.vwap != null ? pct((s.price - ind.vwap) / ind.vwap * 100, 2) : ''}</span></span>
      <span><span class="k">EMA9</span><b>${num(ind.ema9)}</b></span>
      <span><span class="k">RSI14</span><b>${rsi != null ? num(rsi, 1) : '—'}</b>${rsiPos != null ? `<span class="rsi" title="no-chase band 40–65"><i style="left:${rsiPos}%"></i></span>` : ''}</span>
    </div>
    <div class="sect"><div class="eyebrow"><span title="Bar = 95% range of the hit rate when this rule fired on past sessions (first touch of ±0.30% before 15:45). Dot = hit rate. The gate uses the bar's left edge. Not a win probability for the spread. Hover a row for details.">Confidence ⓘ</span><span class="cf-key" title="amber = gate floor ${((L.min_confidence ?? 0.35) * 100).toFixed(0)}% · grey = 50% coin flip · dot = hit rate"><i class="sw floor"></i>${((L.min_confidence ?? 0.35) * 100).toFixed(0)} <i class="sw half"></i>50 <i class="sw dot"></i></span></div>
      <div class="cr cr-head"><span></span><span></span><span title="hit rate">hit</span><span title="past fires (sample size)">n</span><span title="average underlying move in the signal's direction">move</span><span title="confidence gate">gate</span></div>
      <div class="conf">${confidenceRows(sym, hist, sig, L)}</div></div>
    ${sig && !s.observe ? `<div class="sect"><div class="eyebrow">Gate pipeline</div>${gatesHtml}</div>` : ''}
    ${cand}${debate}`;
}
function cand_label(c) { return `${c.sym} ${c.long_strike}/${c.short_strike} ${c.type}`; }

// ── open spreads ─────────────────────────────────────────────────────────────
function renderOpen() {
  const el = $('#open'); if (!S) { el.innerHTML = ''; return; }
  const rows = S.open || [], tp = (S.limits?.tp_pct ?? .2) * 100, sl = (S.limits?.sl_pct ?? .2) * 100;
  const nb = $('#n-open'); nb.hidden = !rows.length; nb.textContent = rows.length;
  if (!rows.length) { el.innerHTML = `<p class="empty"><strong>Flat.</strong> Nothing open; nothing is ever held overnight.</p>`; return; }
  el.innerHTML = rows.map(p => {
    const pnl = +p.pnl_pct || 0, pos = Math.min(100, Math.max(0, (pnl + sl) / (tp + sl) * 100));
    const stopPct = p.entry ? (p.stop - p.entry) / p.entry * 100 : -sl, stopPos = Math.min(100, Math.max(0, (stopPct + sl) / (tp + sl) * 100));
    const held = p.opened_at ? dur(Date.now() - parseTs(p.opened_at)) : '—';
    return `<div class="pos">
      <div><span class="ticker" style="font-size:20px">${esc(p.sym)}</span> <span class="chip ${p.direction}">${p.direction === 'bull' ? '▲ call spread' : '▼ put spread'}</span><div class="legs">${esc(SETUP_LABEL[p.setup] || p.setup)} · ×${p.qty}</div></div>
      <div class="legs" title="${esc(p.long)} / ${esc(p.short)}">long ${esc(p.long)}<br>short ${esc(p.short)}</div>
      <div><div class="pnl ${p.pnl_usd >= 0 ? 'up' : 'down'}" style="color:var(--${p.pnl_usd >= 0 ? 'bull' : 'bear'})">${signed(p.pnl_usd)}</div><div class="sub">${pct(pnl, 1)} · entry ${num(p.entry)} → mid ${num(p.mid)}</div></div>
      <div><div class="ladder" title="stop ${num(p.stop)} (tier ${p.tier}) · peak ${pct(p.peak_pct, 1)}"><span class="stop" style="left:${stopPos}%"></span><span class="cur" style="left:${pos}%"></span></div>
        <div class="ladder-lab"><span>−${sl}% stop</span><span>ratchet ${pct(stopPct, 0)} · t${p.tier}</span><span>+${tp}% target</span></div></div>
      <div class="sub">opened ${hhmm(p.opened_at)} ET<br>held ${held} · peak ${pct(p.peak_pct, 1)}</div>
    </div>`;
  }).join('');
}

// ── activity ─────────────────────────────────────────────────────────────────
function evRow(r) {
  let k = '', what = '';
  if (r.kind === 'decision') {
    const f = (r.gates || []).find(g => !g.ok);
    k = r.accepted ? '<span class="k accept">entered</span>' : '<span class="k reject">refused</span>';
    what = `<b>${esc(r.sym)} ${r.direction === 'bull' ? '▲ call' : '▼ put'} spread</b> · ${esc(SETUP_LABEL[r.setup] || r.setup)}` +
      (r.confidence?.label ? ` · <span class="hint">${esc(r.confidence.label)}</span>` : '') +
      (f ? `<br>${esc(GATE_LABEL[f.gate] || f.gate)}: ${esc(f.text)}` : (r.candidate ? `<br>${esc(cand_label(r.candidate))} · debit ${num(r.candidate.net_debit)} ×${r.qty}` : ''));
  } else if (r.kind === 'fill') {
    k = '<span class="k fill">filled</span>';
    what = `<b>${esc(r.sym)} ${r.direction === 'bull' ? '▲ call' : '▼ put'} spread</b> ×${r.qty} @ ${num(r.net_debit)} (mid ${num(r.expected_mid)}, slip ${num(r.slippage, 3)})<br><span class="hint">${esc(r.long_occ)} / ${esc(r.short_occ)}${r.dry ? ' · DRY' : ''}</span>`;
  } else if (r.kind === 'close') {
    const win = (r.pnl_usd || 0) >= 0;
    k = `<span class="k ${win ? 'win' : 'loss'}">${signed(r.pnl_usd || 0)}</span>`;
    what = `<b>${esc(r.sym)} closed</b> · ${esc(r.reason || '')}<br><span class="hint">${r.pnl_pct != null ? pct(r.pnl_pct, 1) : ''}${r.peak_pct != null ? ` · peak ${pct(r.peak_pct, 1)}` : ''}${r.held_min != null ? ` · held ${Math.round(r.held_min)} min` : ''}${r.fees_usd ? ` · fees ${money(r.fees_usd, 2)}` : ''}</span>`;
  } else if (r.kind === 'orphan') {
    k = '<span class="k orphan">orphan</span>'; what = `<b>${esc(r.sym)}</b> unknown leg at broker — flattened. ${esc(JSON.stringify(r.detail || ''))}`;
  } else {
    k = '<span class="k engine">engine</span>';
    const extra = Object.entries(r).filter(([key]) => !['ts','kind','mode','setup','sym','event'].includes(key)).map(([a, b]) => `${a}=${typeof b === 'number' ? b.toLocaleString() : esc(String(b))}`).join(' · ');
    what = `<b>${esc(r.event || '')}</b>${extra ? ` <span class="hint">${extra}</span>` : ''}`;
  }
  return `<div class="ev"><div class="t">${hhmm(r.ts)}</div><div>${k}</div><div class="what">${what}</div></div>`;
}
async function renderActivity() {
  const day = actDay || etParts().date; $('#act-day').value = day;
  let rows = [];
  try { rows = await (await fetch('/api/ledger?day=' + day)).json(); } catch (e) { rows = []; }
  const keep = r => actFilter === 'all' || (actFilter === 'decision' && r.kind === 'decision') || (actFilter === 'trade' && ['fill', 'close', 'orphan'].includes(r.kind)) || (actFilter === 'engine' && r.kind === 'engine');
  const list = rows.filter(keep).reverse();
  if (day === etParts().date) { const na = $('#n-activity'), n = rows.filter(r => r.kind !== 'engine').length; na.hidden = !n; na.textContent = n; }
  const el = $('#activity');
  if (!list.length) {
    el.innerHTML = `<p class="empty"><strong>Nothing ${actFilter === 'all' ? '' : 'in this filter '}on ${day}.</strong> ${day === etParts().date ? 'Decisions appear here from the first scan at ' + (S?.limits?.entry_start || '10:00') + ' ET; refused setups are logged with the gate that stopped them.' : ''}</p>`;
    return;
  }
  el.innerHTML = list.map(evRow).join('');
}

// ── performance ──────────────────────────────────────────────────────────────
function renderPerf() {
  const t = $('#perf-tiles'), c = $('#chart');
  if (!SUM) { t.innerHTML = ''; c.innerHTML = ''; return; }
  const r = SUM.rolling || {};
  const tile = (k, v, cls = '') => `<div class="pt-tile"><div class="eyebrow">${k}</div><div class="v ${cls}">${v}</div></div>`;
  t.innerHTML = tile('Closed trades', r.n ?? 0) +
    tile('Net P&L', signed(r.net || 0), (r.net || 0) > 0 ? 'up' : (r.net || 0) < 0 ? 'down' : '') +
    tile('Win rate', r.win_rate != null ? (r.win_rate * 100).toFixed(0) + '%' : '—') +
    tile('Profit factor', r.pf != null ? r.pf.toFixed(2) : '—', r.pf != null ? (r.pf >= 1.1 ? 'up' : 'down') : '') +
    tile('Green days', r.days ? `${r.green_days} / ${r.days}` : '—') +
    tile('Top-3 share', r.top3_share != null ? (r.top3_share * 100).toFixed(0) + '%' : '—', r.top3_share != null && r.top3_share > .4 ? 'warn' : '');
  const pts = SUM.series || [];
  if (!pts.length) { c.innerHTML = `<p class="empty"><strong>No closed trades yet.</strong> The cumulative P&L curve draws itself from the first close. Fees (${money(2.6, 2)} per spread round-trip) are already subtracted.</p>`; return; }
  const W = 900, H = 220, padL = 46, padR = 14, padT = 14, padB = 26;
  const ys = pts.map(p => p.cum).concat([0]); let lo = Math.min(...ys), hi = Math.max(...ys); if (hi === lo) { hi += 1; lo -= 1; } const span = hi - lo; lo -= span * .08; hi += span * .08;
  const x = i => padL + (pts.length === 1 ? (W - padL - padR) / 2 : i / (pts.length - 1) * (W - padL - padR));
  const y = v => padT + (hi - v) / (hi - lo) * (H - padT - padB);
  const path = pts.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.cum).toFixed(1)}`).join(' ');
  const area = `${path} L${x(pts.length - 1).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`;
  const ticks = [lo + span * .08, (lo + hi) / 2, hi - span * .08].map(v => Math.round(v));
  const grid = ticks.map(v => `<line class="grid" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/><text class="axis" x="${padL - 6}" y="${y(v) + 4}" text-anchor="end">${v < 0 ? '−' : ''}$${Math.abs(v)}</text>`).join('');
  const xlab = [0, pts.length - 1].map(i => `<text class="axis" x="${x(i)}" y="${H - 8}" text-anchor="${i ? 'end' : 'start'}">${pts[i].ts.slice(5, 10)}</text>`).join('');
  const last = pts[pts.length - 1];
  c.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-label="Cumulative P&L, ${pts.length} closed trades">
    <defs><linearGradient id="pnlfill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--cyan)" stop-opacity=".35"/><stop offset="1" stop-color="var(--violet)" stop-opacity="0"/></linearGradient></defs>
    ${grid}<line class="zero" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>
    <path class="area" d="${area}"/><path class="line" d="${path}"/>
    <circle class="dot" r="4" cx="${x(pts.length - 1)}" cy="${y(last.cum)}"/>
    <line class="cross" id="cross" x1="0" x2="0" y1="${padT}" y2="${H - padB}" opacity="0"/>${xlab}</svg><div class="tip" id="tip" hidden></div>`;
  const svg = $('svg', c), tip = $('#tip'), cross = $('#cross');
  svg.addEventListener('mousemove', e => {
    const rect = svg.getBoundingClientRect(); const px = (e.clientX - rect.left) / rect.width * W;
    let best = 0; for (let i = 1; i < pts.length; i++) if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    const p = pts[best]; cross.setAttribute('x1', x(best)); cross.setAttribute('x2', x(best)); cross.setAttribute('opacity', 1);
    tip.hidden = false; tip.style.left = (x(best) / W * rect.width) + 'px'; tip.style.top = (y(p.cum) / H * rect.height) + 'px';
    tip.innerHTML = `<b>${esc(p.sym)} ${p.direction === 'bull' ? '▲' : '▼'}</b> ${p.ts.slice(0, 10)} ${hhmm(p.ts)} · ${signed(p.pnl_usd)} (${p.pnl_pct != null ? pct(p.pnl_pct, 1) : ''})<br>${esc(p.reason || '')} · cum <b>${signed(p.cum)}</b>`;
  });
  svg.addEventListener('mouseleave', () => { tip.hidden = true; cross.setAttribute('opacity', 0); });
}

// ── report (tiny markdown renderer: headings, lists, tables, bold/italic/code) ─
function md(src) {
  const lines = src.split('\n'); let out = '', inUl = false, inTbl = false;
  const inline = s => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>').replace(/(^|\s)_([^_]+)_(?=\s|$|[.,;:])/g, '$1<em>$2</em>');
  const closeAll = () => { if (inUl) { out += '</ul>'; inUl = false; } if (inTbl) { out += '</tbody></table>'; inTbl = false; } };
  for (const raw of lines) {
    const l = raw.replace(/\s+$/, '');
    if (/^\|/.test(l)) {
      const cells = l.replace(/^\||\|$/g, '').split('|').map(c => c.trim());
      if (cells.every(c => /^:?-+:?$/.test(c))) continue;
      if (!inTbl) { closeAll(); inTbl = true; out += '<table><thead><tr>' + cells.map(c => `<th>${inline(c)}</th>`).join('') + '</tr></thead><tbody>'; continue; }
      out += '<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>'; continue;
    }
    if (inTbl) { out += '</tbody></table>'; inTbl = false; }
    const h = l.match(/^(#{1,3})\s+(.*)/);
    if (h) { closeAll(); out += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    if (/^\s*[-*]\s+/.test(l)) { if (!inUl) { out += '<ul>'; inUl = true; } out += `<li>${inline(l.replace(/^\s*[-*]\s+/, ''))}</li>`; continue; }
    if (inUl) { out += '</ul>'; inUl = false; }
    if (/^>\s?/.test(l)) { out += `<blockquote>${inline(l.replace(/^>\s?/, ''))}</blockquote>`; continue; }
    if (!l.trim()) continue;
    const em = l.match(/^_(.+)_$/);
    out += em ? `<p><em>${inline(em[1])}</em></p>` : `<p>${inline(l)}</p>`;
  }
  closeAll(); return out;
}
async function loadReports() {
  let list = []; try { list = await (await fetch('/api/reports')).json(); } catch (e) {}
  const sel = $('#reports'); const cur = sel.value;
  sel.innerHTML = list.map(d => `<option>${esc(d)}</option>`).join('') || '<option value="">none yet</option>';
  if (cur && list.includes(cur)) sel.value = cur;
  if (list.length) showReport();
}
async function showReport() {
  const d = $('#reports').value; if (!d) return;
  const r = await fetch('/api/report/' + encodeURIComponent(d));
  $('#report').innerHTML = r.ok ? md(await r.text()) : '<p class="empty">No report for that day.</p>';
}

// ── footer rules ─────────────────────────────────────────────────────────────
function renderRules() {
  const L = S?.limits; if (!L) return;
  const item = (k, v) => `<span>${k} <b>${esc(v)}</b></span>`;
  $('#rules').innerHTML = item('Budget', `${money(S.day?.budget_usd || 0)}/day · ${money(L.per_trade_max_usd)}/spread`) + item('Trades', `${S.day?.max_trades ?? '—'}/day · ${L.max_open} open · ${L.same_dir_max} per direction`) +
    item('Exits', `±${(L.tp_pct * 100).toFixed(0)}% · stall ${L.stall_min} min · flatten ${L.flatten} ET`) + item('Entries', `${L.entry_window} · gap ≥${L.gap_delay_pct}% → ${L.gap_delay_start}`) +
    item('DTE', L.dte) + item('Confidence floor', `${(L.min_confidence * 100).toFixed(0)}% · n ≥ ${L.min_history_n}`) +
    item('Debate', L.debate_enabled ? L.debate_model : 'off') + item('Setups', (L.setups || []).map(s => SETUP_LABEL[s] || s).join(' · '));
  const H = S.history || {}; let rows = '';
  for (const sym of Object.keys(H)) for (const st of Object.keys(H[sym])) for (const dir of ['bull', 'bear']) {
    const h = H[sym][st][dir]; if (!h) continue;
    rows += `<tr><td>${sym} · ${esc(SETUP_LABEL[st] || st)} ${dir === 'bull' ? '▲' : '▼'}</td><td>${h.n}</td><td>${((h.rate || 0) * 100).toFixed(0)}%</td><td class="${(h.lower95 || 0) >= .5 ? 'good' : ''}">${((h.lower95 || 0) * 100).toFixed(0)}%</td><td>${((h.upper95 || 0) * 100).toFixed(0)}%</td><td>${h.avg_move_pct != null ? pct(h.avg_move_pct, 3) : '—'}</td></tr>`;
  }
  $('#hist-table').innerHTML = rows ? `<table><thead><tr><th>Setup · direction</th><th>n</th><th>hit</th><th>95% low</th><th>95% high</th><th>avg move</th></tr></thead><tbody>${rows}</tbody></table><p class="hint" style="margin:6px 0 0">Replay of the same rule over every cached session inside today's entry window; outcome = first touch of ±0.30% before 15:45. Green = lower bound clears a coin flip.</p>` : '';
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function showTab(name) {
  for (const b of $('#tabs').children) b.classList.toggle('on', b.dataset.tab === name);
  for (const p of document.querySelectorAll('.panel')) p.classList.toggle('on', p.dataset.panel === name);
  try { localStorage.setItem('tab', name); } catch (e) {}
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) showTab(b.dataset.tab); });
(() => { const h = location.hash.slice(1); let t = null; try { t = localStorage.getItem('tab'); } catch (e) {}
  const valid = [...$('#tabs').children].map(b => b.dataset.tab); showTab(valid.includes(h) ? h : valid.includes(t) ? t : 'desk'); })();

// ── polling ──────────────────────────────────────────────────────────────────
async function refreshStatus() {
  try { const r = await fetch('/api/status', {cache: 'no-store'}); S = r.ok ? await r.json() : null; } catch (e) { S = null; }
  renderTop(); renderTiles(); for (const sym of (S?.limits?.underlyings || ['SPY', 'QQQ'])) symCard(sym); renderOpen(); renderRules();
}
async function refreshWhy() {
  const syms = S?.limits?.underlyings || ['SPY', 'QQQ'];
  await Promise.all(syms.map(async sym => {
    try { const r = await fetch('/api/why/' + sym, {cache: 'no-store'}); if (r.ok) WHY[sym] = await r.json(); } catch (e) {}
  }));
  if (S) for (const sym of syms) symCard(sym);
}
async function refreshExtra() {
  try { const r = await fetch('/api/history_extra', {cache: 'no-store'}); if (r.ok) EXTRA = await r.json(); } catch (e) {}
  if (EXTRA && !EXTRA.ready) setTimeout(refreshExtra, 5000);     // first call kicks off a ~5 s background replay
  if (S) { for (const sym of (S.limits?.underlyings || ['SPY', 'QQQ'])) symCard(sym); }
}
async function refreshLedger() {
  try { const r = await fetch('/api/summary', {cache: 'no-store'}); SUM = r.ok ? await r.json() : null; } catch (e) { SUM = null; }
  renderPerf(); renderActivity();
}
$('#act-filter').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; actFilter = b.dataset.f; for (const x of $('#act-filter').children) x.classList.toggle('on', x === b); renderActivity(); });
$('#act-day').addEventListener('change', e => { actDay = e.target.value || null; renderActivity(); });
$('#reports').addEventListener('change', showReport);
// theme: stored choice wins, else OS preference
const root = document.documentElement;
try { root.dataset.theme = localStorage.getItem('theme') === 'light' ? 'light' : 'dark'; } catch (e) { root.dataset.theme = 'dark'; }   // dark is the desk default; light is opt-in
$('#theme').addEventListener('click', () => { root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light'; try { localStorage.setItem('theme', root.dataset.theme); } catch (e) {} });

refreshStatus(); refreshLedger(); loadReports();
refreshExtra(); setInterval(refreshExtra, 600000);
refreshWhy(); setInterval(() => { if (!document.hidden) refreshWhy(); }, 30000);
// background tabs don't poll (several open dashboards multiplied the load); catch up on return
const live = fn => () => { if (!document.hidden) fn(); };
setInterval(live(refreshStatus), 15000); setInterval(live(refreshLedger), 30000); setInterval(live(loadReports), 300000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshStatus(); refreshLedger(); refreshWhy(); } });
setInterval(() => { if (S) renderTop(); }, 1000);   // live countdown + heartbeat age
})();
