"""Background job orchestration: resolve -> search -> fetch (range reads) -> stack -> cache."""
from __future__ import annotations

import asyncio
import json
import math
import statistics
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import numpy as np

from . import archive, cache, config, fitsrange, net, processing, resolver

# CPU work (WCS, numpy) runs on a tiny pool: keeps the event loop responsive and RAM flat.
_cpu = ThreadPoolExecutor(max_workers=2, thread_name_prefix="px-cpu")
RESULT_VERSION = "r4"


@dataclass(frozen=True)
class JobParams:
    query: str
    fov_arcmin: float = config.DEFAULT_FOV_ARCMIN
    lam_min: float = 0.7    # full SPHEREx range incl. outer channel edges
    lam_max: float = 5.1
    detectors: tuple[int, ...] = (1, 2, 3, 4, 5, 6)
    max_exposures: int = config.DEFAULT_MAX_EXPOSURES
    epoch_gap_days: float = config.DEFAULT_EPOCH_GAP_DAYS
    flags: bool = False

    @classmethod
    def build(cls, query: str, fov_arcmin=None, lam_min=None, lam_max=None, detectors=None,
              max_exposures=None, epoch_gap_days=None, flags=False) -> "JobParams":
        q = (query or "").strip()
        if not q or len(q) > 200:
            raise ValueError("Enter a target name or coordinates (max 200 characters).")
        fov = float(fov_arcmin if fov_arcmin is not None else config.DEFAULT_FOV_ARCMIN)
        if not math.isfinite(fov):
            raise ValueError("Invalid field of view.")
        fov = min(config.MAX_FOV_ARCMIN, max(config.MIN_FOV_ARCMIN, fov))
        lo = float(lam_min if lam_min is not None else 0.7)
        hi = float(lam_max if lam_max is not None else 5.1)
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise ValueError("Invalid wavelength range.")
        lo, hi = max(0.7, min(lo, hi)), min(5.1, max(lo, hi))
        if hi - lo < 0.005:
            raise ValueError("Wavelength range is too narrow.")
        dets = tuple(sorted({int(d) for d in (detectors or (1, 2, 3, 4, 5, 6)) if 1 <= int(d) <= 6}))
        if not dets:
            raise ValueError("Select at least one detector.")
        mx = int(max_exposures if max_exposures is not None else config.DEFAULT_MAX_EXPOSURES)
        mx = min(config.MAX_EXPOSURES, max(1, mx))
        gap = float(epoch_gap_days if epoch_gap_days is not None else config.DEFAULT_EPOCH_GAP_DAYS)
        gap = min(120.0, max(0.5, gap)) if math.isfinite(gap) else config.DEFAULT_EPOCH_GAP_DAYS
        return cls(q, round(fov, 3), round(lo, 4), round(hi, 4), dets, mx, round(gap, 3), bool(flags))

    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class JobResult:
    meta: dict
    frames: np.ndarray            # (F, n, n) float32 display orientation
    cube_path: str                # .npy (K, n, n) float32, memory-mapped on demand
    lam3: np.ndarray              # (K, 3, 3)


@dataclass
class Job:
    id: str
    params: JobParams
    status: str = "queued"
    message: str = "Waiting for a free worker"
    done: int = 0
    total: int = 0
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    error: str | None = None
    result: JobResult | None = None
    task: asyncio.Task | None = None

    def set(self, status: str, message: str = "", done: int | None = None, total: int | None = None):
        self.status, self.message, self.updated = status, message, time.time()
        if done is not None:
            self.done = done
        if total is not None:
            self.total = total

    def public(self) -> dict:
        d = {"id": self.id, "status": self.status, "message": self.message, "done": self.done,
             "total": self.total, "error": self.error, "elapsed": round(time.time() - self.created, 1)}
        if self.result is not None:
            d["meta"] = self.result.meta
        return d


