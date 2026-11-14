// Planet X · SPHEREx Explorer — application controller.
import { api } from './api.js';
import { startGalaxy } from './galaxy.js';
import { Viewer } from './viewer.js';
import { SpectrumChart, DET_COLORS, fmt } from './spectrum.js';
import { SkyContext } from './context.js';
import { fmtRA, fmtDec, fmtDate, fmtDateTime, makeProjection } from './coords.js';

const $ = (id) => document.getElementById(id);
const store = {
  get(k, d) { try { const v = localStorage.getItem('px.' + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem('px.' + k, JSON.stringify(v)); } catch { /* private mode */ } },
};

// ------------------------------------------------------------------ state
const S = {
  cfg: null,
  channels: [],
  job: null,            // { id, meta }
  mode: 'epoch',
  frames: {},           // mode -> {data, nFrames, n}
  frame: 0,
  playing: false,
  fps: 3,
  bounce: false,
  dir: 1,
  catalogs: {},         // name -> data
  spectrum: null,
  pollTimer: 0,
  lastParams: null,
  recording: null,
};

const galaxy = startGalaxy($('galaxy'));
galaxy.setEnabled(store.get('galaxy', true));
galaxy.setQuality(store.get('quality', 'auto'));

const viewer = new Viewer($('view'), { onHover, onClick: onImageClick });
const chart = new SpectrumChart($('specCanvas'));
const sky = new SkyContext($('aladin'));

// ------------------------------------------------------------------ toasts
function toast(msg, kind = '') {
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = msg;
  $('toasts').appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transition = 'opacity .4s'; }, kind === 'err' ? 7000 : 3500);
  setTimeout(() => el.remove(), kind === 'err' ? 7600 : 4000);
}

// ------------------------------------------------------------------ form controls
function setOut(id, v) { $(id).textContent = v; }
function lamRange() {
  const a = +$('lamMin').value, b = +$('lamMax').value;
  const i = Math.min(a, b), j = Math.max(a, b);
  return { i, j, lo: S.channels[i]?.lo ?? 0.75, hi: S.channels[j]?.hi ?? 5.0 };
}
function updateLamUI() {
  const { i, j, lo, hi } = lamRange();
  setOut('lamOut', `${lo.toFixed(2)} – ${hi.toFixed(2)} µm`);
  setOut('chanOut', `Channels ${i + 1} – ${j + 1} of 102 (6 detectors × 17)`);
  const pa = (i / 101) * 100, pb = (j / 101) * 100;
  $('lamFill').style.clipPath = `inset(0 ${100 - pb}% 0 ${pa}%)`;
  document.querySelectorAll('.det').forEach((el) => {
    const d = S.cfg?.detectors[el.dataset.d];
    el.style.opacity = d && (d.hi < lo || d.lo > hi) ? '0.35' : '1';
  });
}
function chanIndex(lam, isMin) {
  const ch = S.channels;
  if (isMin) { const k = ch.findIndex((c) => c.hi > lam + 1e-6); return k < 0 ? 0 : k; }
  for (let k = ch.length - 1; k >= 0; k--) if (ch[k].lo < lam - 1e-6) return k;
  return ch.length - 1;
}
function selectedDetectors() {
  return [...document.querySelectorAll('.det.on')].map((el) => +el.dataset.d);
}
function buildControls() {
  // presets
  const chips = $('presetChips');
  chips.innerHTML = '';
  for (const t of S.cfg.targets) {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'chip'; b.textContent = t.name; b.title = t.note;
    b.addEventListener('click', () => {
      $('q').value = t.name;
      document.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c === b));
      run();
    });
    chips.appendChild(b);
  }
  // detectors
  const dc = $('detChips');
  dc.innerHTML = '';
  for (const [d, info] of Object.entries(S.cfg.detectors)) {
    const el = document.createElement('div');
    el.className = 'det on'; el.dataset.d = d; el.tabIndex = 0; el.setAttribute('role', 'checkbox');
    el.setAttribute('aria-checked', 'true');
    el.style.setProperty('--c', DET_COLORS[d]);
    el.title = `Detector ${d}: ${info.lo}–${info.hi} µm, R≈${info.R}`;
    el.innerHTML = `<b>D${d}</b>${info.lo.toFixed(2)}–${info.hi.toFixed(2)}`;
    const toggle = () => {
      el.classList.toggle('on');
      if (!selectedDetectors().length) { el.classList.add('on'); toast('At least one detector is needed.'); }
      el.setAttribute('aria-checked', el.classList.contains('on'));
    };
    el.addEventListener('click', toggle);
    el.addEventListener('keydown', (e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggle(); } });
    dc.appendChild(el);
  }
  // spectrum legend
  $('detLegend').innerHTML = Object.entries(S.cfg.detectors)
    .map(([d, i]) => `<span><i style="background:${DET_COLORS[d]}"></i>D${d} ${i.lo}–${i.hi} µm</span>`).join('')
    + '<span><i style="background:#fff"></i>102-channel binned</span>';
  $('fov').min = S.cfg.limits.fov_min; $('fov').max = S.cfg.limits.fov_max;
  $('maxExp').max = S.cfg.limits.max_exposures;
}

