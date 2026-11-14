"""Central configuration. Every limit here exists to keep RAM, CPU and bandwidth low.

All values can be overridden with environment variables (prefix ``PX_``), e.g.
``PX_MAX_FOV_ARCMIN=10``.
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default, cast=str):
    raw = os.environ.get("PX_" + name)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "frontend"
CACHE_DIR = Path(_env("CACHE_DIR", str(ROOT / ".cache")))

HOST = _env("HOST", "127.0.0.1")
PORT = _env("PORT", 8000, int)

# --- SPHEREx archive -------------------------------------------------------
IRSA_SIA_URL = "https://irsa.ipac.caltech.edu/SIA"
SPHEREX_COLLECTIONS = ("spherex_dr1", "spherex_qr3")  # DR1 preferred (listed first)
IRSA_DATA_PREFIX = "https://irsa.ipac.caltech.edu/ibe/data/spherex/"
S3_DATA_PREFIX = "https://nasa-irsa-spherex.s3.amazonaws.com/"
SPHEREX_PIXEL_ARCSEC = 6.15          # native detector pixel scale
SPHEREX_DETECTORS = {                # detector -> (lambda_min, lambda_max) micron, R
    1: (0.75, 1.09, 39), 2: (1.10, 1.62, 41), 3: (1.63, 2.41, 41),
    4: (2.42, 3.82, 35), 5: (3.83, 4.41, 112), 6: (4.42, 5.00, 128),
}
CHANNELS_PER_DETECTOR = 17           # 6 x 17 = 102 spectral channels
# Official central wavelength (um) of the 102 SPHEREx channels, index = 17*(band-1)+(channel-1).
# Source: IRSA SPHEREx DR1 all-sky HiPS cube "properties" (data_cube_crval3_arr).
SPHEREX_CHANNEL_CENTERS_UM = (
    0.753, 0.771, 0.790, 0.809, 0.828, 0.848, 0.868, 0.889, 0.910, 0.932, 0.955, 0.978, 1.001, 1.025, 1.050, 1.075, 1.102,
    1.113, 1.139, 1.167, 1.195, 1.224, 1.254, 1.284, 1.315, 1.346, 1.378, 1.412, 1.446, 1.481, 1.517, 1.554, 1.592, 1.631,
    1.656, 1.695, 1.735, 1.775, 1.818, 1.861, 1.905, 1.949, 1.995, 2.041, 2.089, 2.139, 2.189, 2.240, 2.292, 2.345, 2.399,
    2.445, 2.506, 2.575, 2.646, 2.719, 2.794, 2.871, 2.951, 3.033, 3.118, 3.204, 3.293, 3.383, 3.476, 3.570, 3.667, 3.768,
    3.833, 3.867, 3.901, 3.935, 3.970, 4.005, 4.040, 4.076, 4.112, 4.148, 4.184, 4.220, 4.257, 4.294, 4.331, 4.368, 4.405,
    4.435, 4.467, 4.500, 4.533, 4.566, 4.600, 4.635, 4.669, 4.704, 4.739, 4.773, 4.808, 4.843, 4.879, 4.915, 4.951, 4.988,
)
assert len(SPHEREX_CHANNEL_CENTERS_UM) == 6 * CHANNELS_PER_DETECTOR

# --- Resource limits ---------------------------------------------------------
MIN_FOV_ARCMIN = 1.0
MAX_FOV_ARCMIN = _env("MAX_FOV_ARCMIN", 12.0, float)
DEFAULT_FOV_ARCMIN = 4.0
MAX_EXPOSURES = _env("MAX_EXPOSURES", 320, int)      # hard cap per job
DEFAULT_MAX_EXPOSURES = 160
HTTP_CONCURRENCY = _env("HTTP_CONCURRENCY", 16, int)  # simultaneous range requests
HTTP_TIMEOUT_S = 45.0
HTTP_RETRIES = 4
MAX_ACTIVE_JOBS = _env("MAX_ACTIVE_JOBS", 2, int)    # jobs processed at once
MAX_JOBS_IN_MEMORY = _env("MAX_JOBS_IN_MEMORY", 6, int)
JOB_TTL_S = _env("JOB_TTL_S", 3600, int)
HEADER_CACHE_MAX_MB = _env("HEADER_CACHE_MAX_MB", 200, float)
RESULT_CACHE_MAX_MB = _env("RESULT_CACHE_MAX_MB", 300, float)
DEFAULT_EPOCH_GAP_DAYS = 40.0  # SPHEREx revisits a field in clusters ~6 months apart

# FLAGS bits (by name, read from each file's MSKN keywords) that make a pixel unusable.
BAD_FLAG_NAMES = frozenset({
    "TRANSIENT", "OVERFLOW", "SUR_ERROR", "NONFUNC", "MISSING_DATA",
    "HOT", "COLD", "NONLINEAR", "PERSIST", "OUTLIER",
})
# FLAGS bits reported to the user as contamination warnings (pixels kept).
WARN_FLAG_NAMES = (
    "GHOST", "GHOST_FPA", "GHOST_EXT", "STREAK", "BLOOM",
    "SNOWBALL", "HALO", "CROSSTALK", "DICHROIC",
)

# --- External catalogue / resolver services (mirrors tried in order) ---------
SESAME_URLS = (
    "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV?",
    "https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oxp/SNV?",
    "https://vizier.idia.ac.za/viz-bin/nph-sesame/-oxp/SNV?",
)
VIZIER_HOSTS = (
    "https://vizier.cfa.harvard.edu",
    "https://vizier.idia.ac.za",
    "https://vizier.cds.unistra.fr",
)
USER_AGENT = "PlanetX-SPHEREx-Explorer/1.0 (NASA Space Apps 2026)"