class JobManager:
    def __init__(self) -> None:
        self.jobs: "OrderedDict[str, Job]" = OrderedDict()
        self.by_key: dict[str, str] = {}
        self._slots = asyncio.Semaphore(config.MAX_ACTIVE_JOBS)
        self._sia_cache: dict[str, tuple[float, list]] = {}

    # ------------------------------------------------------------------ public
    def submit(self, params: JobParams) -> Job:
        self._evict()
        k = params.key()
        jid = self.by_key.get(k)
        if jid and jid in self.jobs and self.jobs[jid].status != "error":
            job = self.jobs[jid]
            self.jobs.move_to_end(jid)
            return job
        job = Job(uuid.uuid4().hex[:12], params)
        self.jobs[job.id] = job
        self.by_key[k] = job.id
        job.task = asyncio.create_task(self._run(job))
        return job

    def get(self, jid: str) -> Job | None:
        job = self.jobs.get(jid)
        if job is not None:
            self.jobs.move_to_end(jid)
        return job

    def cancel(self, jid: str) -> bool:
        job = self.jobs.get(jid)
        if job is None:
            return False
        if job.task and not job.task.done():
            job.task.cancel()
        return True

    def _evict(self) -> None:
        now = time.time()
        for jid in list(self.jobs):
            j = self.jobs[jid]
            finished = j.status in ("done", "error", "cancelled")
            if finished and now - j.updated > config.JOB_TTL_S:
                self._drop(jid)
        while len(self.jobs) > config.MAX_JOBS_IN_MEMORY:
            victim = next((jid for jid, j in self.jobs.items()
                           if j.status in ("done", "error", "cancelled")), None)
            if victim is None:
                break
            self._drop(victim)

    def _drop(self, jid: str) -> None:
        j = self.jobs.pop(jid, None)
        if j is not None:
            k = j.params.key()
            if self.by_key.get(k) == jid:
                self.by_key.pop(k, None)

    # ------------------------------------------------------------------ pipeline
    async def _run(self, job: Job) -> None:
        try:
            async with self._slots:
                await self._pipeline(job)
        except asyncio.CancelledError:
            job.set("cancelled", "Cancelled")
            job.error = "cancelled"
        except LookupError as exc:
            job.error = str(exc)
            job.set("error", str(exc))
        except Exception as exc:  # report, never crash the server
            job.error = f"{type(exc).__name__}: {exc}"
            job.set("error", "Processing failed: " + job.error)
        finally:
            await asyncio.get_running_loop().run_in_executor(_cpu, cache.prune)

    async def _search(self, ra: float, dec: float) -> list[archive.Exposure]:
        k = f"{ra:.5f},{dec:.5f}"
        hit = self._sia_cache.get(k)
        if hit and time.time() - hit[0] < 900:
            return list(hit[1])
        exps = await archive.query_exposures(ra, dec)
        self._sia_cache[k] = (time.time(), exps)
        if len(self._sia_cache) > 64:
            oldest = min(self._sia_cache, key=lambda x: self._sia_cache[x][0])
            self._sia_cache.pop(oldest, None)
        return list(exps)

    async def _pipeline(self, job: Job) -> None:
        t0 = time.time()
        p = job.params
        acc = [0]
        net.job_bytes.set(acc)  # this task's context only
        loop = asyncio.get_running_loop()
        job.set("resolving", f"Resolving '{p.query}'")
        target = await resolver.resolve(p.query)

        # Search near the target's current-era position (matters for fast movers).
        now_mjd = 40587.0 + time.time() / 86400.0
        q_ra, q_dec = resolver.propagate(target, now_mjd)
        job.set("searching", "Searching the NASA/IPAC SPHEREx archive (IRSA)")
        found = await self._search(q_ra, q_dec)
        n_found = len(found)
        if not found:
            raise LookupError("No SPHEREx spectral images cover this position yet "
                              "(the archive grows weekly - try another target).")
        cands = [e for e in found if e.detector in p.detectors
                 and e.em_max_um >= p.lam_min and e.em_min_um <= p.lam_max]
        if not cands:
            raise LookupError("SPHEREx images exist here, but none match the selected detectors / "
                              "wavelength range.")

        t_ref = statistics.median(e.t_mjd for e in cands)
        ra_c, dec_c = resolver.propagate(target, t_ref)
        grid = processing.make_grid(ra_c, dec_c, p.fov_arcmin)
        labels = processing.group_epochs([e.t_mjd for e in cands], p.epoch_gap_days)
        lab = {id(e): l for e, l in zip(cands, labels)}
        selected = processing.select_round_robin(cands, lambda e: (lab[id(e)], e.detector), p.max_exposures)
        selected.sort(key=lambda e: e.t_mjd)

        rkey = "|".join(map(str, [RESULT_VERSION, f"{ra_c:.6f}", f"{dec_c:.6f}", p.fov_arcmin, p.lam_min,
                                  p.lam_max, p.detectors, p.max_exposures, p.epoch_gap_days, p.flags,
                                  len(selected), selected[0].key, selected[-1].key]))
        rdir = cache.result_dir(rkey)
        cached = await loop.run_in_executor(_cpu, _load_result, rdir)
        if cached is not None:
            cached.meta["stats"]["from_cache"] = True
            cached.meta["stats"]["bytes"] = acc[0]
            cached.meta["stats"]["seconds"] = round(time.time() - t0, 2)
            job.result = cached
            job.set("done", "Loaded from cache", len(selected), len(selected))
            return

        job.set("fetching", "Reading SPHEREx pixels (HTTP range requests)", 0, len(selected))
        results: list[processing.ExposureResult | None] = [None] * len(selected)
        counters = {"failed": 0, "skipped_lambda": 0, "no_overlap": 0}
        bytes_start = acc[0]
        gate = asyncio.Semaphore(config.HTTP_CONCURRENCY)

        async def one(i: int, exp: archive.Exposure):
            async with gate:
                try:
                    results[i] = await self._process_exposure(exp, grid, p, counters, loop)
                except Exception:  # one bad file must not sink the whole job
                    counters["failed"] += 1
                finally:
                    job.done += 1
                    job.updated = time.time()
                    mb = (acc[0] - bytes_start) / 1e6
                    job.message = f"Reading SPHEREx pixels: {job.done}/{job.total} exposures ({mb:.1f} MB)"

        await asyncio.gather(*(one(i, e) for i, e in enumerate(selected)))
        good = [r for r in results if r is not None]
        if not good:
            raise LookupError("Could not read any usable SPHEREx exposure for this position "
                              f"({counters['failed']} failed, {counters['no_overlap']} without overlap, "
                              f"{counters['skipped_lambda']} outside the wavelength range).")
        job.set("stacking", "Stacking bands and building epoch frames", job.done, job.total)
        meta, frames, cube, lam3 = await loop.run_in_executor(
            _cpu, _assemble, good, grid, target, t_ref, p, counters, n_found, len(selected))
        meta["warnings"].extend(_nearby_movers(target, grid, t_ref))
        meta["stats"]["bytes"] = acc[0]
        meta["stats"]["seconds"] = round(time.time() - t0, 2)
        meta["stats"]["from_cache"] = False
        cube_path = await loop.run_in_executor(_cpu, _save_result, rdir, meta, frames, cube, lam3)
        del cube
        job.result = JobResult(meta, frames, cube_path, lam3)
        job.set("done", "Done", job.total, job.total)

    async def _process_exposure(self, exp, grid, p: JobParams, counters, loop):
        last_exc: Exception | None = None
        # IRSA supports multi-range (minimal bytes); S3 mirror is the fallback.
        for url in (exp.fallback_url, exp.url):
            if not url:
                continue
            try:
                layout = await _get_layout(url, p.flags)
                plan = await loop.run_in_executor(_cpu, processing.plan_exposure, layout, grid)
                if plan is None:
                    counters["no_overlap"] += 1
                    return None
                if plan.lam_um == plan.lam_um and not (p.lam_min <= plan.lam_um <= p.lam_max):
                    counters["skipped_lambda"] += 1
                    return None
                img_task = fitsrange.read_image_block(url, layout, plan.y0, plan.y1, plan.x0, plan.x1)
                if p.flags:
                    block, flags = await asyncio.gather(
                        img_task, fitsrange.read_flag_rows(url, layout, plan.y0, plan.y1))
                    bits = fitsrange.flag_bits(layout)
                else:
                    block, flags, bits = await img_task, None, {}
                return await loop.run_in_executor(
                    _cpu, processing.finish_exposure, exp, plan, grid, block, flags, bits)
            except (net.FetchError, fitsrange.FITSLayoutError) as exc:
                last_exc = exc
                continue
        raise last_exc if last_exc else net.FetchError("no URL")