$('fov').addEventListener('input', () => setOut('fovOut', `${(+$('fov').value).toFixed(1)}′`));
$('maxExp').addEventListener('input', () => setOut('maxOut', $('maxExp').value));
$('gap').addEventListener('input', () => setOut('gapOut', `${$('gap').value} d`));
$('lamMin').addEventListener('input', () => { if (+$('lamMin').value > +$('lamMax').value) $('lamMin').value = $('lamMax').value; updateLamUI(); });
$('lamMax').addEventListener('input', () => { if (+$('lamMax').value < +$('lamMin').value) $('lamMax').value = $('lamMin').value; updateLamUI(); });

// ------------------------------------------------------------------ URL state
const DISPLAY_KEYS = ['mode', 'diff', 'stretch', 'cmap', 'inv', 'lo', 'hi', 'fps', 'smooth'];
function readURL() {
  const p = new URLSearchParams(location.search);
  const ra = p.get('ra'), dec = p.get('dec');
  if (p.get('q')) $('q').value = p.get('q');
  else if (ra !== null && dec !== null && isFinite(+ra) && isFinite(+dec)) $('q').value = `${ra} ${dec}`;
  const num = (k, el, f = (v) => v) => { const v = p.get(k); if (v !== null && isFinite(+v)) $(el).value = f(+v); };
  num('fov', 'fov'); num('max', 'maxExp'); num('gap', 'gap');
  if (p.get('lmin') !== null && isFinite(+p.get('lmin'))) $('lamMin').value = chanIndex(+p.get('lmin'), true);
  if (p.get('lmax') !== null && isFinite(+p.get('lmax'))) $('lamMax').value = chanIndex(+p.get('lmax'), false);
  if (p.get('det')) {
    const ds = new Set(p.get('det').split('').map(Number));
    document.querySelectorAll('.det').forEach((el) => { el.classList.toggle('on', ds.has(+el.dataset.d)); });
    if (!selectedDetectors().length) document.querySelectorAll('.det').forEach((el) => el.classList.add('on'));
  }
  $('flags').checked = p.get('flags') === '1';
  if (p.get('mode') === 'exposure') S.mode = 'exposure';
  if (['none', 'static', 'first', 'prev'].includes(p.get('diff'))) setSeg('segDiff', p.get('diff'));
  if (p.get('stretch')) $('stretch').value = p.get('stretch');
  if (p.get('cmap')) $('cmap').value = p.get('cmap');
  if (p.get('inv') === '1') $('btnInvert').classList.add('on');
  if (p.get('smooth') === '1') $('btnSmooth').classList.add('on');
  num('lo', 'loPct'); num('hi', 'hiPct'); num('fps', 'fps');
  if (p.get('ov')) {
    const ov = new Set(p.get('ov').split(','));
    document.querySelectorAll('[data-ov]').forEach((el) => { el.checked = ov.has(el.dataset.ov); });
  }
  setSeg('segMode', S.mode);
  ['fov', 'maxExp', 'gap', 'fps'].forEach((id) => $(id).dispatchEvent(new Event('input')));
  updateLamUI();
  return !!$('q').value.trim();
}
function writeURL() {
  const p = new URLSearchParams();
  const q = $('q').value.trim();
  if (q) p.set('q', q);
  p.set('fov', (+$('fov').value).toString());
  const { lo, hi } = lamRange();
  if (lo > 0.75 + 1e-6) p.set('lmin', lo.toFixed(4));
  if (hi < 5.0 - 1e-6) p.set('lmax', hi.toFixed(4));
  const det = selectedDetectors().join('');
  if (det !== '123456') p.set('det', det);
  if (+$('maxExp').value !== S.cfg.defaults.max_exposures) p.set('max', $('maxExp').value);
  if (+$('gap').value !== S.cfg.defaults.epoch_gap_days) p.set('gap', $('gap').value);
  if ($('flags').checked) p.set('flags', '1');
  if (S.mode !== 'epoch') p.set('mode', S.mode);
  const diff = segValue('segDiff'); if (diff !== 'none') p.set('diff', diff);
  if ($('stretch').value !== 'asinh') p.set('stretch', $('stretch').value);
  if ($('cmap').value !== 'gray') p.set('cmap', $('cmap').value);
  if ($('btnInvert').classList.contains('on')) p.set('inv', '1');
  if ($('btnSmooth').classList.contains('on')) p.set('smooth', '1');
  if (+$('loPct').value !== 5) p.set('lo', $('loPct').value);
  if (+$('hiPct').value !== 99.5) p.set('hi', $('hiPct').value);
  if (+$('fps').value !== 3) p.set('fps', $('fps').value);
  const ov = [...document.querySelectorAll('[data-ov]')].filter((e) => e.checked).map((e) => e.dataset.ov).join(',');
  if (ov !== 'target,compass') p.set('ov', ov);
  const url = `${location.pathname}?${p.toString()}`;
  history.replaceState(null, '', url);
  return location.origin + url;
}

