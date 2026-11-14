"""Module B (part 2) - reprojection, background removal, band stacking, epoch grouping, spectra.

Conventions
-----------
* Output grid: gnomonic (TAN), north up / east left, square pixels of
  ``scale`` arcsec, odd size ``n`` so the target sits on the centre pixel.
* Arrays handed to the client are in *display orientation*: row 0 = north (top).
  Display pixel (cx, cy) == FITS grid pixel (x=cx, y=n-1-cy).
* Surface brightness stays in MJy/sr (bilinear resampling conserves it).
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np
import astropy
from astropy.wcs import WCS

from . import config

astropy.log.setLevel("ERROR")  # SPHEREx headers trigger harmless alternate-WCS SIP notices

_UNIT_TO_UM = {"um": 1.0, "micron": 1.0, "microns": 1.0, "m": 1e6, "nm": 1e-3,
               "angstrom": 1e-4, "a": 1e-4}


# --------------------------------------------------------------------------- grid
@dataclass
class Grid:
    ra0: float
    dec0: float
    n: int
    scale: float          # arcsec / pixel
    ra: np.ndarray = field(repr=False)   # (n, n) FITS orientation
    dec: np.ndarray = field(repr=False)

    def fits_wcs(self) -> WCS:
        return make_wcs(self.ra0, self.dec0, self.n, self.scale)

    def to_dict(self) -> dict:
        return {"ra0": self.ra0, "dec0": self.dec0, "n": self.n, "scale": self.scale}


def make_wcs(ra0: float, dec0: float, n: int, scale: float) -> WCS:
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.cdelt = [-scale / 3600.0, scale / 3600.0]
    w.wcs.crpix = [(n + 1) / 2.0, (n + 1) / 2.0]
    w.wcs.cunit = ["deg", "deg"]
    w.wcs.radesys = "ICRS"
    return w


def make_grid(ra0: float, dec0: float, fov_arcmin: float,
              scale: float = config.SPHEREX_PIXEL_ARCSEC) -> Grid:
    n = int(round(fov_arcmin * 60.0 / scale))
    n = max(9, n + (1 - n % 2))  # odd, >= 9
    w = make_wcs(ra0, dec0, n, scale)
    yy, xx = np.mgrid[0:n, 0:n]
    ra, dec = w.wcs_pix2world(xx.astype(np.float64), yy.astype(np.float64), 0)
    return Grid(ra0, dec0, n, scale, ra, dec)


# --------------------------------------------------------------------------- statistics
def robust_stats(v: np.ndarray) -> tuple[float, float]:
    """Iterative 3-sigma clipped median and MAD-based sigma."""
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan"), float("nan")
    med = float(np.median(v))
    sig = 1.4826 * float(np.median(np.abs(v - med)))
    for _ in range(4):
        if not sig > 0:
            break
        keep = np.abs(v - med) < 3.0 * sig
        if keep.sum() < 10:
            break
        vk = v[keep]
        med = float(np.median(vk))
        new = 1.4826 * float(np.median(np.abs(vk - med)))
        if abs(new - sig) < 1e-3 * sig:
            sig = new
            break
        sig = new
    return med, sig


# --------------------------------------------------------------------------- per exposure
@dataclass
class Plan:
    """Where an exposure's pixels are, computed from its header before downloading pixels."""
    xs: np.ndarray        # (n, n) detector x of each grid pixel (0-based)
    ys: np.ndarray
    inside: np.ndarray    # bool (n, n)
    coverage: float
    x0: int
    x1: int
    y0: int
    y1: int
    lam_um: float         # wavelength at the target (grid centre)
    bw_um: float
    lam3: np.ndarray      # (3, 3) wavelength at display positions {0, mid, n-1}^2
    psf_fwhm: float


