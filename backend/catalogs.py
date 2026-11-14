"""Module D - multi-mission context catalogues (Gaia DR3, AllWISE, CatWISE2020) via VizieR mirrors."""
from __future__ import annotations

import time

from . import config, net

# name -> (vizier source, columns [ra, dec, pmra, pmdec, mag], pm unit factor to mas/yr, epoch MJD, label)
CATALOGS = {
    "gaia": ("I/355/gaiadr3", ["RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "Gmag"], 1.0, 57388.5,
             "Gaia DR3 (epoch 2016.0)", "G"),
    "allwise": ("II/328/allwise", ["RAJ2000", "DEJ2000", None, None, "W2mag"], 1.0, 55400.0,
                "AllWISE (epoch ~2010.5)", "W2"),
    # CatWISE2020 positions refer to MJD 57170 (2015.4); proper motions in arcsec/yr.
    "catwise": ("II/365/catwise", ["RA_ICRS", "DE_ICRS", "pmRA", "pmDE", "W2mproPM"], 1000.0, 57170.0,
                "CatWISE2020 (epoch 2015.4)", "W2"),
}
# Magnitude limits roughly matched to what single SPHEREx epochs detect (6.15" pixels);
# fainter catalogue entries would only clutter the overlay.
MAG_LIMITS = {"gaia": ("Gmag", 19.0), "allwise": ("W2mag", 16.0), "catwise": ("W2mproPM", 16.5)}
_cache: dict[str, tuple[float, dict]] = {}


def _parse_tsv(text: str, want: list[str]) -> list[dict] | None:
    """Parse VizieR ASU-TSV, selecting columns by header name (robust to column order).

    Returns None if the response has no header row containing the wanted columns.
    """
    rows: list[dict] = []
    names: list[str] | None = None
    idx: list[int] = []
    stage = 0  # 0 = expect names, 1 = expect units/dashes, 2 = data
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if stage == 0:
            names = [c.strip() for c in line.split("\t")]
            if not all(w in names for w in want):
                return None
            idx = [names.index(w) for w in want]
            stage = 1
            continue
        if stage == 1:
            if set(line.replace("\t", "").strip()) <= {"-"}:
                stage = 2  # the dashes line follows the units line
            continue
        parts = line.split("\t")
        if len(parts) <= max(idx):
            continue
        rec = {}
        for w, k in zip(want, idx):
            try:
                rec[w] = float(parts[k])
            except ValueError:
                rec[w] = None
        if rec[want[0]] is None or rec[want[1]] is None:
            continue
        rows.append(rec)
    return rows if names is not None else []


async def cone(cat: str, ra: float, dec: float, radius_arcsec: float, limit: int = 600) -> dict:
    if cat not in CATALOGS:
        raise ValueError(f"unknown catalogue '{cat}'")
    radius_arcsec = max(5.0, min(radius_arcsec, 720.0))
    key = f"{cat}|{ra:.5f}|{dec:.5f}|{radius_arcsec:.0f}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    src, cols, pm_factor, epoch, label, band = CATALOGS[cat]
    out_cols = [c for c in cols if c]
    params = {
        "-source": src, "-c": f"{ra:.6f} {dec:+.6f}", "-c.rs": f"{radius_arcsec:.1f}",
        "-out.max": str(limit), "-sort": "_r",
    }
    # VizieR returns no CatWISE rows when its columns are restricted with -out (verified
    # 2026-11), so that table is fetched with -out.all and columns are picked by name.
    if cat == "catwise":
        params["-out.all"] = "1"  # note: an empty value ("-out.all=") is ignored by VizieR
    else:
        params["-out"] = ",".join(out_cols)
    mag_col, mag_lim = MAG_LIMITS[cat]
    params[mag_col] = f"<{mag_lim}"
    last = "no mirror reachable"
    for host in config.VIZIER_HOSTS:
        try:
            r = await net.get(f"{host}/viz-bin/asu-tsv", params=params, retries=1, timeout=25.0)
        except net.FetchError as exc:
            last = str(exc)
            continue
        if "<html" in r.text[:500].lower():
            last = f"{host} unavailable"
            continue
        raw = _parse_tsv(r.text, out_cols)
        if raw is None:
            last = f"{host}: unexpected response"
            continue
        rows = []
        for m in raw:
            pmra = m.get(cols[2]) if cols[2] else None
            pmde = m.get(cols[3]) if cols[3] else None
            rows.append([round(m[cols[0]], 7), round(m[cols[1]], 7),
                         round(pmra * pm_factor, 3) if pmra is not None else 0.0,
                         round(pmde * pm_factor, 3) if pmde is not None else 0.0,
                         m.get(cols[4])])
        res = {"cat": cat, "label": label, "band": band, "epoch_mjd": epoch, "rows": rows,
               "mirror": host, "mag_limit": f"{band} < {mag_lim}"}
        _cache[key] = (time.time(), res)
        if len(_cache) > 128:
            _cache.pop(min(_cache, key=lambda x: _cache[x][0]), None)
        return res
    raise net.FetchError(f"VizieR query failed ({last})")