// ------------------------------------------------------------------ segmented controls
function setSeg(id, v) { document.querySelectorAll(`#${id} button`).forEach((b) => b.classList.toggle('on', b.dataset.v === v)); }
function segValue(id) { return document.querySelector(`#${id} button.on`)?.dataset.v; }
document.querySelectorAll('.seg').forEach((seg) => seg.addEventListener('click', (e) => {
  const b = e.target.closest('button'); if (!b) return;
  setSeg(seg.id, b.dataset.v);
  if (seg.id === 'segMode') switchMode(b.dataset.v);
  else if (seg.id === 'segDiff') { viewer.setOptions({ diff: b.dataset.v }); $('loPct').disabled = b.dataset.v !== 'none'; writeURL(); }
  else if (seg.id === 'segSurvey') sky.setSurvey(b.dataset.v);
}));

// ------------------------------------------------------------------ display options
function pushDisplayOptions() {
  viewer.setOptions({
    stretch: $('stretch').value, cmap: $('cmap').value,
    invert: $('btnInvert').classList.contains('on'), smooth: $('btnSmooth').classList.contains('on'),
    loPct: +$('loPct').value, hiPct: +$('hiPct').value, diff: segValue('segDiff'),
  });
  $('loPct').disabled = segValue('segDiff') !== 'none';
  updateLegend();
}
['stretch', 'cmap'].forEach((id) => $(id).addEventListener('change', () => { pushDisplayOptions(); writeURL(); }));
['loPct', 'hiPct'].forEach((id) => $(id).addEventListener('input', () => { pushDisplayOptions(); }));
['loPct', 'hiPct'].forEach((id) => $(id).addEventListener('change', writeURL));
$('btnInvert').addEventListener('click', () => { $('btnInvert').classList.toggle('on'); pushDisplayOptions(); writeURL(); });
$('btnSmooth').addEventListener('click', () => { $('btnSmooth').classList.toggle('on'); pushDisplayOptions(); writeURL(); });
$('fps').addEventListener('input', () => { S.fps = +$('fps').value; setOut('fpsOut', S.fps); });
$('fps').addEventListener('change', writeURL);
$('btnBounce').addEventListener('click', () => { S.bounce = !S.bounce; $('btnBounce').classList.toggle('on', S.bounce); });

function updateLegend() {
  if (!S.job || !viewer.data) { $('hudLegend').textContent = ''; return; }
  const [lo, hi] = viewer.limits;
  const diff = segValue('segDiff') !== 'none' && viewer.data.nFrames > 1;
  $('hudLegend').textContent = diff ? `difference ±${fmt(hi)} MJy/sr` : `${fmt(lo)} … ${fmt(hi)} MJy/sr  (${$('stretch').value})`;
}

// ------------------------------------------------------------------ overlays
document.querySelectorAll('[data-ov]').forEach((el) => el.addEventListener('change', async () => {
  const k = el.dataset.ov;
  if (k === 'target' || k === 'compass') viewer.setOverlays({ [k]: el.checked });
  else await toggleCatalog(k, el.checked);
  writeURL();
}));
async function toggleCatalog(name, on) {
  if (!on || !S.job) {
    const cats = { ...viewer.ov.catalogs }; delete cats[name];
    viewer.setOverlays({ catalogs: cats });
    return;
  }
  const g = S.job.meta.grid;
  const radius = (g.n * g.scale) / 2 * 1.45;
  try {
    if (!S.catalogs[name]) {
      S.catalogs[name] = await api.catalog(name, g.ra0, g.dec0, radius);
      if (!S.catalogs[name].rows.length) toast(`${S.catalogs[name].label}: no sources in this field.`);
    }
    const el = document.querySelector(`[data-ov="${name}"]`);
    if (el && el.checked) viewer.setOverlays({ catalogs: { ...viewer.ov.catalogs, [name]: S.catalogs[name] } });
  } catch (e) {
    toast(`Catalogue unavailable: ${e.message}`, 'err');
    const el = document.querySelector(`[data-ov="${name}"]`); if (el) el.checked = false;
  }
}

// ------------------------------------------------------------------ job lifecycle
function gatherParams() {
  const { lo, hi } = lamRange();
  return {
    query: $('q').value.trim(), fov_arcmin: +$('fov').value, lam_min: +lo.toFixed(4), lam_max: +hi.toFixed(4),
    detectors: selectedDetectors(), max_exposures: +$('maxExp').value, epoch_gap_days: +$('gap').value,
    flags: $('flags').checked,
  };
}

