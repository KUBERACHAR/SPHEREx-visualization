// Lightweight canvas spectrum chart (no chart library): per-exposure points with error bars,
// 102-channel binned spectrum line, detector bands, hover tooltip.

export const DET_COLORS = { 1: '#7f7bff', 2: '#3fb5ff', 3: '#46e0b0', 4: '#f2d14a', 5: '#ff8a4c', 6: '#ff4f6d' };
const DET_RANGES = { 1: [0.75, 1.09], 2: [1.10, 1.62], 3: [1.63, 2.41], 4: [2.42, 3.82], 5: [3.83, 4.41], 6: [4.42, 5.0] };

export class SpectrumChart {
  constructor(canvas, tooltipHost) {
    this.c = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = null;
    this.log = false;
    this.hover = null;
    this.host = tooltipHost;
    this._ro = new ResizeObserver(() => this._resize());
    this._ro.observe(canvas);
    canvas.addEventListener('pointermove', (e) => this._move(e));
    canvas.addEventListener('pointerleave', () => { this.hover = null; this.draw(); });
    this._resize();
  }
  _resize() {
    const r = this.c.getBoundingClientRect();
    this.dpr = Math.min(2, window.devicePixelRatio || 1);
    this.c.width = Math.max(1, Math.round(r.width * this.dpr));
    this.c.height = Math.max(1, Math.round(r.height * this.dpr));
    this.draw();
  }
  set(data) { this.data = data; this.hover = null; this.draw(); }
  setLog(v) { this.log = v; this.draw(); }

  _frame() {
    const d = this.dpr, W = this.c.width, H = this.c.height;
    return { l: 52 * d, r: W - 12 * d, t: 14 * d, b: H - 34 * d, W, H };
  }
  _scales() {
    const f = this._frame();
    const pts = this.data.points;
    const xmin = 0.7, xmax = 5.05;
    let ys = pts.map((p) => p.flux);
    let ymin, ymax;
    if (this.log) {
      const pos = ys.filter((v) => v > 0);
      if (!pos.length) { ymin = 1e-3; ymax = 1; }
      else {
        pos.sort((a, b) => a - b);
        ymin = pos[Math.floor(pos.length * 0.01)] / 1.5; ymax = pos[pos.length - 1] * 1.5;
      }
      const lmin = Math.log10(ymin), lmax = Math.log10(Math.max(ymax, ymin * 10));
      return { f, x: (v) => f.l + ((v - xmin) / (xmax - xmin)) * (f.r - f.l),
               y: (v) => f.b - ((Math.log10(Math.max(v, 10 ** lmin)) - lmin) / (lmax - lmin)) * (f.b - f.t),
               xmin, xmax, ymin: 10 ** lmin, ymax: 10 ** lmax };
    }
    ys = ys.slice().sort((a, b) => a - b);
    const lo = ys[Math.floor(ys.length * 0.02)] ?? 0, hi = ys[Math.ceil(ys.length * 0.98) - 1] ?? 1;
    ymin = Math.min(0, lo); ymax = hi;
    const pad = (ymax - ymin) * 0.12 || Math.abs(ymax) * 0.2 || 1;
    ymin -= pad * 0.5; ymax += pad;
    return { f, x: (v) => f.l + ((v - xmin) / (xmax - xmin)) * (f.r - f.l),
             y: (v) => f.b - ((v - ymin) / (ymax - ymin)) * (f.b - f.t), xmin, xmax, ymin, ymax };
  }

