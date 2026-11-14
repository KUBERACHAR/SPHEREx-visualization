// Canvas image viewer: all display maths (difference imaging, stretch, colour, overlays)
// runs in the browser so every control reacts instantly with no server round trip.
import { lut } from './colormaps.js';
import { makeProjection, propagate } from './coords.js';

const NAN_RGB = [14, 16, 34];
const CAT_COLORS = { gaia: '#4fd1ff', allwise: '#ffb36b', catwise: '#ff6fb5' };

function percentileSorted(sorted, p) {
  if (!sorted.length) return 0;
  const i = Math.min(sorted.length - 1, Math.max(0, (p / 100) * (sorted.length - 1)));
  const a = Math.floor(i), b = Math.min(sorted.length - 1, a + 1);
  return sorted[a] + (sorted[b] - sorted[a]) * (i - a);
}

export class Viewer {
  constructor(canvas, { onHover, onClick } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.off = document.createElement('canvas');
    this.offCtx = this.off.getContext('2d');
    this.onHover = onHover || (() => {});
    this.onClick = onClick || (() => {});
    this.data = null;       // { frames: Float32Array, nFrames, n, times[], grid, target, tRef }
    this.derived = null;    // Float32Array after difference mode
    this.sortedSample = null;
    this.opts = { diff: 'none', stretch: 'asinh', cmap: 'gray', invert: false, smooth: false, loPct: 5, hiPct: 99.5 };
    this.ov = { target: true, compass: true, catalogs: {}, aperture: null };
    this.frame = 0;
    this.zoom = 1; this.cx = 0; this.cy = 0;
    this.limits = [0, 1];
    this._raf = 0;
    this._imgData = null;
    this._bindPointer();
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(canvas);
    this._resize();
  }

  // ------------------------------------------------------------------ data & options
  setData(d) {
    this.data = d;
    this.proj = makeProjection(d.grid);
    this.off.width = this.off.height = d.n;
    this._imgData = this.offCtx.createImageData(d.n, d.n);
    this.frame = Math.min(this.frame, d.nFrames - 1);
    if (this.frame < 0) this.frame = 0;
    this._median = null;
    this.resetView(false);
    this._derive();
    this.render();
  }

  setOptions(o) {
    const prevDiff = this.opts.diff;
    Object.assign(this.opts, o);
    if (this.data && (o.diff !== undefined && o.diff !== prevDiff)) this._derive();
    else if (this.data && (o.loPct !== undefined || o.hiPct !== undefined)) this._computeLimits();
    this.render();
  }

  setOverlays(o) { Object.assign(this.ov, o); this.render(); }
  setFrame(i) {
    if (!this.data) return;
    const n = this.data.nFrames;
    this.frame = ((i % n) + n) % n;
    this.render();
  }

  frameValue(x, y) {
    if (!this.data) return NaN;
    const n = this.data.n;
    const xi = Math.round(x), yi = Math.round(y);
    if (xi < 0 || yi < 0 || xi >= n || yi >= n) return NaN;
    return this.data.frames[this.frame * n * n + yi * n + xi];
  }

  _derive() {
    const { frames, nFrames: F, n } = this.data;
    const N = n * n;
    const mode = this.opts.diff;
    if (mode === 'none' || F < 2) { this.derived = frames; this._computeLimits(); return; }
    const out = new Float32Array(F * N);
    if (mode === 'static') {
      if (!this._median) this._median = this._pixelMedian();
      const m = this._median;
      for (let f = 0; f < F; f++) {
        const o = f * N;
        for (let p = 0; p < N; p++) out[o + p] = frames[o + p] - m[p];
      }
    } else {
      for (let f = 0; f < F; f++) {
        const ref = mode === 'first' ? 0 : (f - 1 + F) % F; // 'prev' wraps so the loop blinks cleanly
        const o = f * N, r = ref * N;
        for (let p = 0; p < N; p++) out[o + p] = frames[o + p] - frames[r + p];
      }
    }
    this.derived = out;
    this._computeLimits();
  }