function showLoading(on, text = '', sub = '', pct = null) {
  $('loading').classList.toggle('hidden', !on);
  if (!on) return;
  $('loadingText').textContent = text;
  $('loadingSub').textContent = sub;
  const bar = $('progressBar');
  if (pct === null) bar.classList.add('indet'); else { bar.classList.remove('indet'); bar.style.width = `${Math.round(pct * 100)}%`; }
}

async function run() {
  const params = gatherParams();
  if (!params.query) { toast('Enter a target name or coordinates.', 'err'); $('q').focus(); return; }
  stopPlay();
  clearTimeout(S.pollTimer);
  S.lastParams = params;
  writeURL();
  $('emptyState').classList.add('hidden');
  showLoading(true, 'Resolving target…', '', null);
  $('btnGo').disabled = true;
  try {
    const job = await api.submit(params);
    S.pending = job.id;
    poll(job.id, 0);
  } catch (e) {
    showLoading(false); $('btnGo').disabled = false;
    if (!S.job) $('emptyState').classList.remove('hidden');
    toast(e.message, 'err');
  }
}

async function poll(id, n) {
  if (S.pending !== id) return;
  let st;
  try { st = await api.status(id); } catch (e) {
    if (e.status === 404) { toast('The server restarted; running the search again.'); return run(); }
    if (n > 40) { showLoading(false); $('btnGo').disabled = false; toast(e.message, 'err'); return; }
    S.pollTimer = setTimeout(() => poll(id, n + 1), 1500);
    return;
  }
  if (S.pending !== id) return;
  const pct = st.total ? st.done / st.total : null;
  const sub = st.total ? `${st.done} / ${st.total} exposures · ${st.elapsed.toFixed(0)} s` : `${st.elapsed.toFixed(0)} s`;
  showLoading(true, st.message || st.status, sub, st.status === 'fetching' ? pct : null);
  if (st.status === 'done') { S.pending = null; await loadJob(st); return; }
  if (st.status === 'error' || st.status === 'cancelled') {
    S.pending = null;
    showLoading(false); $('btnGo').disabled = false;
    if (!S.job) $('emptyState').classList.remove('hidden');
    if (st.status === 'error') toast(st.error || 'Processing failed', 'err');
    return;
  }
  S.pollTimer = setTimeout(() => poll(id, n + 1), n < 10 ? 600 : 1000);
}

$('btnCancel').addEventListener('click', async () => {
  const id = S.pending; S.pending = null; clearTimeout(S.pollTimer);
  showLoading(false); $('btnGo').disabled = false;
  if (!S.job) $('emptyState').classList.remove('hidden');
  if (id) { try { await api.cancel(id); } catch { /* ignore */ } }
});

async function loadJob(st) {
  S.job = { id: st.id, meta: st.meta };
  S.frames = {}; S.catalogs = {}; S.spectrum = null;
  viewer.setOverlays({ catalogs: {}, aperture: null });
  chart.set(null); $('btnCsv').disabled = true;
  $('specInfo').textContent = 'Click any source in the image to measure its brightness in every exposure across SPHEREx\'s 0.75–5 µm range (102 channels).';
  const meta = st.meta;
  if (S.mode === 'exposure' && meta.exposures.length > 400) S.mode = 'epoch';
  if (S.mode === 'epoch' && meta.epochs.length < 2 && meta.exposures.length > 1) {
    S.mode = 'exposure';
    toast('Only one epoch here so far, so showing individual exposures.');
  }
  setSeg('segMode', S.mode);
  try {
    await showMode(S.mode);
  } catch (e) {
    toast(`Could not load frames: ${e.message}`, 'err');
  }
  showLoading(false); $('btnGo').disabled = false;
  renderTargetCard();
  renderStats();
  for (const el of document.querySelectorAll('[data-ov]')) {
    if (['gaia', 'allwise', 'catwise'].includes(el.dataset.ov) && el.checked) toggleCatalog(el.dataset.ov, true);
  }
  updateSky();
  const s = meta.stats;
  toast(`${s.n_used} exposures · ${meta.epochs.length} epoch(s)` +
    (s.from_cache ? ' · from cache' : ` · ${(s.bytes / 1e6).toFixed(1)} MB in ${s.seconds.toFixed(0)} s`), 'ok');
  if (meta.warnings.length) $('metaWarn').innerHTML = meta.warnings.map((w) => `<div class="warnbox">${esc(w)}</div>`).join('');
  else $('metaWarn').innerHTML = '';
  writeURL();
}

async function switchMode(mode) {
  if (mode === S.mode && viewer.data) return;
  S.mode = mode;
  writeURL();
  if (!S.job) return;
  stopPlay();
  showLoading(true, mode === 'epoch' ? 'Loading epoch stacks…' : 'Loading individual exposures…', '', null);
  try { await showMode(mode); } catch (e) { toast(e.message, 'err'); }
  showLoading(false);
}

