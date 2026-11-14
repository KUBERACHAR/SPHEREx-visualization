"""Small size-bounded disk caches (file headers and finished results)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from . import config


def _h(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


LAYOUT_DIR = config.CACHE_DIR / "layouts"
RESULT_DIR = config.CACHE_DIR / "results"


def ensure_dirs() -> None:
    LAYOUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)


def layout_get(url_key: str) -> dict | None:
    p = LAYOUT_DIR / _h(url_key)[:2] / (_h(url_key) + ".json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def layout_put(url_key: str, data: dict) -> None:
    d = LAYOUT_DIR / _h(url_key)[:2]
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / (_h(url_key) + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, d / (_h(url_key) + ".json"))
    except OSError:
        pass


def result_dir(job_key: str) -> Path:
    return RESULT_DIR / _h(job_key)


def _dir_size(p: Path) -> int:
    total = 0
    for root, _, files in os.walk(p):
        for fn in files:
            try:
                total += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return total


def prune() -> None:
    """Delete least-recently-used entries until each cache is under its size limit."""
    # Results: one directory per job.
    try:
        entries = [(p.stat().st_mtime, p) for p in RESULT_DIR.iterdir() if p.is_dir()]
        sizes = {p: _dir_size(p) for _, p in entries}
        total = sum(sizes.values())
        limit = config.RESULT_CACHE_MAX_MB * 1e6
        for _, p in sorted(entries):
            if total <= limit:
                break
            total -= sizes[p]
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass
    # Layouts: many small files.
    try:
        files = []
        for root, _, fns in os.walk(LAYOUT_DIR):
            for fn in fns:
                fp = os.path.join(root, fn)
                try:
                    st = os.stat(fp)
                    files.append((st.st_mtime, st.st_size, fp))
                except OSError:
                    pass
        total = sum(s for _, s, _ in files)
        limit = config.HEADER_CACHE_MAX_MB * 1e6
        for _, s, fp in sorted(files):
            if total <= limit:
                break
            try:
                os.remove(fp)
                total -= s
            except OSError:
                pass
    except OSError:
        pass


def touch(p: Path) -> None:
    try:
        now = time.time()
        os.utime(p, (now, now))
    except OSError:
        pass