  _pixelMedian() {
    const { frames, nFrames: F, n } = this.data;
    const N = n * n, m = new Float32Array(N), buf = new Float64Array(F);
    for (let p = 0; p < N; p++) {
      let k = 0;
      for (let f = 0; f < F; f++) { const v = frames[f * N + p]; if (v === v) buf[k++] = v; }
      if (!k) { m[p] = NaN; continue; }
      const s = buf.subarray(0, k).sort();
      m[p] = k % 2 ? s[(k - 1) >> 1] : 0.5 * (s[k / 2 - 1] + s[k / 2]);
    }
    return m;
  }

  _computeLimits() {
    const d = this.derived;
    if (!d) return;
    const diff = this.opts.diff !== 'none' && this.data.nFrames > 1;
    // Sample (<=120k values) across *all* frames: identical scaling for every frame is
    // essential for blinking - otherwise brightness jumps would be stretch artefacts.
    const step = Math.max(1, Math.floor(d.length / 120000));
    const vals = [];
    for (let i = 0; i < d.length; i += step) { const v = d[i]; if (v === v) vals.push(diff ? Math.abs(v) : v); }
    const s = Float32Array.from(vals).sort();
    if (!s.length) { this.limits = [0, 1]; return; }
    if (diff) {
      const lim = Math.max(1e-9, percentileSorted(s, this.opts.hiPct));
      this.limits = [-lim, lim];
    } else {
      let lo = percentileSorted(s, this.opts.loPct), hi = percentileSorted(s, this.opts.hiPct);
      if (!(hi > lo)) hi = lo + 1e-6;
      this.limits = [lo, hi];
    }
  }

  // ------------------------------------------------------------------ view transform
  _resize() {
    const r = this.canvas.getBoundingClientRect();
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    this.dpr = dpr;
    this.canvas.width = Math.max(1, Math.round(r.width * dpr));
    this.canvas.height = Math.max(1, Math.round(r.height * dpr));
    this.render();
  }
  get vs() { // device px per image px
    if (!this.data) return 1;
    return (Math.min(this.canvas.width, this.canvas.height) / this.data.n) * this.zoom;
  }
  resetView(rerender = true) {
    if (!this.data) return;
    this.zoom = 1; this.cx = this.cy = (this.data.n - 1) / 2;
    if (rerender) this.render();
  }
  zoomBy(f, px, py) {
    if (!this.data) return;
    const W = this.canvas.width, H = this.canvas.height;
    if (px === undefined) { px = W / 2; py = H / 2; }
    const before = this.toImg(px, py);
    this.zoom = Math.min(24, Math.max(1, this.zoom * f));
    const vs = this.vs;
    this.cx = before.x - (px - W / 2) / vs;
    this.cy = before.y - (py - H / 2) / vs;
    this._clampPan();
    this.render();
  }
  _clampPan() {
    const n = this.data.n, half = (Math.min(this.canvas.width, this.canvas.height) / this.vs) / 2;
    const lo = Math.min(half - 0.5, (n - 1) / 2), hi = Math.max(n - 0.5 - half, (n - 1) / 2);
    this.cx = Math.min(hi, Math.max(lo, this.cx));
    this.cy = Math.min(hi, Math.max(lo, this.cy));
  }
  toImg(px, py) { // canvas device px -> image px (pixel centres at integers)
    const vs = this.vs;
    return { x: (px - this.canvas.width / 2) / vs + this.cx, y: (py - this.canvas.height / 2) / vs + this.cy };
  }
  toCanvas(x, y) {
    const vs = this.vs;
    return { x: (x - this.cx) * vs + this.canvas.width / 2, y: (y - this.cy) * vs + this.canvas.height / 2 };
  }