def _nearby_movers(target, grid, t_ref) -> list[str]:
    """Warn when coordinates were typed but a known fast mover sits nearby (e.g. the
    challenge's 2010-epoch WISE 0855 coordinates: the brown dwarf has since moved ~2')."""
    out = []
    if target.source != "coordinates":
        return out
    half_fov = grid.n * grid.scale / 2.0
    for t in resolver.BUILTIN_TARGETS.values():
        if not t.has_pm:
            continue
        ra, dec = resolver.propagate(t, t_ref)
        dra = (ra - grid.ra0 + 180.0) % 360.0 - 180.0
        sep = math.hypot(dra * math.cos(math.radians(grid.dec0)), dec - grid.dec0) * 3600.0
        if sep < max(900.0, 3 * half_fov):
            where = "inside" if sep < half_fov * 0.95 else "outside"
            out.append(f"{t.name} (proper motion {math.hypot(t.pmra, t.pmdec) / 1000:.1f}\"/yr) is "
                       f"{sep / 60:.1f}' from the field centre at the SPHEREx epoch ({where} this field). "
                       f"Search by its name to centre on and track it.")
    return out


_layout_mem: "OrderedDict[str, fitsrange.FileLayout]" = OrderedDict()


async def _get_layout(url: str, with_flags: bool) -> fitsrange.FileLayout:
    lay = _layout_mem.get(url)
    if lay is None:
        d = cache.layout_get(url)
        if d is not None:
            try:
                lay = fitsrange.FileLayout.from_json(d)
            except (KeyError, ValueError, TypeError):
                lay = None
    fresh = False
    if lay is None:
        lay = await fitsrange.read_layout(url, with_flags=with_flags)
        fresh = True
    if with_flags and lay.flags_header is None:
        if await fitsrange.load_flags_header(url, lay):
            fresh = True
    if fresh:
        cache.layout_put(url, lay.to_json())
    _layout_mem[url] = lay
    _layout_mem.move_to_end(url)
    while len(_layout_mem) > 400:
        _layout_mem.popitem(last=False)
    return lay


