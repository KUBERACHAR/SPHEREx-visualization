// 256-entry colour look-up tables built from a few control points (tiny, no data files).

const STOPS = {
  gray: [[0, 0, 0, 0], [1, 255, 255, 255]],
  cosmic: [[0, 2, 2, 10], [0.25, 30, 20, 110], [0.5, 140, 40, 170], [0.75, 255, 120, 120], [0.9, 255, 210, 150], [1, 255, 255, 255]],
  inferno: [[0, 0, 0, 4], [0.15, 31, 12, 72], [0.3, 85, 15, 109], [0.45, 136, 34, 106], [0.6, 186, 54, 85],
            [0.75, 227, 89, 51], [0.87, 249, 140, 10], [0.95, 249, 201, 50], [1, 252, 255, 164]],
  ice: [[0, 0, 0, 0], [0.3, 10, 40, 110], [0.6, 40, 140, 210], [0.85, 160, 225, 255], [1, 255, 255, 255]],
  // diverging, black at the centre - natural for difference images on a dark UI
  redblue: [[0, 120, 190, 255], [0.25, 40, 90, 230], [0.5, 0, 0, 0], [0.75, 230, 80, 30], [1, 255, 220, 120]],
};

const cache = new Map();

export function lut(name) {
  if (cache.has(name)) return cache.get(name);
  const stops = STOPS[name] || STOPS.gray;
  const out = new Uint8ClampedArray(256 * 3);
  for (let i = 0; i < 256; i++) {
    const t = i / 255;
    let k = 0;
    while (k < stops.length - 2 && t > stops[k + 1][0]) k++;
    const [t0, r0, g0, b0] = stops[k], [t1, r1, g1, b1] = stops[k + 1];
    const f = t1 > t0 ? Math.min(1, Math.max(0, (t - t0) / (t1 - t0))) : 0;
    out[i * 3] = r0 + (r1 - r0) * f;
    out[i * 3 + 1] = g0 + (g1 - g0) * f;
    out[i * 3 + 2] = b0 + (b1 - b0) * f;
  }
  cache.set(name, out);
  return out;
}

export const NAMES = Object.keys(STOPS);