  _bindPointer() {
    const c = this.canvas;
    let down = null, moved = false;
    const pos = (e) => {
      const r = c.getBoundingClientRect();
      return { x: (e.clientX - r.left) * this.dpr, y: (e.clientY - r.top) * this.dpr };
    };
    c.addEventListener('pointerdown', (e) => {
      if (!this.data) return;
      down = { ...pos(e), cx: this.cx, cy: this.cy };
      moved = false;
      c.setPointerCapture(e.pointerId);
    });
    c.addEventListener('pointermove', (e) => {
      if (!this.data) return;
      const p = pos(e);
      if (down) {
        const dx = p.x - down.x, dy = p.y - down.y;
        if (!moved && Math.hypot(dx, dy) > 5 * this.dpr) { moved = true; c.classList.add('grabbing'); }
        if (moved && this.zoom > 1) {
          this.cx = down.cx - dx / this.vs; this.cy = down.cy - dy / this.vs;
          this._clampPan(); this.render();
        }
      }
      this._hover(p);
    });
    const end = (e) => {
      if (!down) return;
      const p = pos(e);
      if (!moved && this.data) {
        const im = this.toImg(p.x, p.y);
        const n = this.data.n;
        if (im.x >= -0.5 && im.y >= -0.5 && im.x <= n - 0.5 && im.y <= n - 0.5) {
          this.onClick({ x: Math.min(n - 1, Math.max(0, im.x)), y: Math.min(n - 1, Math.max(0, im.y)) });
        }
      }
      down = null; c.classList.remove('grabbing');
    };
    c.addEventListener('pointerup', end);
    c.addEventListener('pointercancel', () => { down = null; c.classList.remove('grabbing'); });
    c.addEventListener('pointerleave', () => this.onHover(null));
    c.addEventListener('wheel', (e) => {
      if (!this.data) return;
      e.preventDefault();
      const p = pos(e);
      this.zoomBy(e.deltaY < 0 ? 1.25 : 0.8, p.x, p.y);
    }, { passive: false });
  }

  _hover(p) {
    const im = this.toImg(p.x, p.y);
    const n = this.data.n;
    if (im.x < -0.5 || im.y < -0.5 || im.x > n - 0.5 || im.y > n - 0.5) { this.onHover(null); return; }
    const sky = this.proj.toSky(im.x, im.y);
    const xi = Math.round(im.x), yi = Math.round(im.y);
    const N = n * n;
    const value = this.data.frames[this.frame * N + yi * n + xi];
    const shown = this.derived[this.frame * N + yi * n + xi];
    this.onHover({ x: xi, y: yi, ra: sky.ra, dec: sky.dec, value, shown });
  }