def _assemble(good, grid, target, t_ref, p: JobParams, counters, n_found, n_selected):
    good.sort(key=lambda r: r.meta["t_mjd"])
    labels = processing.group_epochs([r.meta["t_mjd"] for r in good], p.epoch_gap_days)
    frames, ep_meta, warns = processing.build_epoch_frames(good, labels)
    exp_meta = []
    for r, l in zip(good, labels):
        m = dict(r.meta)
        m["epoch"] = l
        exp_meta.append(m)
    cube = np.stack([r.image for r in good]).astype(np.float32)
    lam3 = np.stack([r.lam3 for r in good]).astype(np.float32)
    if len(ep_meta) < 2:
        warns.append("Only one epoch is available here so far; use the 'exposures' frame mode "
                     "to blink individual exposures.")
    if counters["failed"]:
        warns.append(f"{counters['failed']} exposure(s) could not be downloaded and were skipped.")
    meta = {
        "target": target.to_dict(),
        "center": {"ra": grid.ra0, "dec": grid.dec0, "t_ref_mjd": t_ref},
        "grid": grid.to_dict(),
        "params": asdict(p),
        "epochs": ep_meta,
        "exposures": exp_meta,
        "warnings": warns,
        "stats": {"n_found": n_found, "n_selected": n_selected, "n_used": len(good),
                  "n_failed": counters["failed"], "n_skipped_lambda": counters["skipped_lambda"],
                  "n_no_overlap": counters["no_overlap"]},
        "units": "MJy/sr (background subtracted)",
    }
    return meta, frames, cube, lam3


def _save_result(rdir, meta, frames, cube, lam3) -> str:
    rdir.mkdir(parents=True, exist_ok=True)
    np.save(rdir / "frames.npy", frames)
    np.save(rdir / "cube.npy", cube)
    np.save(rdir / "lam3.npy", lam3)
    with open(rdir / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return str(rdir / "cube.npy")


def _load_result(rdir) -> JobResult | None:
    try:
        with open(rdir / "meta.json", "r", encoding="utf-8") as f:
            meta = json.load(f)
        frames = np.load(rdir / "frames.npy")
        lam3 = np.load(rdir / "lam3.npy")
        if not (rdir / "cube.npy").exists():
            return None
        cache.touch(rdir)
        return JobResult(meta, frames, str(rdir / "cube.npy"), lam3)
    except (OSError, ValueError, KeyError):
        return None


def load_cube(result: JobResult):
    """Memory-mapped exposure cube: pages are loaded only when touched (low RAM)."""
    return np.load(result.cube_path, mmap_mode="r")