async function showMode(mode) {
  const meta = S.job.meta;
  if (!S.frames[mode]) {
    const f = await api.frames(S.job.id, mode);
    if (f.n !== meta.grid.n) throw new Error('frame size mismatch');
    S.frames = { [mode]: f, ...(S.frames.epoch && mode !== 'epoch' ? { epoch: S.frames.epoch } : {}) };
  }
  const f = S.frames[mode];
  const list = mode === 'epoch' ? meta.epochs : meta.exposures;
  S.frame = 0;
  viewer.setData({
    frames: f.data, nFrames: f.nFrames, n: f.n, grid: meta.grid,
    times: list.map((m) => m.t_mjd), target: meta.target, tRef: meta.center.t_ref_mjd,
  });
  viewer.setFrame(S.frame);
  $('scrub').max = Math.max(0, f.nFrames - 1);
  $('scrub').value = S.frame;
  buildTicks(list, mode);
  pushDisplayOptions();
  updateFrameUI();
}

function buildTicks(list, mode) {
  const ticks = $('ticks');
  ticks.innerHTML = '';
  const n = list.length;
  if (n < 2) return;
  const width = ticks.getBoundingClientRect().width || 300;
  const max = Math.max(2, Math.min(mode === 'epoch' ? 10 : 6, Math.floor(width / 64)));
  const step = Math.max(1, Math.ceil((n - 1) / (max - 1)));
  const idx = [];
  for (let i = 0; i < n; i += step) idx.push(i);
  if (idx[idx.length - 1] !== n - 1) {
    if ((n - 1 - idx[idx.length - 1]) < step * 0.6 && idx.length > 1) idx.pop();
    idx.push(n - 1);
  }
  for (const i of idx) {
    const s = document.createElement('span');
    s.style.left = `${(i / (n - 1)) * 100}%`;
    s.textContent = fmtDate(list[i].t_mjd).slice(2);
    if (i === 0) s.classList.add('first');
    if (i === n - 1) s.classList.add('last');
    if (mode === 'epoch' && list[i].band_complete === false) { s.classList.add('warn'); s.textContent += '⚠'; }
    ticks.appendChild(s);
  }
}

// ------------------------------------------------------------------ frame UI
function currentMeta() {
  if (!S.job) return null;
  return (S.mode === 'epoch' ? S.job.meta.epochs : S.job.meta.exposures)[S.frame];
}
function updateFrameUI() {
  const m = currentMeta();
  if (!m) { $('hudFrame').textContent = ''; return; }
  const meta = S.job.meta;
  let txt;
  if (S.mode === 'epoch') {
    const dets = m.detectors.length === 6 ? 'D1–D6' : m.detectors.map((d) => 'D' + d).join(' ');
    txt = `EPOCH ${S.frame + 1}/${meta.epochs.length}  ${fmtDate(m.t_mjd)}${m.band_complete === false ? '  ⚠ partial bands' : ''}\n`
      + `${m.n_exp} exposures · ${dets}` + (m.lam_min ? ` · ${m.lam_min.toFixed(2)}–${m.lam_max.toFixed(2)} µm` : '');
  } else {
    txt = `EXPOSURE ${S.frame + 1}/${meta.exposures.length}  ${fmtDateTime(m.t_mjd)}\n`
      + `D${m.detector}` + (m.lam_um ? ` · λ ${m.lam_um.toFixed(3)} µm` : '') + ` · epoch ${m.epoch + 1}`;
  }
  $('hudFrame').textContent = txt;
  $('scrub').value = S.frame;
  renderFrameMeta(m);
}

function renderFrameMeta(m) {
  const rows = [];
  const add = (k, v) => rows.push(`<div class="k">${k}</div><div class="v">${v}</div>`);
  if (S.mode === 'epoch') {
    add('Epoch', `${m.epoch + 1} of ${S.job.meta.epochs.length}`);
    add('Mean date', fmtDateTime(m.t_mjd));
    add('Span', `${fmtDate(m.t_start)} → ${fmtDate(m.t_end)} (${(m.t_end - m.t_start).toFixed(1)} d)`);
    add('Exposures', m.n_exp);
    add('Detectors', m.detectors.map((d) => 'D' + d).join(', ') + (m.band_complete === false ? ' <span class="flagpill">partial</span>' : ''));
    if (m.lam_min) add('Wavelengths', `${m.lam_min.toFixed(3)} – ${m.lam_max.toFixed(3)} µm`);
    add('Stack noise (1σ)', `${fmt(m.noise)} MJy/sr`);
    add('Coverage', `${(m.coverage * 100).toFixed(1)} %`);
    add('Rejected pixels', `${(m.rejected_frac * 100).toFixed(2)} % <span class="muted">(cosmic rays / artefacts)</span>`);
  } else {
    add('Observation', esc(m.obs_id));
    add('Date', fmtDateTime(m.t_mjd));
    add('Detector', `D${m.detector} (${m.em_min_um.toFixed(2)}–${m.em_max_um.toFixed(2)} µm)`);
    if (m.lam_um) add('λ at target', `${m.lam_um.toFixed(4)} µm (bandwidth ${m.bw_um ? m.bw_um.toFixed(4) : '?'} µm)`);
    add('Sky background', `${fmt(m.bg)} MJy/sr <span class="muted">(zodiacal light + sky)</span>`);
    add('Noise (1σ)', `${fmt(m.noise)} MJy/sr`);
    if (m.psf_fwhm) add('PSF FWHM', `${m.psf_fwhm.toFixed(2)}″`);
    add('Coverage', `${(m.coverage * 100).toFixed(1)} %`);
    add('Release', esc(m.collection));
    if (m.flags_read) {
      add('Masked bad pixels', `${m.n_bad_pixels}`);
      const warn = Object.entries(m.warn_flags || {});
      add('Contamination', warn.length ? warn.map(([k, v]) => `<span class="flagpill">${k} ${(v * 100).toFixed(1)}%</span>`).join('') : '<span class="flagpill ok">none</span>');
      add('Flags at target', (m.center_flags || []).length ? m.center_flags.map((f) => `<span class="flagpill${f === 'SOURCE' ? ' ok' : ''}">${f}</span>`).join('') : '<span class="flagpill ok">clean</span>');
    } else {
      add('Quality flags', '<span class="muted">not loaded – enable “Pixel-quality flags” under Advanced</span>');
    }
  }
  $('metaFrame').className = 'kv';
  $('metaFrame').innerHTML = rows.join('');
}