  // ------------------------------------------------------------------ drawing
  render() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this._draw(); });
  }

  frameTime(i = this.frame) { return this.data ? this.data.times[i] : 0; }

  _draw() {
    const ctx = this.ctx, W = this.canvas.width, H = this.canvas.height;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, W, H);
    if (!this.data) return;
    const { n } = this.data;
    const N = n * n, base = this.frame * N, d = this.derived;
    const [lo, hi] = this.limits;
    const diff = this.opts.diff !== 'none' && this.data.nFrames > 1;
    const L = lut(this.opts.cmap), px = this._imgData.data;
    const st = this.opts.stretch, inv = this.opts.invert;
    const A = 10, asA = Math.asinh(A);
    const f = st === 'linear' ? (t) => t : st === 'sqrt' ? Math.sqrt
      : st === 'log' ? (t) => Math.log10(1 + 1000 * t) / 3 : (t) => Math.asinh(A * t) / asA;
    const span = hi - lo;
    for (let p = 0; p < N; p++) {
      const v = d[base + p];
      const o = p * 4;
      if (v !== v) { px[o] = NAN_RGB[0]; px[o + 1] = NAN_RGB[1]; px[o + 2] = NAN_RGB[2]; px[o + 3] = 255; continue; }
      let t;
      if (diff) {
        let u = v / hi; u = u > 1 ? 1 : u < -1 ? -1 : u;
        t = 0.5 + 0.5 * (u < 0 ? -f(-u) : f(u));
      } else {
        t = (v - lo) / span; t = t < 0 ? 0 : t > 1 ? 1 : t;
        t = f(t);
      }
      if (inv) t = 1 - t;
      const k = ((t * 255 + 0.5) | 0) * 3;
      px[o] = L[k]; px[o + 1] = L[k + 1]; px[o + 2] = L[k + 2]; px[o + 3] = 255;
    }
    this.offCtx.putImageData(this._imgData, 0, 0);
    const vs = this.vs;
    ctx.imageSmoothingEnabled = !!this.opts.smooth;
    ctx.imageSmoothingQuality = 'high';
    const dx = W / 2 - (this.cx + 0.5) * vs, dy = H / 2 - (this.cy + 0.5) * vs;
    ctx.drawImage(this.off, 0, 0, n, n, dx, dy, n * vs, n * vs);
    this._drawOverlays();
  }

  _drawOverlays() {
    const ctx = this.ctx, dpr = this.dpr, vs = this.vs, d = this.data;
    const t = this.frameTime();
    const lw = 1.5 * dpr;
    // catalogues
    for (const [name, cat] of Object.entries(this.ov.catalogs || {})) {
      if (!cat || !cat.rows) continue;
      ctx.strokeStyle = CAT_COLORS[name] || '#fff';
      ctx.fillStyle = CAT_COLORS[name] || '#fff';
      ctx.lineWidth = 1.2 * dpr;
      const r = Math.max(4 * dpr, 0.85 * vs);
      for (const row of cat.rows) {
        const [ra, dec, pmra, pmdec] = row;
        const pos = propagate(ra, dec, pmra, pmdec, cat.epoch_mjd, t);
        const p = this.proj.toPix(pos.ra, pos.dec);
        if (!p || p.x < -2 || p.y < -2 || p.x > d.n + 1 || p.y > d.n + 1) continue;
        const c = this.toCanvas(p.x, p.y);
        ctx.globalAlpha = 0.85;
        ctx.beginPath(); ctx.arc(c.x, c.y, r, 0, Math.PI * 2); ctx.stroke();
        const pm = Math.hypot(pmra, pmdec);
        if (pm > 100) { // high proper motion: arrow = motion over the next 10 years
          const q = propagate(pos.ra, pos.dec, pmra, pmdec, 0, 3652.5);
          const pq = this.proj.toPix(q.ra, q.dec);
          if (pq) { const cq = this.toCanvas(pq.x, pq.y); this._arrow(c.x, c.y, cq.x, cq.y, lw); }
        }
      }
      ctx.globalAlpha = 1;
    }
    // predicted target track
    const tg = d.target;
    if (this.ov.target && tg) {
      const c0 = { ra: d.grid.ra0, dec: d.grid.dec0 };
      const hasPm = !!(tg.pmra || tg.pmdec);
      if (hasPm && d.nFrames > 1) {
        ctx.strokeStyle = 'rgba(255,200,87,0.55)'; ctx.lineWidth = lw; ctx.setLineDash([3 * dpr, 3 * dpr]);
        ctx.beginPath();
        d.times.forEach((ti, i) => {
          const q = propagate(c0.ra, c0.dec, tg.pmra, tg.pmdec, d.tRef, ti);
          const p = this.proj.toPix(q.ra, q.dec); if (!p) return;
          const c = this.toCanvas(p.x, p.y);
          i ? ctx.lineTo(c.x, c.y) : ctx.moveTo(c.x, c.y);
        });
        ctx.stroke(); ctx.setLineDash([]);
      }
      const q = propagate(c0.ra, c0.dec, tg.pmra || 0, tg.pmdec || 0, d.tRef, t);
      const p = this.proj.toPix(q.ra, q.dec);
      if (p) {
        const c = this.toCanvas(p.x, p.y);
        const r = Math.max(9 * dpr, 2.6 * vs), g = r * 0.55;
        ctx.strokeStyle = '#ffc857'; ctx.lineWidth = lw;
        ctx.beginPath();
        ctx.moveTo(c.x - r - g, c.y); ctx.lineTo(c.x - r + g * 0.3, c.y);
        ctx.moveTo(c.x + r - g * 0.3, c.y); ctx.lineTo(c.x + r + g, c.y);
        ctx.moveTo(c.x, c.y - r - g); ctx.lineTo(c.x, c.y - r + g * 0.3);
        ctx.moveTo(c.x, c.y + r - g * 0.3); ctx.lineTo(c.x, c.y + r + g);
        ctx.stroke();
      }
    }
    // spectrum aperture
    const ap = this.ov.aperture;
    if (ap) {
      let x = ap.x, y = ap.y;
      if (ap.follow && tg && (tg.pmra || tg.pmdec)) {
        const dt = (t - ap.t) / 365.25;
        x += -(tg.pmra * dt / 1000) / d.grid.scale;
        y += -(tg.pmdec * dt / 1000) / d.grid.scale;
      }
      const c = this.toCanvas(x, y);
      ctx.strokeStyle = '#57e3a1'; ctx.lineWidth = lw;
      ctx.beginPath(); ctx.arc(c.x, c.y, ap.r * vs, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 0.35;
      ctx.beginPath(); ctx.arc(c.x, c.y, (ap.r + 2.5) * vs, 0, Math.PI * 2); ctx.arc(c.x, c.y, (ap.r + 6) * vs, 0, Math.PI * 2); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (this.ov.compass) this._compass();
  }

  _arrow(x0, y0, x1, y1, lw) {
    const ctx = this.ctx;
    const a = Math.atan2(y1 - y0, x1 - x0), L = Math.hypot(x1 - x0, y1 - y0);
    if (L < 3) return;
    const h = Math.min(8 * this.dpr, L * 0.4);
    ctx.lineWidth = lw;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
    ctx.lineTo(x1 - h * Math.cos(a - 0.45), y1 - h * Math.sin(a - 0.45));
    ctx.moveTo(x1, y1); ctx.lineTo(x1 - h * Math.cos(a + 0.45), y1 - h * Math.sin(a + 0.45));
    ctx.stroke();
  }

  _compass() {
    const ctx = this.ctx, dpr = this.dpr, W = this.canvas.width, H = this.canvas.height;
    const L = 26 * dpr, x = W - 22 * dpr, y = H - 46 * dpr;
    ctx.save();
    ctx.strokeStyle = 'rgba(230,236,255,0.85)'; ctx.fillStyle = 'rgba(230,236,255,0.9)';
    ctx.font = `${11 * dpr}px system-ui, sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    this._arrow(x, y, x, y - L, 1.4 * dpr); ctx.fillText('N', x, y - L - 9 * dpr);
    this._arrow(x, y, x - L, y, 1.4 * dpr); ctx.fillText('E', x - L - 9 * dpr, y);
    // scale bar: 1 arcmin (or 30" if too large)
    let arcsec = 60, len = (arcsec / this.data.grid.scale) * this.vs;
    if (len > W * 0.35) { arcsec = 30; len /= 2; }
    if (len > W * 0.35) { arcsec = 10; len = (arcsec / this.data.grid.scale) * this.vs; }
    const bx = W - 14 * dpr - len, by = H - 14 * dpr;
    ctx.lineWidth = 2 * dpr; ctx.beginPath(); ctx.moveTo(bx, by); ctx.lineTo(bx + len, by); ctx.stroke();
    ctx.textAlign = 'center'; ctx.fillText(arcsec >= 60 ? `${arcsec / 60}′` : `${arcsec}″`, bx + len / 2, by - 9 * dpr);
    ctx.restore();
  }

  toPNG() { return this.canvas.toDataURL('image/png'); }
}