def plan_exposure(layout, grid: Grid) -> Plan | None:
    hdr = layout.image_hdr()
    nx, ny = int(hdr["NAXIS1"]), int(hdr["NAXIS2"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        w = WCS(hdr)
        xs, ys = w.all_world2pix(grid.ra, grid.dec, 0, quiet=True)
    xs = np.asarray(xs, np.float64)
    ys = np.asarray(ys, np.float64)
    finite = np.isfinite(xs) & np.isfinite(ys)
    inside = finite & (xs >= -0.5) & (xs <= nx - 0.5) & (ys >= -0.5) & (ys <= ny - 0.5)
    coverage = float(inside.mean())
    if coverage < 0.02:
        return None
    xi, yi = xs[inside], ys[inside]
    x0, x1 = int(math.floor(xi.min())) - 1, int(math.ceil(xi.max())) + 1
    y0, y1 = int(math.floor(yi.min())) - 1, int(math.ceil(yi.max())) + 1
    x0, x1 = max(0, x0), min(nx - 1, x1)
    y0, y1 = max(0, y0), min(ny - 1, y1)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None

    n = grid.n
    c = n // 2
    lam_um = bw_um = float("nan")
    lam3 = np.full((3, 3), np.nan)
    hdul = layout.wave_hdulist()
    if hdul is not None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                sw = WCS(hdr, fobj=hdul, key="W")
                sw.sip = None
                unit = str(hdr.get("CUNIT1W", "um")).strip().lower()
                k = _UNIT_TO_UM.get(unit, 1.0)
                # Display row r corresponds to FITS grid row n-1-r.
                idx = [0, c, n - 1]
                px, py = [], []
                for r in idx:
                    for col in idx:
                        gy = n - 1 - r
                        px.append(np.clip(xs[gy, col], 0, nx - 1))
                        py.append(np.clip(ys[gy, col], 0, ny - 1))
                px.append(np.clip(xs[c, c], 0, nx - 1))
                py.append(np.clip(ys[c, c], 0, ny - 1))
                world = sw.all_pix2world(np.column_stack([px, py]), 0)
                lam3 = (world[:9, 0] * k).reshape(3, 3)
                lam_um, bw_um = float(world[9, 0] * k), float(world[9, 1] * k)
        finally:
            hdul.close()
    return Plan(xs, ys, inside, coverage, x0, x1, y0, y1, lam_um, bw_um, lam3,
                float(hdr.get("PSF_FWHM", float("nan"))))


def bilinear(img: np.ndarray, x: np.ndarray, y: np.ndarray, min_weight: float = 0.5) -> np.ndarray:
    """NaN-aware bilinear sampling of ``img`` at float pixel positions (0-based).

    Weights of invalid (NaN) neighbours are dropped and the rest renormalised when
    at least ``min_weight`` of the kernel remains; positions outside -> NaN.
    """
    h, w = img.shape
    out = np.full(x.shape, np.nan, np.float32)
    if h < 2 or w < 2:
        return out
    ok = np.isfinite(x) & np.isfinite(y) & (x >= -0.5) & (x <= w - 0.5) & (y >= -0.5) & (y <= h - 0.5)
    if not ok.any():
        return out
    xx = np.clip(x[ok], 0.0, w - 1.0)
    yy = np.clip(y[ok], 0.0, h - 1.0)
    ix = np.minimum(np.floor(xx).astype(np.int64), w - 2)
    iy = np.minimum(np.floor(yy).astype(np.int64), h - 2)
    fx = xx - ix
    fy = yy - iy
    acc = np.zeros(xx.shape, np.float64)
    wsum = np.zeros(xx.shape, np.float64)
    for dy, dx, wt in ((0, 0, (1 - fx) * (1 - fy)), (0, 1, fx * (1 - fy)),
                       (1, 0, (1 - fx) * fy), (1, 1, fx * fy)):
        v = img[iy + dy, ix + dx]
        good = np.isfinite(v)
        acc += np.where(good, v, 0.0) * wt
        wsum += np.where(good, wt, 0.0)
    res = np.where(wsum >= min_weight, acc / np.maximum(wsum, 1e-12), np.nan)
    out[ok] = res.astype(np.float32)
    return out


@dataclass
class ExposureResult:
    meta: dict
    image: np.ndarray     # (n, n) display orientation, background subtracted, NaN = no data
    lam3: np.ndarray


def finish_exposure(exp, plan: Plan, grid: Grid, block: np.ndarray, flags: np.ndarray | None,
                    bits: dict[str, int]) -> ExposureResult | None:
    """Resample a downloaded pixel block onto the grid and measure background/noise."""
    img = block.astype(np.float32, copy=True)
    n_bad = 0
    warn_frac: dict[str, float] = {}
    center_flags: list[str] = []
    if flags is not None and bits:
        fblock = flags[:, plan.x0:plan.x0 + img.shape[1]] if flags.shape[1] > img.shape[1] else flags
        if fblock.shape == img.shape:
            bad_mask = 0
            for name in config.BAD_FLAG_NAMES:
                if name in bits:
                    bad_mask |= 1 << bits[name]
            bad = (fblock & bad_mask) != 0
            n_bad = int(bad.sum())
            img[bad] = np.nan
            # nearest-neighbour flag word for each grid pixel
            fx = np.rint(plan.xs - plan.x0).astype(np.int64)
            fy = np.rint(plan.ys - plan.y0).astype(np.int64)
            okf = plan.inside & (fx >= 0) & (fy >= 0) & (fx < fblock.shape[1]) & (fy < fblock.shape[0])
            words = np.zeros(plan.xs.shape, np.int64)
            words[okf] = fblock[fy[okf], fx[okf]]
            ncov = max(1, int(okf.sum()))
            for name in config.WARN_FLAG_NAMES:
                if name in bits:
                    frac = float(((words & (1 << bits[name])) != 0)[okf].sum()) / ncov
                    if frac > 0:
                        warn_frac[name] = round(frac, 4)
            c = grid.n // 2
            if okf[c, c]:
                wc = int(words[c, c])
                center_flags = sorted(nm for nm, b in bits.items() if wc & (1 << b))
    out = bilinear(img, plan.xs - plan.x0, plan.ys - plan.y0)
    out[~plan.inside] = np.nan
    valid = np.isfinite(out)
    if valid.sum() < max(20, 0.02 * out.size):
        return None
    bg, noise = robust_stats(out[valid])
    if not (math.isfinite(bg) and math.isfinite(noise)):
        return None
    out -= np.float32(bg)
    meta = exp.to_dict()
    meta.update({
        "lam_um": _r(plan.lam_um, 5), "bw_um": _r(plan.bw_um, 5), "coverage": _r(float(valid.mean()), 3),
        "bg": _r(bg, 5), "noise": _r(max(noise, 1e-6), 6), "psf_fwhm": _r(plan.psf_fwhm, 2),
        "n_bad_pixels": n_bad, "warn_flags": warn_frac, "center_flags": center_flags,
        "flags_read": flags is not None,
    })
    return ExposureResult(meta, np.flipud(out).copy(), plan.lam3)


def _r(v: float, nd: int):
    return None if v is None or not math.isfinite(v) else round(float(v), nd)


# --------------------------------------------------------------------------- selection
def group_epochs(times: list[float], gap_days: float) -> list[int]:
    """Epoch index for each time (times need not be sorted)."""
    order = sorted(range(len(times)), key=lambda i: times[i])
    labels = [0] * len(times)
    ep, prev = 0, None
    for i in order:
        if prev is not None and times[i] - prev > gap_days:
            ep += 1
        labels[i] = ep
        prev = times[i]
    return labels


def select_round_robin(items: list, keyfunc, cap: int) -> list:
    """Pick up to ``cap`` items spread evenly over groups (e.g. epoch x detector)."""
    if len(items) <= cap:
        return list(items)
    groups: dict = {}
    for it in items:
        groups.setdefault(keyfunc(it), []).append(it)
    # spread picks within each group evenly in time
    queues = []
    for k in sorted(groups):
        g = groups[k]
        order = _spread_order(len(g))
        queues.append([g[i] for i in order])
    out = []
    while len(out) < cap:
        progressed = False
        for q in queues:
            if q and len(out) < cap:
                out.append(q.pop(0))
                progressed = True
        if not progressed:
            break
    return out


def _spread_order(n: int) -> list[int]:
    """Indices 0..n-1 ordered so any prefix is spread across the range."""
    if n <= 2:
        return list(range(n))
    order, seen = [], set()
    k = 1
    while (1 << k) < n:
        k += 1
    for j in range(1 << k):  # van der Corput (bit-reversal) sequence over [0, 1)
        rev = int(format(j, f"0{k}b")[::-1], 2)
        idx = min(n - 1, int(rev / (1 << k) * n))
        if idx not in seen:
            seen.add(idx)
            order.append(idx)
    order.extend(i for i in range(n) if i not in seen)
    return order


# --------------------------------------------------------------------------- stacking
def clipped_weighted_mean(cube: np.ndarray, noise: np.ndarray, kappa: float = 5.0) -> tuple[np.ndarray, np.ndarray, float]:
    """Inverse-variance weighted mean with per-pixel outlier rejection.

    A pixel value is rejected if it deviates from the per-pixel median by more than
    ``kappa`` x max(its exposure noise, robust spread across exposures). This removes
    cosmic rays / hot pixels (they land on different sky pixels in each exposure)
    while keeping genuine brightness differences between wavelengths.
    Returns (mean, n_used, rejected_fraction).
    """
    k = cube.shape[0]
    w = (1.0 / np.maximum(noise, 1e-6) ** 2).astype(np.float32)[:, None, None]
    valid = np.isfinite(cube)
    rejected = 0.0
    if k >= 3:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            med = np.nanmedian(cube, axis=0)
            spread = 1.4826 * np.nanmedian(np.abs(cube - med), axis=0)
        thr = kappa * np.maximum(noise.astype(np.float32)[:, None, None], spread[None])
        dev = np.abs(cube - med[None])
        bad = valid & (dev > thr)
        nvalid = max(1, int(valid.sum()))
        rejected = float(bad.sum()) / nvalid
        valid &= ~bad
    wv = np.where(valid, w, 0.0).astype(np.float32)
    num = np.sum(np.where(valid, cube, 0.0) * wv, axis=0)
    den = np.sum(wv, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(den > 0, num / den, np.nan).astype(np.float32)
    return mean, valid.sum(axis=0).astype(np.int16), rejected


def build_epoch_frames(results: list[ExposureResult], epochs: list[int]) -> tuple[np.ndarray, list[dict], list[str]]:
    """Stack exposures into one frame per epoch with *consistent band weighting*.

    Each detector is stacked separately inside an epoch; detectors present in every
    epoch are then averaged with equal weight. A star's brightness differs strongly
    between 0.75 and 5 um, so mixing detectors in different proportions per epoch
    would fake variability - this avoids it.
    """
    warnings_out: list[str] = []
    ne = max(epochs) + 1 if epochs else 0
    by_ep_det: dict[tuple[int, int], list[int]] = {}
    for i, (r, e) in enumerate(zip(results, epochs)):
        by_ep_det.setdefault((e, r.meta["detector"]), []).append(i)
    dets_per_epoch = [{d for (e, d) in by_ep_det if e == ep} for ep in range(ne)]
    # Reference band set: detectors seen in at least half of the epochs.
    need = max(1, math.ceil(0.5 * ne))
    ref = {d for d in range(1, 7) if sum(d in s for s in dets_per_epoch) >= need}
    frames, metas = [], []
    n_partial = 0
    for ep in range(ne):
        use = sorted(dets_per_epoch[ep] & ref) or sorted(dets_per_epoch[ep])
        complete = set(use) == ref
        n_partial += 0 if complete else 1
        det_means, det_noise = [], []
        idx_all: list[int] = []
        rej_all = []
        for d in use:
            idx = by_ep_det.get((ep, d), [])
            if not idx:
                continue
            cube = np.stack([results[i].image for i in idx])
            noise = np.array([results[i].meta["noise"] for i in idx], np.float32)
            mean, _, rej = clipped_weighted_mean(cube, noise)
            det_means.append(mean)
            det_noise.append(float(np.sqrt(1.0 / np.sum(1.0 / noise ** 2))))
            idx_all.extend(idx)
            rej_all.append(rej)
        if not det_means:
            continue
        stack = np.stack(det_means)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            frame = np.nanmean(stack, axis=0).astype(np.float32)
        noise_f = float(math.sqrt(sum(s * s for s in det_noise)) / len(det_noise))
        times = [results[i].meta["t_mjd"] for i in idx_all]
        lams = [results[i].meta["lam_um"] for i in idx_all if results[i].meta["lam_um"] is not None]
        frames.append(frame)
        metas.append({
            "epoch": ep, "t_mjd": float(np.mean(times)), "t_start": float(min(times)),
            "t_end": float(max(times)), "n_exp": len(idx_all), "detectors": [int(d) for d in use],
            "lam_min": _r(min(lams), 4) if lams else None, "lam_max": _r(max(lams), 4) if lams else None,
            "noise": _r(noise_f, 6), "coverage": _r(float(np.isfinite(frame).mean()), 3),
            "rejected_frac": _r(float(np.mean(rej_all)) if rej_all else 0.0, 5),
            "band_complete": complete,
            "members": idx_all,
        })
    if ne > 1 and len(ref) < len(set().union(*dets_per_epoch)):
        warnings_out.append("For consistent brightness, epoch frames only combine detectors seen in most "
                            f"epochs (D{', D'.join(str(d) for d in sorted(ref))}); each detector has equal weight.")
    if n_partial:
        warnings_out.append(f"{n_partial} epoch frame(s) lack some of those detectors (marked with a warning "
                            "on the timeline), so their brightness is not directly comparable. For strict "
                            "photometry select a single detector.")
    arr = np.stack(frames) if frames else np.zeros((0, 1, 1), np.float32)
    return arr, metas, warnings_out


# --------------------------------------------------------------------------- spectra
def channel_edges() -> list[tuple[int, int, float, float]]:
    """The 102 SPHEREx channels as (channel, detector, lo_um, hi_um).

    Built from the official channel centres: edges are midpoints between neighbouring
    centres of the same detector; the outer edges extend by half a channel. Adjacent
    detectors overlap slightly (e.g. D1 ch17 at 1.102 um vs D2 ch1 at 1.113 um), so
    measurements are always binned within their own detector.
    """
    nch = config.CHANNELS_PER_DETECTOR
    c = config.SPHEREX_CHANNEL_CENTERS_UM
    out = []
    for d in range(1, 7):
        cc = c[(d - 1) * nch:d * nch]
        for i in range(nch):
            a = cc[i] - (cc[1] - cc[0]) / 2 if i == 0 else (cc[i - 1] + cc[i]) / 2
            b = cc[i] + (cc[-1] - cc[-2]) / 2 if i == nch - 1 else (cc[i] + cc[i + 1]) / 2
            out.append(((d - 1) * nch + i + 1, d, a, b))
    return out


def channel_of(det: int, lam: float) -> int | None:
    """Channel number (1..102) of a measurement: nearest channel centre within its detector."""
    if not (isinstance(det, (int, np.integer)) and 1 <= det <= 6) or not math.isfinite(lam):
        return None
    nch = config.CHANNELS_PER_DETECTOR
    cc = config.SPHEREX_CHANNEL_CENTERS_UM[(det - 1) * nch:det * nch]
    i = min(range(nch), key=lambda k: abs(cc[k] - lam))
    return (det - 1) * nch + i + 1


def interp_lam3(lam3: np.ndarray, cx: float, cy: float, n: int) -> float:
    """Bilinear interpolation of a 3x3 wavelength sample at display pixel (cx, cy)."""
    if not np.all(np.isfinite(lam3)):
        v = lam3[np.isfinite(lam3)]
        return float(v.mean()) if v.size else float("nan")
    half = (n - 1) / 2.0
    gx = min(max(cx / half, 0.0), 2.0)
    gy = min(max(cy / half, 0.0), 2.0)
    ix, iy = min(int(gx), 1), min(int(gy), 1)
    fx, fy = gx - ix, gy - iy
    return float((1 - fx) * (1 - fy) * lam3[iy, ix] + fx * (1 - fy) * lam3[iy, ix + 1]
                 + (1 - fx) * fy * lam3[iy + 1, ix] + fx * fy * lam3[iy + 1, ix + 1])


def aperture_spectrum(cube, metas: list[dict], lam3s: np.ndarray, cx: float, cy: float,
                      scale: float, radius_px: float = 1.6, offsets=None) -> dict:
    """Aperture photometry of every exposure at display pixel (cx, cy).

    ``offsets`` optionally gives a per-exposure (dx, dy) display-pixel shift so the
    aperture follows a proper-motion track. Flux in mJy (no aperture correction).
    """
    n = cube.shape[1]
    omega = (scale / 206264.806) ** 2          # sr per pixel
    to_mjy = omega * 1e9                       # MJy/sr * sr = MJy -> mJy
    yy, xx = np.mgrid[0:n, 0:n]
    pts = []
    skipped = 0
    r_in, r_out = radius_px + 2.5, radius_px + 6.0
    for k, m in enumerate(metas):
        dx, dy = (offsets[k] if offsets is not None else (0.0, 0.0))
        px, py = cx + dx, cy + dy
        if not (0 <= px <= n - 1 and 0 <= py <= n - 1):
            continue
        img = np.asarray(cube[k])
        d2 = (xx - px) ** 2 + (yy - py) ** 2
        ap = d2 <= radius_px ** 2
        if ap.sum() == 0:
            continue
        vals = img[ap]
        if not np.all(np.isfinite(vals)):
            skipped += 1  # saturated core / missing pixels: flux would be biased
            continue
        ann = (d2 >= r_in ** 2) & (d2 <= r_out ** 2)
        av = img[ann]
        av = av[np.isfinite(av)]
        if av.size >= 8:
            loc, _ = robust_stats(av)
            if not math.isfinite(loc):
                loc = 0.0
        else:
            loc = 0.0
        nap = int(ap.sum())
        flux = float((vals - loc).sum()) * to_mjy
        err = float(m["noise"]) * math.sqrt(nap) * to_mjy
        lam = interp_lam3(lam3s[k], px, py, n)
        if not math.isfinite(lam):
            continue
        pts.append({"lam": round(lam, 5), "bw": m.get("bw_um"), "flux": flux, "err": err,
                    "det": m["detector"], "t": m["t_mjd"], "i": k})
    pts.sort(key=lambda p: p["lam"])
    by_ch: dict[int, list[dict]] = {}
    for p in pts:
        p["ch"] = channel_of(p["det"], p["lam"])
        if p["ch"] is not None:
            by_ch.setdefault(p["ch"], []).append(p)
    centres = config.SPHEREX_CHANNEL_CENTERS_UM
    chans = []
    for ch, d, a, b in channel_edges():
        sel = by_ch.get(ch)
        if not sel:
            continue
        w = np.array([1.0 / max(p["err"], 1e-12) ** 2 for p in sel])
        f = np.array([p["flux"] for p in sel])
        mean = float((w * f).sum() / w.sum())
        chans.append({"ch": ch, "det": d, "lam": centres[ch - 1], "lo": round(a, 5),
                      "hi": round(b, 5), "flux": mean, "err": float(1.0 / math.sqrt(w.sum())), "n": len(sel)})
    chans.sort(key=lambda c: c["lam"])
    return {"points": pts, "channels": chans, "unit": "mJy", "radius_px": radius_px, "n_skipped": skipped,
            "radius_arcsec": round(radius_px * scale, 2)}
