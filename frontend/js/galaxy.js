// Animated spiral-galaxy background.
// Designed for low-end machines: pre-rendered sprites, typed arrays, capped frame rate,
// reduced internal resolution, automatic pause when hidden, honours prefers-reduced-motion.

const TIERS = {
  low:    { stars: 160, particles: 650,  fps: 24, scale: 0.6 },
  medium: { stars: 300, particles: 1500, fps: 30, scale: 0.8 },
  high:   { stars: 480, particles: 2600, fps: 40, scale: 1.0 },
};

function autoTier() {
  const mem = navigator.deviceMemory || 4;
  const cores = navigator.hardwareConcurrency || 4;
  const px = window.screen ? window.screen.width * window.screen.height : 2e6;
  if (mem <= 2 || cores <= 2) return 'low';
  if (mem >= 8 && cores >= 8 && px <= 4.2e6) return 'high';
  return 'medium';
}

function makeSprite(rgb, size = 32) {
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  grd.addColorStop(0, `rgba(255,255,255,1)`);
  grd.addColorStop(0.18, `rgba(${rgb},0.95)`);
  grd.addColorStop(0.5, `rgba(${rgb},0.25)`);
  grd.addColorStop(1, `rgba(${rgb},0)`);
  g.fillStyle = grd;
  g.fillRect(0, 0, size, size);
  return c;
}