function renderStats() {
  const m = S.job.meta, s = m.stats;
  const rows = [
    ['Images found in archive', s.n_found], ['Selected (cap / filters)', s.n_selected], ['Used', s.n_used],
    ['Outside wavelength range', s.n_skipped_lambda], ['Failed downloads', s.n_failed],
    ['Data transferred', s.from_cache ? 'cached' : `${(s.bytes / 1e6).toFixed(2)} MB`], ['Processing time', `${s.seconds.toFixed(1)} s`],
    ['Grid', `${m.grid.n}×${m.grid.n} px @ ${m.grid.scale}″/px (${(m.grid.n * m.grid.scale / 60).toFixed(1)}′)`],
    ['Units', m.units],
  ];
  $('metaStats').className = 'kv';
  $('metaStats').innerHTML = rows.map(([k, v]) => `<div class="k">${k}</div><div class="v">${v}</div>`).join('');
}

function renderTargetCard() {
  const m = S.job.meta, t = m.target, c = m.center;
  const pm = Math.hypot(t.pmra, t.pmdec);
  const card = $('targetCard');
  card.classList.remove('hidden');
  card.innerHTML = `
    <h3>${esc(t.name)}</h3>
    <div class="kv">
      <div class="k">Catalogue (J2000)</div><div class="v">${fmtRA(t.ra)} ${fmtDec(t.dec)}</div>
      ${pm > 0 ? `<div class="k">Proper motion</div><div class="v">${(pm / 1000).toFixed(3)}″/yr (μα* ${t.pmra.toFixed(1)}, μδ ${t.pmdec.toFixed(1)} mas/yr)</div>` : ''}
      <div class="k">At SPHEREx epoch</div><div class="v">${fmtRA(c.ra)} ${fmtDec(c.dec)}<br><span class="muted">${fmtDate(c.t_ref_mjd)} · ${c.ra.toFixed(5)}° ${c.dec >= 0 ? '+' : ''}${c.dec.toFixed(5)}°</span></div>
      <div class="k">Resolved by</div><div class="v">${esc(t.source)}</div>
    </div>
    ${t.note ? `<p class="note">${esc(t.note)}</p>` : ''}`;
}

// ------------------------------------------------------------------ playback
function step(d) {
  if (!viewer.data) return;
  S.frame = (S.frame + d + viewer.data.nFrames) % viewer.data.nFrames;
  viewer.setFrame(S.frame);
  updateFrameUI();
}
let lastTick = 0, playRaf = 0;
function tick(now) {
  if (!S.playing) return;
  playRaf = requestAnimationFrame(tick);
  if (now - lastTick < 1000 / S.fps) return;
  lastTick = now;
  const n = viewer.data.nFrames;
  if (S.bounce && n > 1) {
    if (S.frame + S.dir >= n || S.frame + S.dir < 0) S.dir = -S.dir;
    step(S.dir);
  } else step(1);
}
function startPlay() {
  if (!viewer.data || viewer.data.nFrames < 2) return;
  S.playing = true; lastTick = 0;
  $('playIcon').innerHTML = '<path d="M7 5h3.5v14H7zM13.5 5H17v14h-3.5z" fill="currentColor"/>';
  playRaf = requestAnimationFrame(tick);
}
function stopPlay() {
  S.playing = false; cancelAnimationFrame(playRaf);
  $('playIcon').innerHTML = '<path d="M7 5v14l12-7z" fill="currentColor"/>';
}
$('btnPlay').addEventListener('click', () => (S.playing ? stopPlay() : startPlay()));
$('btnPrev').addEventListener('click', () => { stopPlay(); step(-1); });
$('btnNext').addEventListener('click', () => { stopPlay(); step(1); });
$('scrub').addEventListener('input', () => { stopPlay(); S.frame = +$('scrub').value; viewer.setFrame(S.frame); updateFrameUI(); });