  draw() {
    const ctx = this.ctx, W = this.c.width, H = this.c.height, d = this.dpr;
    ctx.clearRect(0, 0, W, H);
    ctx.font = `${10.5 * d}px system-ui, sans-serif`;
    if (!this.data || !this.data.points.length) {
      ctx.fillStyle = '#6d7599'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText(this.data ? 'No usable measurements at this position' : 'Click a source in the image', W / 2, H / 2);
      return;
    }
    const s = this._scales(), f = s.f;
    // detector bands
    for (const [det, [a, b]] of Object.entries(DET_RANGES)) {
      ctx.fillStyle = DET_COLORS[det] + '14';
      ctx.fillRect(s.x(a), f.t, s.x(b) - s.x(a), f.b - f.t);
      ctx.fillStyle = DET_COLORS[det] + 'aa';
      ctx.fillRect(s.x(a), f.b + 1 * d, s.x(b) - s.x(a), 3 * d);
    }
    // axes & grid
    ctx.strokeStyle = 'rgba(137,160,255,0.14)'; ctx.fillStyle = '#9aa3c7'; ctx.lineWidth = 1 * d;
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    for (let v = 1; v <= 5; v++) {
      const x = s.x(v);
      ctx.beginPath(); ctx.moveTo(x, f.t); ctx.lineTo(x, f.b); ctx.stroke();
      ctx.fillText(`${v}`, x, f.b + 7 * d);
    }
    ctx.fillText('wavelength (µm)', (f.l + f.r) / 2, f.b + 19 * d);
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    const ticks = this.log ? logTicks(s.ymin, s.ymax) : niceTicks(s.ymin, s.ymax, 5);
    for (const v of ticks) {
      const y = s.y(v);
      if (y < f.t - 1 || y > f.b + 1) continue;
      ctx.beginPath(); ctx.moveTo(f.l, y); ctx.lineTo(f.r, y); ctx.stroke();
      ctx.fillText(fmt(v), f.l - 6 * d, y);
    }
    if (!this.log && s.ymin < 0 && s.ymax > 0) {
      ctx.strokeStyle = 'rgba(230,236,255,0.35)';
      ctx.beginPath(); ctx.moveTo(f.l, s.y(0)); ctx.lineTo(f.r, s.y(0)); ctx.stroke();
    }
    ctx.save(); ctx.translate(13 * d, (f.t + f.b) / 2); ctx.rotate(-Math.PI / 2);
    ctx.textAlign = 'center'; ctx.fillStyle = '#9aa3c7'; ctx.fillText(`flux (${this.data.unit})`, 0, 0); ctx.restore();

    ctx.save();
    ctx.beginPath(); ctx.rect(f.l, f.t, f.r - f.l, f.b - f.t); ctx.clip();
    // per-exposure points with error bars
    for (const p of this.data.points) {
      const x = s.x(p.lam), y = s.y(p.flux);
      ctx.strokeStyle = DET_COLORS[p.det] + '88'; ctx.lineWidth = 1 * d;
      ctx.beginPath(); ctx.moveTo(x, s.y(p.flux - p.err)); ctx.lineTo(x, s.y(p.flux + p.err)); ctx.stroke();
      ctx.fillStyle = DET_COLORS[p.det];
      ctx.beginPath(); ctx.arc(x, y, 2.6 * d, 0, Math.PI * 2); ctx.fill();
    }
    // binned 102-channel spectrum
    const ch = this.data.channels;
    if (ch.length > 1) {
      ctx.strokeStyle = 'rgba(255,255,255,0.85)'; ctx.lineWidth = 1.6 * d;
      ctx.beginPath();
      ch.forEach((c, i) => {
        const x = s.x(c.lam), y = s.y(c.flux);
        // do not bridge wavelength gaps wider than ~3 channels (missing data)
        const gap = i && (c.ch - ch[i - 1].ch > 3);
        (i && !gap) ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.stroke();
    }
    // hover
    if (this.hover) {
      const p = this.hover;
      const x = s.x(p.lam), y = s.y(p.flux);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5 * d;
      ctx.beginPath(); ctx.arc(x, y, 5 * d, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.restore();
    if (this.hover) this._tooltip(s);
  }

  _tooltip(s) {
    const ctx = this.ctx, d = this.dpr, p = this.hover;
    const lines = [`λ ${p.lam.toFixed(3)} µm  (D${p.det})`, `${fmt(p.flux)} ± ${fmt(p.err)} ${this.data.unit}`,
      p.date || ''];
    ctx.font = `${10.5 * d}px Consolas, monospace`;
    const w = Math.max(...lines.map((l) => ctx.measureText(l).width)) + 14 * d, h = lines.length * 14 * d + 8 * d;
    let x = s.x(p.lam) + 10 * d, y = s.y(p.flux) - h - 6 * d;
    if (x + w > this.c.width) x = s.x(p.lam) - w - 10 * d;
    if (y < 0) y = s.y(p.flux) + 10 * d;
    ctx.fillStyle = 'rgba(8,10,26,0.95)'; ctx.strokeStyle = 'rgba(137,160,255,0.4)';
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x, y, w, h, 6 * d) : ctx.rect(x, y, w, h); ctx.fill(); ctx.stroke();
    ctx.fillStyle = '#e8ecff'; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    lines.forEach((l, i) => ctx.fillText(l, x + 7 * d, y + 5 * d + i * 14 * d));
  }

  _move(e) {
    if (!this.data || !this.data.points.length) return;
    const r = this.c.getBoundingClientRect();
    const mx = (e.clientX - r.left) * this.dpr, my = (e.clientY - r.top) * this.dpr;
    const s = this._scales();
    let best = null, bd = 14 * this.dpr;
    for (const p of this.data.points) {
      const dd = Math.hypot(s.x(p.lam) - mx, s.y(p.flux) - my);
      if (dd < bd) { bd = dd; best = p; }
    }
    if (best !== this.hover) { this.hover = best; this.draw(); }
  }
}

function niceTicks(a, b, n) {
  const span = b - a;
  if (!(span > 0)) return [a];
  const step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(a / step) * step; v <= b + 1e-12; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}
function logTicks(a, b) {
  const out = [];
  for (let e = Math.floor(Math.log10(a)); e <= Math.ceil(Math.log10(b)); e++) out.push(10 ** e);
  return out;
}
export function fmt(v) {
  const a = Math.abs(v);
  if (a === 0) return '0';
  if (a >= 1000) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 0.1) return v.toFixed(3);
  return v.toPrecision(3);
}
