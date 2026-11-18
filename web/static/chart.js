/* Price & signals chart — 5-min candles, VWAP/EMA overlays, markers from a bar-by-bar
   replay of the live entry rule (/api/chart/<sym>), plus the ledger's fills & closes.
   Refreshes every 30 s while viewing today. */
(() => {
'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const LC = window.LightweightCharts;
const el = $('#ch');
if (!el) return;
if (!LC) { $('#ch-empty').hidden = false; $('#ch-empty').textContent = 'Chart library failed to load (offline?).'; return; }

const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SETUP = {trend_pullback: 'Trend pullback', vwap_momentum: 'VWAP momentum'};
const SHORT = {trend_pullback: 'Pullback', vwap_momentum: 'VWAP mom.'};
const GATE = {entry_window: 'Window', signal_fresh: 'Stale signal', event_blackout: 'Events', confidence: 'Confidence', cooldown: 'Cooldown', same_direction: 'Correlation',
  fill_quality: 'Fill quality', drawdown: 'Drawdown', vol_edge: 'Vol edge', chain: 'Chain', dte: 'DTE', liquidity: 'Liquidity',
  spread_cost: 'Spread cost', spread_ratio: 'Pricing', day_risk: 'Budget', debate: 'Debate'};
let sym = 'SPY', day = '', data = null, timer = null, fitted = '';
const show = {vwap: true, ema9: true, ema21: false, off: true};
try { Object.assign(show, JSON.parse(localStorage.getItem('ch-ind') || '{}')); sym = localStorage.getItem('ch-sym') || 'SPY'; } catch (e) {}

let chart, candles, vol, lines = {};
function build() {
  if (chart) chart.remove();
  chart = LC.createChart(el, {
    autoSize: true,
    layout: {background: {type: 'solid', color: 'transparent'}, textColor: css('--muted'), fontFamily: css('--mono'), fontSize: 11},
    grid: {vertLines: {color: css('--line')}, horzLines: {color: css('--line')}},
    rightPriceScale: {borderColor: css('--line'), scaleMargins: {top: .08, bottom: .22}},
    timeScale: {borderColor: css('--line'), timeVisible: true, secondsVisible: false, rightOffset: 4, barSpacing: 9},
    crosshair: {mode: 0, vertLine: {color: css('--line-strong'), labelBackgroundColor: css('--surface-2')},
                horzLine: {color: css('--line-strong'), labelBackgroundColor: css('--surface-2')}},
    // wheel must scroll the page, not zoom the chart (it's mid-page); zoom = pinch / drag the axes
    handleScale: {mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true},
    handleScroll: {mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
  });
  const bull = css('--bull'), bear = css('--bear');
  candles = chart.addCandlestickSeries({upColor: bull, downColor: bear, borderUpColor: bull, borderDownColor: bear,
    wickUpColor: bull, wickDownColor: bear, priceLineColor: css('--ink-2'), priceLineStyle: 2});
  vol = chart.addHistogramSeries({priceScaleId: 'vol', priceFormat: {type: 'volume'}, lastValueVisible: false, priceLineVisible: false});
  chart.priceScale('vol').applyOptions({scaleMargins: {top: .82, bottom: 0}, visible: false});
  const ln = (color, style = 0, w = 2) => chart.addLineSeries({color, lineWidth: w, lineStyle: style, lastValueVisible: false,
    priceLineVisible: false, crosshairMarkerVisible: false});
  lines = {vwap: ln(css('--amber'), 0, 2), ema9: ln(css('--cyan'), 0, 1), ema21: ln(css('--violet'), 2, 1)};
  chart.timeScale().subscribeVisibleTimeRangeChange(placeWindow);
  chart.subscribeCrosshairMove(onMove);
  fitted = '';
  if (data) render();
}

function placeWindow() {
  const w = $('#ch-win');
  if (!data || !data.bars.length) { w.hidden = true; return; }
  const ts = chart.timeScale();
  const snap = t => { // nearest bar time (coordinate needs a time that exists)
    let best = null; for (const b of data.bars) { if (b.time >= t) { best = b.time; break; } }
    return best ?? data.bars[data.bars.length - 1].time;
  };
  const a = ts.timeToCoordinate(snap(data.window.start));
  const endBar = [...data.bars].reverse().find(b => b.time <= data.window.end);
  let b = endBar ? ts.timeToCoordinate(endBar.time) : null;
  if (a == null || data.bars[0].time > data.window.end) { w.hidden = true; return; }
  const half = (ts.options().barSpacing || 8) / 2;
  if (b == null) b = el.clientWidth;
  w.style.left = Math.max(0, a - half) + 'px';
  w.style.width = Math.max(0, b + half - Math.max(0, a - half)) + 'px';
  w.hidden = false;
}

function onMove(p) {
  if (!data || !p || !p.time) { quote(); return; }
  const bar = p.seriesData.get(candles);
  if (bar) quote(bar, p.time);
  const here = data.signals.filter(s => s.time === p.time && (s.traded !== false || show.off));
  if (here.length) $('#ch-quote').insertAdjacentHTML('beforeend', '<span class="hv">' + here.map(s =>
    `<span class="side ${s.direction === 'bull' ? 'buy' : 'sell'}">${s.direction === 'bull' ? '▲ BUY' : '▼ SELL'}</span> ${esc(SETUP[s.setup] || s.setup)} ${verdict(s)}`).join(' · ') + '</span>');
}
function quote(bar, t) {
  const q = $('#ch-quote');
  if (!data || !data.bars.length) { q.innerHTML = ''; return; }
  bar = bar || data.bars[data.bars.length - 1];
  t = t || bar.time;
  const hm = new Date(t * 1000).toISOString().slice(11, 16);
  const chg = bar.close - data.bars[0].open, pc = chg / data.bars[0].open * 100;
  const cls = chg >= 0 ? 'up' : 'down';
  q.innerHTML = `<b>${bar.close.toFixed(2)}</b> <span class="${cls}">${chg >= 0 ? '+' : ''}${chg.toFixed(2)} (${pc >= 0 ? '+' : ''}${pc.toFixed(2)}%)</span>
    <span class="dim">O ${bar.open.toFixed(2)} H ${bar.high.toFixed(2)} L ${bar.low.toFixed(2)} · ${hm} ET</span>`;
}

function render() {
  const d = data;
  const empty = $('#ch-empty');
  if (!d.bars.length) {
    empty.hidden = false;
    empty.textContent = d.source === 'live' ? 'No closed 5-min bars yet today — the first one lands at 09:35 ET.' : 'No bars cached for this session.';
  } else empty.hidden = true;

  candles.setData(d.bars);
  const bull = css('--bull'), bear = css('--bear');
  vol.setData(d.bars.map(b => ({time: b.time, value: b.volume,
    color: (b.close >= b.open ? bull : bear) + '40'})));
  for (const k of ['vwap', 'ema9', 'ema21']) { lines[k].setData(d[k] || []); lines[k].applyOptions({visible: !!show[k]}); }

  const muted = css('--muted');
  const markers = [];
  const violet = css('--violet');
  for (const s of d.signals) {
    const up = s.direction === 'bull';
    if (s.traded === false) {          // disabled setup: shown for comparison, never traded
      if (!show.off) continue;
      markers.push({time: s.time, position: up ? 'belowBar' : 'aboveBar', shape: 'circle',
        color: violet, size: .7, text: s.in_window ? (up ? 'pb▲' : 'pb▼') : ''});
      continue;
    }
    markers.push({time: s.time, position: up ? 'belowBar' : 'aboveBar', shape: up ? 'arrowUp' : 'arrowDown',
      color: !s.in_window ? muted : (up ? bull : bear) + (s.provisional ? '99' : ''), size: s.in_window ? 1.4 : .8,
      text: s.in_window ? (up ? 'BUY' : 'SELL') + (s.provisional ? '?' : '') : ''});
  }
  for (const e of d.events) {
    if (e.kind === 'fill') markers.push({time: snap(e.time), position: e.direction === 'bull' ? 'belowBar' : 'aboveBar',
      shape: 'circle', color: css('--blue'), size: 1.2, text: 'FILLED'});
    if (e.kind === 'close' || e.kind === 'orphan') markers.push({time: snap(e.time), position: 'aboveBar', shape: 'square',
      color: (e.pnl_usd ?? 0) >= 0 ? bull : bear, size: 1.1,
      text: e.pnl_usd != null ? `${e.pnl_usd >= 0 ? '+' : '−'}$${Math.abs(e.pnl_usd).toFixed(0)}` : 'CLOSED'});
  }
  markers.sort((a, b) => a.time - b.time);
  candles.setMarkers(markers);

  const key = d.sym + d.day;
  if (fitted !== key) { chart.timeScale().fitContent(); fitted = key; }
  quote();
  requestAnimationFrame(placeWindow);
  list();
}
function decisionFor(s) {   // same rule as chart.match_decision: exact signal price first, time window only as fallback
  const c = data.events.filter(e => e.kind === 'decision' && e.direction === s.direction && e.setup === s.setup
    && e.time >= s.time && e.time < s.time + 3600);
  return c.find(e => e.price != null && Math.abs(e.price - s.price) < 0.015)
      || c.find(e => e.price == null && e.time < s.time + 1800);
}
function verdict(s) {
  if (s.traded === false) return '<span class="k off" title="setup switched off in config — never traded">◌ off</span>';
  const dec = decisionFor(s);
  if (dec) {
    const lag = Math.round((dec.time - (s.time + 300)) / 60);       // decision vs the signal bar's close
    const lagChip = lag > 5 ? `<span class="k lag ${lag > 10 ? 'loss' : 'reject'}" title="Engine decided ${lag} min after this bar closed (${esc(dec.et)} ET) — the entry assumes the bar's close">⏲ ${lag} min</span> ` : '';
    return lagChip + (dec.accepted ? '<span class="k accept">✓ entered</span>'
      : `<span class="k reject" title="${esc(dec.why)}">✗ ${esc(GATE[dec.gate] || dec.gate || 'gate')}</span>`);
  }
  if (!s.in_window) return '<span class="k engine" title="Fired outside the entry window — not tradable">⏱ off-hours</span>';
  if (s.provisional) return '<span class="k engine" title="Newest bars use real-time IEX data with rescaled volume; delayed full-market bars replace them ~15 min later and can change this">? provisional</span>';
  return '<span class="k engine" title="The engine logged no decision for this fire (e.g. it was down, or the bar changed when SIP data replaced IEX)">—</span>';
}
function snap(t) { // ledger event → the 5-min bar it happened in
  if (!data.bars.length) return t;
  let best = data.bars[0].time;
  for (const b of data.bars) { if (b.time <= t) best = b.time; else break; }
  return best;
}

function list() {
  const d = data;
  const box = $('#ch-sigs');
  const tr = d.signals.filter(s => s.traded !== false), off = d.signals.filter(s => s.traded === false);
  const rows = d.signals.filter(s => s.traded !== false || show.off).map(s => {
    const up = s.direction === 'bull';
    return `<div class="sg ${s.in_window ? '' : 'out'} ${s.traded === false ? 'off' : ''} ${s.provisional ? 'prov' : ''}">
      <span class="t">${s.et}</span>
      <span class="side ${up ? 'buy' : 'sell'}">${up ? '▲ BUY' : '▼ SELL'}</span>
      <span class="px">${s.price.toFixed(2)}</span>
      <span class="st" title="${esc(SETUP[s.setup] || s.setup)} · strength ${(s.strength * 100).toFixed(0)}/100">${esc(SHORT[s.setup] || s.setup)} <span class="sbar"><i style="width:${(s.strength * 100).toFixed(0)}%"></i></span></span>
      <span class="vd">${verdict(s)}</span></div>`;
  }).reverse();
  const inWin = tr.filter(s => s.in_window).length;
  const head = `<div class="ch-sig-head">
    <span class="wc" title="Fires from traded setups on ${esc(d.day)}">⚡ ${tr.length}</span>
    <span class="wc ${inWin ? 'ok' : ''}" title="…of which inside the entry window ${esc(d.window.label)} ET">⏱ ${inWin}</span>
    ${off.length && show.off ? `<span class="wc" title="Fires from setups not traded">◌ ${off.length}</span>` : ''}
    <span class="wc ${d.trend || ''}" title="Daily 20-day trend">${d.trend ? (d.trend === 'bull' ? '▲ trend' : '▼ trend') : '→ flat'}</span>
    ${d.fallback ? `<span class="wc wait" title="No bars today (weekend, holiday or feed down) — showing the last session">↺ ${esc(d.day)}</span>` : ''}
  </div>`;
  const note = '';
  box.innerHTML = head + note + (rows.length ? `<div class="sg-list">${rows.join('')}</div>`
    : `<p class="empty">⚡ 0</p>`);
}

async function load() {
  try {
    const r = await fetch(`/api/chart/${sym}${day ? `?day=${day}` : ''}`);
    if (!r.ok) throw new Error(r.status);
    data = await r.json();
    render();
  } catch (e) {
    $('#ch-empty').hidden = false; $('#ch-empty').textContent = 'Could not load chart data.';
  }
}
async function loadDays() {
  try {
    const days = await (await fetch(`/api/chart/${sym}/days`)).json();
    const sel = $('#ch-day');
    sel.innerHTML = days.map((d, i) => `<option value="${i === 0 ? '' : d}">${i === 0 ? 'Today · ' + d : d}</option>`).join('');
    sel.value = day;
  } catch (e) {}
}
function schedule() {
  clearInterval(timer);
  timer = setInterval(() => { if (!day && !document.hidden) load(); }, 30000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !day) load(); });
}

$('#ch-sym').addEventListener('click', e => {
  const b = e.target.closest('[data-sym]'); if (!b) return;
  sym = b.dataset.sym; try { localStorage.setItem('ch-sym', sym); } catch (_) {}
  document.querySelectorAll('#ch-sym .chip').forEach(c => c.classList.toggle('on', c === b));
  load();
});
$('#ch-ind').addEventListener('click', e => {
  const b = e.target.closest('[data-ind]'); if (!b) return;
  const k = b.dataset.ind; show[k] = !show[k]; b.classList.toggle('on', show[k]);
  if (lines[k]) lines[k].applyOptions({visible: show[k]}); else if (data) render();
  try { localStorage.setItem('ch-ind', JSON.stringify(show)); } catch (_) {}
});
$('#ch-day').addEventListener('change', e => { day = e.target.value; load(); });
document.querySelectorAll('#ch-sym .chip').forEach(c => c.classList.toggle('on', c.dataset.sym === sym));
document.querySelectorAll('#ch-ind .chip').forEach(c => c.classList.toggle('on', !!show[c.dataset.ind]));
// rebuild with new colors when the theme flips
new MutationObserver(build).observe(document.documentElement, {attributes: true, attributeFilter: ['data-theme']});
// the chart lives in a tab: it's built at 0×0 while hidden, so refit when it first gets a size
let wasVisible = false;
new ResizeObserver(() => {
  const vis = el.clientWidth > 0;
  if (vis && !wasVisible && chart && data) chart.timeScale().fitContent();
  wasVisible = vis;
  requestAnimationFrame(placeWindow);
}).observe(el);

build(); loadDays(); load(); schedule();
})();