// ------------------------------------------------------------------ hover & spectrum
function onHover(h) {
  if (!h) { $('hudCursor').textContent = ''; return; }
  const v = isFinite(h.value) ? `${fmt(h.value)} MJy/sr` : 'no data';
  $('hudCursor').textContent = `${fmtRA(h.ra)} ${fmtDec(h.dec)}\n${h.ra.toFixed(5)}° ${h.dec >= 0 ? '+' : ''}${h.dec.toFixed(5)}°\n(${h.x}, ${h.y})  ${v}`;
}

async function onImageClick(p) {
  if (!S.job) return;
  const follow = $('specFollow').checked;
  const r = 1.6;
  viewer.setOverlays({ aperture: { x: p.x, y: p.y, r, follow, t: viewer.frameTime() } });
  activateTab('spectrum');
  $('specInfo').textContent = 'Measuring every exposure…';
  try {
    const sp = await api.spectrum(S.job.id, { x: p.x.toFixed(2), y: p.y.toFixed(2), frame: S.frame, mode: S.mode, follow, r });
    const exps = S.job.meta.exposures;
    sp.points.forEach((pt) => { pt.date = fmtDate(exps[pt.i].t_mjd); });
    S.spectrum = sp;
    chart.set(sp);
    $('btnCsv').disabled = !sp.points.length;
    const skipped = sp.n_skipped ? ` · ${sp.n_skipped} skipped (saturated / missing pixels)` : '';
    $('specInfo').innerHTML = `<b>${fmtRA(sp.ra)} ${fmtDec(sp.dec)}</b><br>${sp.points.length} measurements · ${sp.channels.length}/102 channels · aperture r=${sp.radius_arcsec}″${skipped}` +
      (sp.followed_pm ? ' · aperture follows proper motion' : '');
  } catch (e) {
    $('specInfo').textContent = `Spectrum failed: ${e.message}`;
  }
}
$('specLog').addEventListener('change', () => chart.setLog($('specLog').checked));
$('specFollow').addEventListener('change', () => { if (viewer.ov.aperture) onImageClick(viewer.ov.aperture); });
$('btnCsv').addEventListener('click', () => {
  const sp = S.spectrum; if (!sp) return;
  const lines = ['# Planet X SPHEREx Explorer spectrum', `# target=${S.job.meta.target.name} ra=${sp.ra.toFixed(6)} dec=${sp.dec.toFixed(6)} aperture_arcsec=${sp.radius_arcsec}`,
    'wavelength_um,bandwidth_um,flux_mJy,err_mJy,detector,mjd'];
  for (const p of sp.points) lines.push([p.lam, p.bw ?? '', p.flux.toPrecision(6), p.err.toPrecision(4), p.det, p.t].join(','));
  lines.push('', '# 102-channel binned', 'channel,detector,wavelength_um,lo_um,hi_um,flux_mJy,err_mJy,n');
  for (const c of sp.channels) lines.push([c.ch, c.det, c.lam, c.lo, c.hi, c.flux.toPrecision(6), c.err.toPrecision(4), c.n].join(','));
  download(new Blob([lines.join('\n')], { type: 'text/csv' }), `spectrum_${slug()}.csv`);
});