function gauss() { // Box-Muller, cheap enough at init
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export function startGalaxy(canvas) {
  const ctx = canvas.getContext('2d', { alpha: false });
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const palette = [
    '255,226,180', // core warm
    '255,196,140',
    '190,210,255', // arm blue-white
    '140,170,255',
    '200,150,255', // violet
    '255,140,200', // pink HII
  ];
  const sprites = palette.map((p) => makeSprite(p));
  let tierName = 'medium', tier = TIERS.medium;
  let w = 0, h = 0, cw = 0, ch = 0, scale = 1;
  let bg = null;           // pre-rendered nebula + static stars
  let stars = null;        // twinkling stars: x, y, size, phase, speed
  let parts = null;        // galaxy particles: r, theta0, omega, size, sprite, alpha
  let enabled = true, running = false, raf = 0, last = 0, t0 = performance.now();
  let mx = 0.5, my = 0.5, pmx = 0.5, pmy = 0.5;
  let shoot = null, nextShoot = 4000;

  function build() {
    const n = tier.particles;
    parts = {
      r: new Float32Array(n), th: new Float32Array(n), om: new Float32Array(n),
      s: new Float32Array(n), sp: new Uint8Array(n), a: new Float32Array(n),
    };
    const arms = 3, wind = 1.35;
    for (let i = 0; i < n; i++) {
      const core = i < n * 0.16;
      let t, theta;
      if (core) {
        t = Math.abs(gauss()) * 0.09;
        theta = Math.random() * Math.PI * 2;
      } else {
        t = 0.06 + Math.pow(Math.random(), 0.9) * 0.94;
        const arm = i % arms;
        theta = (arm / arms) * Math.PI * 2 + t * wind * Math.PI * 2 + gauss() * (0.22 + 0.18 * t);
        t += gauss() * 0.025;
      }
      t = Math.max(0.004, t);
      parts.r[i] = t;
      parts.th[i] = theta;
      parts.om[i] = 0.055 / (0.18 + t);                 // differential rotation (rad/s)
      parts.s[i] = core ? 6 + Math.random() * 10 : 3 + Math.random() * 6 * (1 - t * 0.5);
      let sp;
      if (core) sp = Math.random() < 0.7 ? 0 : 1;
      else if (Math.random() < 0.07) sp = 5;
      else sp = t < 0.35 ? (Math.random() < 0.5 ? 1 : 2) : (Math.random() < 0.6 ? 3 : (Math.random() < 0.6 ? 2 : 4));
      parts.sp[i] = sp;
      parts.a[i] = core ? 0.35 + Math.random() * 0.35 : 0.25 + Math.random() * 0.6;
    }
    const ns = tier.stars;
    stars = { x: new Float32Array(ns), y: new Float32Array(ns), s: new Float32Array(ns),
              ph: new Float32Array(ns), f: new Float32Array(ns), c: new Uint8Array(ns) };
    for (let i = 0; i < ns; i++) {
      stars.x[i] = Math.random(); stars.y[i] = Math.random();
      stars.s[i] = 2 + Math.random() * Math.random() * 7;
      stars.ph[i] = Math.random() * 6.283; stars.f[i] = 0.4 + Math.random() * 1.8;
      stars.c[i] = Math.random() < 0.75 ? 2 : (Math.random() < 0.5 ? 1 : 4);
    }
  }

  function renderBackground() {
    bg = document.createElement('canvas');
    bg.width = cw; bg.height = ch;
    const g = bg.getContext('2d');
    g.fillStyle = '#04050d';
    g.fillRect(0, 0, cw, ch);
    const neb = [
      [0.72, 0.38, 0.55, 'rgba(92,60,200,0.30)'], [0.85, 0.15, 0.35, 'rgba(40,110,220,0.22)'],
      [0.12, 0.85, 0.5, 'rgba(20,90,160,0.22)'], [0.35, 0.2, 0.3, 'rgba(170,50,140,0.12)'],
      [0.6, 0.75, 0.4, 'rgba(60,40,140,0.18)'],
    ];
    for (const [x, y, r, c] of neb) {
      const R = r * Math.max(cw, ch);
      const grd = g.createRadialGradient(x * cw, y * ch, 0, x * cw, y * ch, R);
      grd.addColorStop(0, c); grd.addColorStop(1, 'rgba(0,0,0,0)');
      g.fillStyle = grd; g.fillRect(0, 0, cw, ch);
    }
    // faint static star dust
    const dust = Math.round((cw * ch) / 900);
    for (let i = 0; i < dust; i++) {
      const a = Math.random() * 0.55;
      g.fillStyle = `rgba(${Math.random() < 0.8 ? '220,228,255' : '255,220,190'},${a})`;
      const s = Math.random() < 0.92 ? 1 : 1.6;
      g.fillRect(Math.random() * cw, Math.random() * ch, s * scale, s * scale);
    }
  }

  function resize() {
    w = window.innerWidth; h = window.innerHeight;
    scale = tier.scale;
    cw = Math.max(1, Math.round(w * scale)); ch = Math.max(1, Math.round(h * scale));
    canvas.width = cw; canvas.height = ch;
    renderBackground();
    draw(performance.now());
  }

  function draw(now) {
    if (!bg) return;
    const t = (now - t0) / 1000;
    pmx += (mx - pmx) * 0.04; pmy += (my - pmy) * 0.04;
    ctx.globalCompositeOperation = 'source-over';
    ctx.globalAlpha = 1;
    ctx.drawImage(bg, 0, 0);
    ctx.globalCompositeOperation = 'lighter';
    // twinkling stars (slight parallax)
    const ox = (pmx - 0.5) * 10 * scale, oy = (pmy - 0.5) * 10 * scale;
    for (let i = 0; i < stars.x.length; i++) {
      const tw = 0.55 + 0.45 * Math.sin(t * stars.f[i] + stars.ph[i]);
      ctx.globalAlpha = tw * 0.9;
      const s = stars.s[i] * scale;
      ctx.drawImage(sprites[stars.c[i]], stars.x[i] * cw + ox - s / 2, stars.y[i] * ch + oy - s / 2, s, s);
    }
    // galaxy
    const wide = w > 900;
    const cx = cw * (wide ? 0.66 : 0.5) - (pmx - 0.5) * 24 * scale;
    const cy = ch * (wide ? 0.44 : 0.36) - (pmy - 0.5) * 18 * scale;
    const R = Math.min(cw, ch) * (wide ? 0.46 : 0.42);
    const tilt = 0.42, phi = -0.42, cphi = Math.cos(phi), sphi = Math.sin(phi);
    // core glow
    const cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 0.32);
    cg.addColorStop(0, 'rgba(255,236,200,0.55)');
    cg.addColorStop(0.35, 'rgba(255,170,110,0.18)');
    cg.addColorStop(1, 'rgba(120,80,200,0)');
    ctx.globalAlpha = 1;
    ctx.fillStyle = cg;
    ctx.fillRect(cx - R * 0.4, cy - R * 0.4, R * 0.8, R * 0.8);
    const P = parts, n = P.r.length;
    for (let i = 0; i < n; i++) {
      const ang = P.th[i] + P.om[i] * t;
      const rr = P.r[i] * R;
      const x = Math.cos(ang) * rr, y = Math.sin(ang) * rr * tilt;
      const X = cx + x * cphi - y * sphi, Y = cy + x * sphi + y * cphi;
      const s = P.s[i] * scale;
      ctx.globalAlpha = P.a[i];
      ctx.drawImage(sprites[P.sp[i]], X - s / 2, Y - s / 2, s, s);
    }
    // shooting star
    if (shoot) {
      const k = (now - shoot.start) / shoot.dur;
      if (k >= 1) shoot = null;
      else {
        const x = shoot.x + shoot.dx * k, y = shoot.y + shoot.dy * k;
        const grd = ctx.createLinearGradient(x, y, x - shoot.dx * 0.18, y - shoot.dy * 0.18);
        grd.addColorStop(0, 'rgba(255,255,255,0.9)'); grd.addColorStop(1, 'rgba(160,200,255,0)');
        ctx.globalAlpha = Math.sin(k * Math.PI);
        ctx.strokeStyle = grd; ctx.lineWidth = 1.6 * scale;
        ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - shoot.dx * 0.18, y - shoot.dy * 0.18); ctx.stroke();
      }
    } else if (now - t0 > nextShoot) {
      nextShoot = now - t0 + 6000 + Math.random() * 9000;
      shoot = { start: now, dur: 900 + Math.random() * 500, x: Math.random() * cw * 0.8 + cw * 0.1,
                y: Math.random() * ch * 0.4, dx: (Math.random() < 0.5 ? -1 : 1) * cw * 0.25, dy: ch * 0.18 };
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
  }

  function loop(now) {
    if (!running) return;
    raf = requestAnimationFrame(loop);
    if (now - last < 1000 / tier.fps) return;
    last = now;
    draw(now);
  }

  function start() {
    if (running || !enabled || reduced || document.hidden) return;
    running = true; last = 0;
    raf = requestAnimationFrame(loop);
  }
  function stop() { running = false; cancelAnimationFrame(raf); }

  function setQuality(q) {
    tierName = q === 'auto' || !TIERS[q] ? autoTier() : q;
    tier = TIERS[tierName];
    build(); resize();
  }
  function setEnabled(on) {
    enabled = !!on;
    canvas.style.opacity = enabled ? '1' : '0';
    if (enabled) { draw(performance.now()); start(); } else stop();
  }

  let rt = 0;
  window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(resize, 150); });
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  window.addEventListener('pointermove', (e) => { mx = e.clientX / Math.max(1, w); my = e.clientY / Math.max(1, h); }, { passive: true });

  setQuality('auto');
  start();
  return { setEnabled, setQuality, get tier() { return tierName; } };
}
