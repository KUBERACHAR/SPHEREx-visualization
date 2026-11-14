// Sky-coordinate helpers: formatting, gnomonic (TAN) projection matching the server grid,
// proper-motion offsets and MJD dates. Display pixel (x, y) has y = 0 at the top (north).

const D2R = Math.PI / 180;

export function fmtRA(deg) {
  let h = (((deg % 360) + 360) % 360) / 15;
  let hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ss = ((h - hh) * 60 - mm) * 60;
  if (ss >= 59.995) { ss = 0; mm += 1; }
  if (mm >= 60) { mm = 0; hh = (hh + 1) % 24; }
  return `${String(hh).padStart(2, '0')}h${String(mm).padStart(2, '0')}m${ss.toFixed(2).padStart(5, '0')}s`;
}

export function fmtDec(deg) {
  const sign = deg < 0 ? '−' : '+';
  let a = Math.abs(deg);
  let dd = Math.floor(a), mm = Math.floor((a - dd) * 60), ss = ((a - dd) * 60 - mm) * 60;
  if (ss >= 59.95) { ss = 0; mm += 1; }
  if (mm >= 60) { mm = 0; dd += 1; }
  return `${sign}${String(dd).padStart(2, '0')}°${String(mm).padStart(2, '0')}′${ss.toFixed(1).padStart(4, '0')}″`;
}

export function mjdToDate(mjd) {
  return new Date(Date.UTC(1858, 10, 17) + mjd * 86400000);
}
export function fmtDate(mjd) {
  return mjdToDate(mjd).toISOString().slice(0, 10);
}
export function fmtDateTime(mjd) {
  return mjdToDate(mjd).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
}

/** Projection for a server grid {ra0, dec0, n, scale(arcsec/px)}. */
export function makeProjection(grid) {
  const a0 = grid.ra0 * D2R, d0 = grid.dec0 * D2R;
  const sd0 = Math.sin(d0), cd0 = Math.cos(d0);
  const c = (grid.n - 1) / 2;
  const s = grid.scale / 3600; // deg per px
  return {
    /** sky (deg) -> display pixel {x, y} (null if behind the tangent plane) */
    toPix(ra, dec) {
      const a = ra * D2R, d = dec * D2R;
      const cosc = sd0 * Math.sin(d) + cd0 * Math.cos(d) * Math.cos(a - a0);
      if (cosc <= 0) return null;
      const xi = (Math.cos(d) * Math.sin(a - a0)) / cosc / D2R;            // east, deg
      const eta = (cd0 * Math.sin(d) - sd0 * Math.cos(d) * Math.cos(a - a0)) / cosc / D2R; // north
      return { x: c - xi / s, y: c - eta / s };
    },
    /** display pixel -> sky (deg) */
    toSky(x, y) {
      const xi = -(x - c) * s * D2R, eta = -(y - c) * s * D2R;
      const rho = Math.hypot(xi, eta);
      if (rho === 0) return { ra: grid.ra0, dec: grid.dec0 };
      const cc = Math.atan(rho), sc = Math.sin(cc), ccos = Math.cos(cc);
      const dec = Math.asin(ccos * sd0 + (eta * sc * cd0) / rho);
      const ra = a0 + Math.atan2(xi * sc, rho * cd0 * ccos - eta * sd0 * sc);
      return { ra: (((ra / D2R) % 360) + 360) % 360, dec: dec / D2R };
    },
  };
}

/** Linear proper-motion propagation (mas/yr, pmra includes cos dec); fine over decades. */
export function propagate(ra, dec, pmra, pmdec, fromMjd, toMjd) {
  if (!pmra && !pmdec) return { ra, dec };
  const dt = (toMjd - fromMjd) / 365.25;
  const cosd = Math.max(1e-6, Math.cos(dec * D2R));
  return { ra: ra + (pmra * dt) / 3.6e6 / cosd, dec: dec + (pmdec * dt) / 3.6e6 };
}
