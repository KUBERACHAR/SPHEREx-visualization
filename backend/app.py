"""FastAPI application: REST API + static single-page frontend."""
from __future__ import annotations

import asyncio
import io
import math
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, cache, catalogs, config, jobs, net, processing, resolver

manager: jobs.JobManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    cache.ensure_dirs()
    await asyncio.get_running_loop().run_in_executor(None, cache.prune)
    manager = jobs.JobManager()
    yield
    await net.close()


app = FastAPI(title="Planet X - SPHEREx Explorer", version=__version__, lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=2048, compresslevel=5)


def _mgr() -> jobs.JobManager:
    assert manager is not None
    return manager


def mjd_to_iso(mjd: float) -> str:
    return (datetime(1858, 11, 17, tzinfo=timezone.utc) + timedelta(days=mjd)).strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------- models
class JobRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    fov_arcmin: float = config.DEFAULT_FOV_ARCMIN
    lam_min: float = 0.7
    lam_max: float = 5.1
    detectors: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6])
    max_exposures: int = config.DEFAULT_MAX_EXPOSURES
    epoch_gap_days: float = config.DEFAULT_EPOCH_GAP_DAYS
    flags: bool = False


# --------------------------------------------------------------------------- endpoints
@app.get("/api/health")
async def health():
    return {"ok": True, "version": __version__, "time": time.time()}


@app.get("/api/config")
async def get_config():
    return {
        "version": __version__,
        "detectors": {str(d): {"lo": lo, "hi": hi, "R": r} for d, (lo, hi, r) in config.SPHEREX_DETECTORS.items()},
        "channels": [{"ch": ch, "det": d, "lo": round(a, 4), "hi": round(b, 4)}
                     for ch, d, a, b in processing.channel_edges()],
        "limits": {"fov_min": config.MIN_FOV_ARCMIN, "fov_max": config.MAX_FOV_ARCMIN,
                   "max_exposures": config.MAX_EXPOSURES},
        "defaults": {"fov_arcmin": config.DEFAULT_FOV_ARCMIN, "max_exposures": config.DEFAULT_MAX_EXPOSURES,
                     "epoch_gap_days": config.DEFAULT_EPOCH_GAP_DAYS},
        "pixel_arcsec": config.SPHEREX_PIXEL_ARCSEC,
        "targets": [{"key": k, **t.to_dict()} for k, t in resolver.BUILTIN_TARGETS.items()],
        "catalogs": {k: v[4] for k, v in catalogs.CATALOGS.items()},
    }


@app.get("/api/resolve")
async def resolve(q: str = Query(..., min_length=1, max_length=200)):
    try:
        t = await resolver.resolve(q)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    now_mjd = 40587.0 + time.time() / 86400.0
    ra, dec = resolver.propagate(t, now_mjd)
    return {"target": t.to_dict(), "now": {"ra": ra, "dec": dec, "mjd": now_mjd}}


@app.post("/api/jobs")
async def create_job(req: JobRequest):
    try:
        params = jobs.JobParams.build(req.query, req.fov_arcmin, req.lam_min, req.lam_max, req.detectors,
                                      req.max_exposures, req.epoch_gap_days, req.flags)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc))
    job = _mgr().submit(params)
    return job.public()


def _job(jid: str) -> jobs.Job:
    job = _mgr().get(jid)
    if job is None:
        raise HTTPException(404, "Job not found or expired - please run the search again.")
    return job


def _done(jid: str) -> jobs.Job:
    job = _job(jid)
    if job.result is None:
        raise HTTPException(409, f"Job is {job.status}")
    return job


@app.get("/api/jobs/{jid}")
async def job_status(jid: str):
    return _job(jid).public()


@app.delete("/api/jobs/{jid}")
async def job_cancel(jid: str):
    return {"cancelled": _mgr().cancel(jid)}


@app.get("/api/jobs/{jid}/frames.bin")
async def job_frames(jid: str, mode: str = Query("epoch", pattern="^(epoch|exposure)$")):
    job = _done(jid)
    res = job.result
    if mode == "epoch":
        arr = res.frames
    else:
        arr = await asyncio.get_running_loop().run_in_executor(None, lambda: np.array(jobs.load_cube(res)))
    data = np.ascontiguousarray(arr, dtype="<f4").tobytes()
    return Response(data, media_type="application/octet-stream",
                    headers={"X-Frames": str(arr.shape[0]), "X-Size": str(arr.shape[-1]),
                             "Cache-Control": "private, max-age=3600"})


