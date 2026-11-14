"""Module B (part 1) - find SPHEREx spectral images covering a sky position (IRSA SIA v2)."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from . import config, net


@dataclass
class Exposure:
    obs_id: str
    detector: int
    t_mjd: float           # exposure start (MJD)
    t_end: float
    em_min_um: float
    em_max_um: float
    url: str               # S3 URL (range readable)
    fallback_url: str      # IRSA URL (also range readable)
    collection: str

    @property
    def key(self) -> str:
        return f"{self.obs_id}_D{self.detector}"

    def to_dict(self) -> dict:
        return {"obs_id": self.obs_id, "detector": self.detector, "t_mjd": self.t_mjd,
                "em_min_um": self.em_min_um, "em_max_um": self.em_max_um, "collection": self.collection}


def _votable_rows(xml_bytes: bytes) -> list[dict]:
    """Minimal TABLEDATA VOTable reader (avoids importing astropy.table: lower RAM)."""
    root = ET.fromstring(xml_bytes)
    ns = ""
    m = re.match(r"\{.*\}", root.tag)
    if m:
        ns = m.group(0)
    rows: list[dict] = []
    for table in root.iter(f"{ns}TABLE"):
        names = [f.get("name") for f in table.findall(f"{ns}FIELD")]
        data = table.find(f"{ns}DATA")
        if data is None:
            continue
        td = data.find(f"{ns}TABLEDATA")
        if td is None:
            if data.find(f"{ns}BINARY") is not None or data.find(f"{ns}BINARY2") is not None:
                return _votable_rows_astropy(xml_bytes)
            continue
        for tr in td.findall(f"{ns}TR"):
            vals = [(c.text or "") for c in tr.findall(f"{ns}TD")]
            rows.append(dict(zip(names, vals)))
    for info in root.iter(f"{ns}INFO"):
        if info.get("name") == "QUERY_STATUS" and info.get("value") == "ERROR":
            raise RuntimeError(f"SIA error: {(info.text or '').strip()}")
    return rows


def _votable_rows_astropy(xml_bytes: bytes) -> list[dict]:  # pragma: no cover - fallback only
    import io
    from astropy.io.votable import parse_single_table
    import numpy as np
    t = parse_single_table(io.BytesIO(xml_bytes)).to_table(use_names_over_ids=True)
    out = []
    for i in range(len(t)):
        row = {}
        for c in t.colnames:
            v = t[c][i]
            row[c] = "" if np.ma.is_masked(v) else (v.decode() if isinstance(v, bytes) else str(v))
        out.append(row)
    return out


def _s3_url(row: dict) -> str | None:
    ca = row.get("cloud_access") or ""
    if ca.strip().startswith("{"):
        try:
            aws = json.loads(ca).get("aws", {})
            if aws.get("bucket_name") and aws.get("key"):
                return f"https://{aws['bucket_name']}.s3.amazonaws.com/{aws['key']}"
        except (ValueError, AttributeError):
            pass
    url = row.get("access_url", "")
    if url.startswith(config.IRSA_DATA_PREFIX):
        return config.S3_DATA_PREFIX + url[len(config.IRSA_DATA_PREFIX):]
    return None


async def query_exposures(ra: float, dec: float, radius_deg: float = 0.0005) -> list[Exposure]:
    params = [("POS", f"CIRCLE {ra:.7f} {dec:.7f} {radius_deg:.6f}"), ("RESPONSEFORMAT", "VOTable"),
              ("MAXREC", "5000")]
    params += [("COLLECTION", c) for c in config.SPHEREX_COLLECTIONS]
    r = await net.get(config.IRSA_SIA_URL, params=params, timeout=120.0, retries=3)
    rows = _votable_rows(r.content)
    best: dict[str, tuple[int, Exposure]] = {}
    for row in rows:
        if row.get("dataproduct_subtype", "science") not in ("", "science"):
            continue
        bp = row.get("energy_bandpassname", "")
        m = re.search(r"D(\d)", bp)
        if not m:
            continue
        try:
            exp = Exposure(
                obs_id=row["obs_id"], detector=int(m.group(1)),
                t_mjd=float(row["t_min"]), t_end=float(row.get("t_max") or row["t_min"]),
                em_min_um=float(row["em_min"]) * 1e6, em_max_um=float(row["em_max"]) * 1e6,
                url="", fallback_url=row["access_url"], collection=row.get("obs_collection", ""),
            )
        except (KeyError, ValueError):
            continue
        exp.url = _s3_url(row) or exp.fallback_url
        rank = config.SPHEREX_COLLECTIONS.index(exp.collection) if exp.collection in config.SPHEREX_COLLECTIONS else 99
        prev = best.get(exp.key)
        if prev is None or rank < prev[0]:
            best[exp.key] = (rank, exp)
    out = [e for _, e in best.values()]
    out.sort(key=lambda e: (e.t_mjd, e.detector))
    return out