// ------------------------------------------------------------------ tabs & sky context
function activateTab(name) {
  document.querySelectorAll('.tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === name));
  document.querySelectorAll('.tab-body').forEach((b) => b.classList.toggle('hidden', b.dataset.body !== name));
}
document.querySelectorAll('.tabs button').forEach((b) => b.addEventListener('click', () => activateTab(b.dataset.tab)));
$('btnLoadAladin').addEventListener('click', loadSky);
async function loadSky() {
  try { await sky.load(); updateSky(); } catch (e) {
    $('aladin').innerHTML = `<div class="muted center-msg">${esc(e.message)}<br><button class="btn ghost small" id="btnRetryAladin">Retry</button></div>`;
    $('btnRetryAladin').addEventListener('click', loadSky);
  }
}
function updateSky() {
  if (!S.job) return;
  const g = S.job.meta.grid, proj = makeProjection(g), e = g.n - 0.5;
  const corners = [[-0.5, -0.5], [e, -0.5], [e, e], [-0.5, e]].map(([x, y]) => { const s = proj.toSky(x, y); return [s.ra, s.dec]; });
  sky.show({ ra: g.ra0, dec: g.dec0, fovDeg: (g.n * g.scale) / 3600, corners });
}

// ------------------------------------------------------------------ export
function slug() { return (S.job?.meta.target.name || 'sky').replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, ''); }
function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
$('btnPng').addEventListener('click', () => {
  if (!viewer.data) return;
  $('view').toBlob((b) => b && download(b, `spherex_${slug()}_${S.mode}${S.frame + 1}.png`), 'image/png');
});
$('btnFits').addEventListener('click', () => {
  if (!S.job) return;
  const a = document.createElement('a'); a.href = api.fitsUrl(S.job.id, S.frame, S.mode); a.download = '';
  document.body.appendChild(a); a.click(); a.remove();
});
$('btnRec').addEventListener('click', () => {
  if (S.recording) { S.recording.stop(); return; }
  if (!viewer.data || viewer.data.nFrames < 2) { toast('Need at least two frames to record.'); return; }
  const canvas = $('view');
  if (!canvas.captureStream || typeof MediaRecorder === 'undefined') { toast('Video recording is not supported in this browser.', 'err'); return; }
  const mime = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4'].find((m) => MediaRecorder.isTypeSupported(m));
  if (!mime) { toast('No supported video format in this browser.', 'err'); return; }
  const rec = new MediaRecorder(canvas.captureStream(30), { mimeType: mime, videoBitsPerSecond: 4e6 });
  const chunks = [];
  rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  rec.onstop = () => {
    S.recording = null; $('btnRec').classList.remove('rec-on'); stopPlay();
    download(new Blob(chunks, { type: mime }), `spherex_${slug()}.${mime.includes('mp4') ? 'mp4' : 'webm'}`);
    toast('Video saved.', 'ok');
  };
  S.recording = rec;
  $('btnRec').classList.add('rec-on');
  S.frame = 0; viewer.setFrame(0); updateFrameUI();
  rec.start(250);
  startPlay();
  const loops = Math.max(2, Math.ceil(8 / (viewer.data.nFrames / S.fps)));
  setTimeout(() => { if (S.recording === rec) rec.stop(); }, Math.min(60000, (viewer.data.nFrames / S.fps) * loops * 1000 + 300));
  toast('Recording… press the record button again to stop early.');
});

// ------------------------------------------------------------------ misc UI
$('searchForm').addEventListener('submit', (e) => { e.preventDefault(); run(); });
$('q').addEventListener('input', () => document.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c.textContent === $('q').value)));
$('btnShare').addEventListener('click', async () => {
  const url = writeURL();
  try { await navigator.clipboard.writeText(url); toast('Link copied – it reproduces this exact view.', 'ok'); }
  catch { prompt('Copy this link:', url); }
});
$('btnHelp').addEventListener('click', () => $('helpDlg').showModal());
$('btnSettings').addEventListener('click', () => $('settingsDlg').showModal());
$('setGalaxy').checked = store.get('galaxy', true);
$('setQuality').value = store.get('quality', 'auto');
$('setGalaxy').addEventListener('change', () => { galaxy.setEnabled($('setGalaxy').checked); store.set('galaxy', $('setGalaxy').checked); });
$('setQuality').addEventListener('change', () => { galaxy.setQuality($('setQuality').value); store.set('quality', $('setQuality').value); });

document.addEventListener('keydown', (e) => {
  const tgt = e.target;
  if (tgt.closest && tgt.closest('input, select, textarea')) {
    if (tgt.type !== 'range' && tgt.type !== 'checkbox') return;
    if (tgt.type === 'range' && e.key.startsWith('Arrow')) return; // let sliders use arrow keys
  }
  if (document.querySelector('dialog[open]')) return;
  const k = e.key;
  if (k === ' ') { e.preventDefault(); S.playing ? stopPlay() : startPlay(); }
  else if (k === 'ArrowRight') { e.preventDefault(); stopPlay(); step(1); }
  else if (k === 'ArrowLeft') { e.preventDefault(); stopPlay(); step(-1); }
  else if (k === 'd' || k === 'D') {
    const order = ['none', 'static', 'first', 'prev'];
    const next = order[(order.indexOf(segValue('segDiff')) + 1) % order.length];
    setSeg('segDiff', next); pushDisplayOptions(); writeURL();
  } else if (k === 'm' || k === 'M') { const m = S.mode === 'epoch' ? 'exposure' : 'epoch'; setSeg('segMode', m); switchMode(m); }
  else if (k === 'i' || k === 'I') $('btnInvert').click();
  else if (k === '+' || k === '=') viewer.zoomBy(1.25);
  else if (k === '-' || k === '_') viewer.zoomBy(0.8);
  else if (k === '0') viewer.resetView();
  else if (k === '?') $('helpDlg').showModal();
});

function esc(s) { return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

// ------------------------------------------------------------------ boot
(async function boot() {
  try {
    S.cfg = await api.config();
  } catch (e) {
    toast(e.message, 'err');
    $('emptyState').querySelector('p').textContent = 'The Planet X server is not reachable. Start it with run.bat / run.sh and reload this page.';
    return;
  }
  S.channels = S.cfg.channels;
  buildControls();
  const auto = readURL();
  S.fps = +$('fps').value;
  pushDisplayOptions();
  if (auto) run();
})();