@app.get("/api/jobs/{jid}/spectrum")
async def job_spectrum(jid: str, x: float, y: float, frame: int = 0,
                       mode: str = Query("epoch", pattern="^(epoch|exposure)$"),
                       follow: bool = False, r: float = Query(1.6, ge=0.8, le=4.0)):
    job = _done(jid)
    res = job.result
    meta = res.meta
    n = meta["grid"]["n"]
    if not (math.isfinite(x) and math.isfinite(y)) or not (0 <= x <= n - 1 and 0 <= y <= n - 1):
        raise HTTPException(422, "pixel outside the image")
    exps = meta["exposures"]
    if mode == "epoch":
        eps = meta["epochs"]
        if not 0 <= frame < len(eps):
            raise HTTPException(422, "bad frame index")
        t_frame = eps[frame]["t_mjd"]
    else:
        if not 0 <= frame < len(exps):
            raise HTTPException(422, "bad frame index")
        t_frame = exps[frame]["t_mjd"]
    offsets = None
    tg = meta["target"]
    if follow and (tg.get("pmra") or tg.get("pmdec")):
        scale = meta["grid"]["scale"]
        offsets = []
        for m in exps:
            dt = (m["t_mjd"] - t_frame) / 365.25
            offsets.append((-(tg["pmra"] * dt / 1000.0) / scale, -(tg["pmdec"] * dt / 1000.0) / scale))

    def work():
        cube = jobs.load_cube(res)
        return processing.aperture_spectrum(cube, exps, res.lam3, x, y, meta["grid"]["scale"], r, offsets)

    spec = await asyncio.get_running_loop().run_in_executor(None, work)
    # sky position of the clicked pixel (display -> FITS grid row flip)
    w = processing.make_wcs(meta["grid"]["ra0"], meta["grid"]["dec0"], n, meta["grid"]["scale"])
    ra, dec = w.wcs_pix2world([[x, n - 1 - y]], 0)[0]
    spec.update({"x": x, "y": y, "ra": float(ra), "dec": float(dec), "followed_pm": offsets is not None})
    return spec


@app.get("/api/jobs/{jid}/fits")
async def job_fits(jid: str, frame: int = 0, mode: str = Query("epoch", pattern="^(epoch|exposure)$")):
    from astropy.io import fits

    job = _done(jid)
    res = job.result
    meta = res.meta
    if mode == "epoch":
        if not 0 <= frame < res.frames.shape[0]:
            raise HTTPException(422, "bad frame index")
        img = np.asarray(res.frames[frame])
        fm = meta["epochs"][frame]
    else:
        cube = jobs.load_cube(res)
        if not 0 <= frame < cube.shape[0]:
            raise HTTPException(422, "bad frame index")
        img = np.array(cube[frame])
        fm = meta["exposures"][frame]
    g = meta["grid"]
    hdr = processing.make_wcs(g["ra0"], g["dec0"], g["n"], g["scale"]).to_header()
    hdr["BUNIT"] = "MJy/sr"
    hdr["DATE-OBS"] = mjd_to_iso(fm["t_mjd"])
    hdr["MJD-OBS"] = fm["t_mjd"]
    hdr["ORIGIN"] = "Planet X SPHEREx Explorer"
    hdr["TELESCOP"] = "SPHEREx"
    hdr["OBJECT"] = str(meta["target"]["name"])[:60]
    hdr["COMMENT"] = "Background-subtracted, resampled (bilinear) onto a north-up TAN grid."
    data = np.flipud(img).astype(np.float32)  # back to FITS orientation (row 0 = south)
    buf = io.BytesIO()
    fits.PrimaryHDU(data, hdr).writeto(buf)
    name = f"spherex_{mode}{frame:03d}.fits"
    return Response(buf.getvalue(), media_type="application/fits",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/catalog")
async def catalog(cat: str = Query(..., pattern="^(gaia|allwise|catwise)$"),
                  ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90),
                  radius: float = Query(180.0, ge=5, le=720)):
    try:
        return await catalogs.cone(cat, ra, dec, radius)
    except net.FetchError as exc:
        return JSONResponse({"error": str(exc), "rows": []}, status_code=502)


# --------------------------------------------------------------------------- frontend
app.mount("/", StaticFiles(directory=str(config.STATIC_DIR), html=True), name="static")
