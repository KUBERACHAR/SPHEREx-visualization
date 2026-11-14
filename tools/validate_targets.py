"""Live validation against the five mandatory challenge targets.

For every target this runs the real pipeline (IRSA archive, range reads, stacking) and
measures, in the stacked epoch frames:
  * detection significance at the proper-motion-predicted position,
  * the observed centroid motion between the first and last epoch vs. the catalogue
    proper motion (movers), and
  * the epoch-to-epoch brightness change (variables).

Usage:  python tools/validate_targets.py [--fov 4] [--out VALIDATION.md]
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from backend import cache, jobs, net, processing, resolver  # noqa: E402

CASES = [
    # name, detectors, what to check
    ("Barnard's Star", (1, 2, 3, 4, 5, 6), "mover"),
    ("Epsilon Indi B", (1, 2, 3, 4, 5, 6), "mover"),
    ("WISE 0855", (5, 6), "mover"),          # a Y dwarf: only visible beyond ~3.8 um
    ("RW Aurigae", (1, 2, 3, 4, 5, 6), "variable"),
    ("R Draconis", (1, 2, 3, 4, 5, 6), "variable"),
]


def predicted_offset(meta, t):
    """Display-pixel offset of the target at time t relative to the grid centre."""
    tg, g = meta["target"], meta["grid"]
    dt = (t - meta["center"]["t_ref_mjd"]) / 365.25
    return -(tg["pmra"] * dt / 1000.0) / g["scale"], -(tg["pmdec"] * dt / 1000.0) / g["scale"]


def centroid(img, x0, y0, half=2):
    n = img.shape[0]
    xi, yi = int(round(x0)), int(round(y0))
    ys, xs = np.mgrid[max(0, yi - half):min(n, yi + half + 1), max(0, xi - half):min(n, xi + half + 1)]
    v = img[ys, xs]
    w = np.where(np.isfinite(v) & (v > 0), v, 0.0)
    if w.sum() <= 0:
        return None
    return float((w * xs).sum() / w.sum()), float((w * ys).sum() / w.sum())


async def run_case(name, dets, kind, fov):
    m = jobs.JobManager()
    t0 = time.time()
    job = m.submit(jobs.JobParams.build(name, fov, detectors=dets))
    while job.status not in ("done", "error", "cancelled"):
        await asyncio.sleep(0.5)
    if job.status != "done":
        return {"name": name, "ok": False, "error": job.error}
    meta, frames = job.result.meta, job.result.frames
    n = meta["grid"]["n"]
    c = (n - 1) / 2
    rows = []
    for i, ep in enumerate(meta["epochs"]):
        dx, dy = predicted_offset(meta, ep["t_mjd"])
        px, py = c + dx, c + dy
        img = frames[i]
        val = img[int(round(py)), int(round(px))]
        peak = np.nanmax(img[max(0, int(py) - 1):int(py) + 3, max(0, int(px) - 1):int(px) + 3])
        snr = float(peak / ep["noise"]) if ep["noise"] else float("nan")
        cen = centroid(img, px, py)
        ap = processing.aperture_spectrum(frames[i:i + 1], [{"noise": ep["noise"], "detector": 0, "t_mjd": ep["t_mjd"]}],
                                          np.full((1, 3, 3), 1.0), px, py, meta["grid"]["scale"], 2.5)
        flux = ap["points"][0]["flux"] if ap["points"] else float("nan")
        rows.append({"epoch": i + 1, "t": ep["t_mjd"], "pred": (px, py), "cen": cen, "snr": snr, "val": float(val),
                     "flux": flux, "n_exp": ep["n_exp"]})
    res = {"name": name, "ok": True, "meta": meta, "rows": rows, "secs": time.time() - t0, "kind": kind, "dets": dets}
    if len(rows) >= 2 and rows[0]["cen"] and rows[-1]["cen"]:
        s = meta["grid"]["scale"]
        ox = (rows[-1]["cen"][0] - rows[0]["cen"][0]) * s
        oy = (rows[-1]["cen"][1] - rows[0]["cen"][1]) * s
        px_ = (rows[-1]["pred"][0] - rows[0]["pred"][0]) * s
        py_ = (rows[-1]["pred"][1] - rows[0]["pred"][1]) * s
        res["motion_obs"] = (ox, oy)
        res["motion_pred"] = (px_, py_)
    return res


def report(results, fov) -> str:
    out = ["# Validation against the mandatory challenge targets", "",
           f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} by `tools/validate_targets.py` "
           f"against the live NASA/IPAC SPHEREx archive (field of view {fov}′, default settings unless noted).", "",
           "Display-pixel convention: x grows to the west, y grows to the south (north up, east left). "
           "Motion values are in arcsec on the sky, measured between the first and last epoch stacks "
           "with a flux-weighted centroid (SPHEREx pixels are 6.15″, so sub-pixel motions are close to the "
           "measurement limit).", "",
           "| Target | Images found / used | Epochs | Data read | Time | Peak S/N at predicted position (per epoch) | Result |",
           "|---|---|---|---|---|---|---|"]
    details = []
    for r in results:
        if not r["ok"]:
            out.append(f"| {r['name']} | – | – | – | – | – | ❌ {r['error']} |")
            continue
        m, st = r["meta"], r["meta"]["stats"]
        snrs = ", ".join(f"{x['snr']:.0f}" for x in r["rows"])
        verdict = []
        if all(x["snr"] > 5 for x in r["rows"]):
            verdict.append("detected in every epoch")
        else:
            verdict.append("detected in %d/%d epochs" % (sum(x["snr"] > 5 for x in r["rows"]), len(r["rows"])))
        if r["kind"] == "mover" and "motion_obs" in r:
            ox, oy = r["motion_obs"]; px, py = r["motion_pred"]
            verdict.append(f"moved {math.hypot(ox, oy):.1f}″ (predicted {math.hypot(px, py):.1f}″)")
        if r["kind"] == "variable":
            fl = [x["flux"] for x in r["rows"] if math.isfinite(x["flux"])]
            if len(fl) >= 2 and min(fl) > 0:
                verdict.append(f"aperture flux varies ×{max(fl) / min(fl):.2f} between epochs")
            else:
                pk = [x["snr"] * r["meta"]["epochs"][i]["noise"] for i, x in enumerate(r["rows"])]
                verdict.append(f"core saturated, so no aperture photometry; brightest unsaturated pixel varies "
                               f"×{max(pk) / min(pk):.2f} between epochs (indicative only)")
        dets = "" if r["dets"] == (1, 2, 3, 4, 5, 6) else f" (D{'+D'.join(map(str, r['dets']))})"
        data = "cache" if st.get("from_cache") else f"{st['bytes'] / 1e6:.1f} MB"
        out.append(f"| {r['name']}{dets} | {st['n_found']} / {st['n_used']} | {len(m['epochs'])} | {data} | "
                   f"{r['secs']:.0f} s | {snrs} | ✅ {'; '.join(verdict)} |")
        details.append(f"### {r['name']}{dets}")
        details.append("")
        t = m["target"]
        details.append(f"Catalogue position (J2000) {t['ra']:.6f} {t['dec']:+.6f}, proper motion "
                       f"({t['pmra']:.1f}, {t['pmdec']:.1f}) mas/yr; SPHEREx-epoch centre "
                       f"{m['center']['ra']:.6f} {m['center']['dec']:+.6f}.")
        details.append("")
        details.append("| Epoch | Mean MJD | Exposures | Predicted (x, y) | Centroid (x, y) | Peak S/N | Aperture flux (mJy) |")
        details.append("|---|---|---|---|---|---|---|")
        for x in r["rows"]:
            cen = f"({x['cen'][0]:.2f}, {x['cen'][1]:.2f})" if x["cen"] else "–"
            details.append(f"| {x['epoch']} | {x['t']:.1f} | {x['n_exp']} | ({x['pred'][0]:.2f}, {x['pred'][1]:.2f}) | "
                           f"{cen} | {x['snr']:.1f} | {x['flux']:.3g} |")
        if m["warnings"]:
            details.append("")
            details.extend(f"> {w}" for w in m["warnings"])
        details.append("")
    return "\n".join(out + ["", "## Per-epoch details", ""] + details)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fov", type=float, default=4.0)
    ap.add_argument("--out", default=str(ROOT / "VALIDATION.md"))
    args = ap.parse_args()
    cache.ensure_dirs()
    results = []
    for name, dets, kind in CASES:
        print(f"-> {name} ...", flush=True)
        r = await run_case(name, dets, kind, args.fov)
        print("   ", "ok" if r["ok"] else r["error"], flush=True)
        results.append(r)
    await net.close()
    text = report(results, args.fov)
    Path(args.out).write_text(text, encoding="utf-8")
    print(text.split("## Per-epoch")[0])


if __name__ == "__main__":
    asyncio.run(main())
