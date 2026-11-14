"""Module A - coordinate parsing, target-name resolution and proper-motion propagation.

* Accepts decimal degrees ("133.795 -7.245"), sexagesimal ("08 55 10.83 -07 14 42.5",
  "08h55m10.83s -07d14m42.5s", "08:55:10.83, -07:14:42.5") or object names.
* Names are resolved with CDS Sesame (SIMBAD/NED/VizieR) through several mirrors;
  the five mandatory challenge targets are also built in so they work offline.
* SIMBAD positions are ICRS at epoch J2000. High proper-motion objects move a lot
  by the SPHEREx era (Barnard's Star: ~4.5', WISE 0855: ~3.5' since 2000), so the
  position is propagated to the requested epoch.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from urllib.parse import quote

from . import config, net

J2000_MJD = 51544.5
DAYS_PER_YEAR = 365.25


@dataclass
class Target:
    name: str
    ra: float                  # ICRS deg at epoch `epoch_mjd`
    dec: float
    pmra: float = 0.0          # mas/yr, includes cos(dec)
    pmdec: float = 0.0         # mas/yr
    epoch_mjd: float = J2000_MJD
    source: str = "input"
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def has_pm(self) -> bool:
        return abs(self.pmra) > 0 or abs(self.pmdec) > 0


# Values from SIMBAD via Sesame (ICRS, epoch J2000), verified 2026-11.
BUILTIN_TARGETS: dict[str, Target] = {
    "barnard": Target("Barnard's Star", 269.4520769586187, 4.69336496657667, -801.551, 10362.394,
                      source="builtin", note="High proper-motion red dwarf (10.4\"/yr)."),
    "epsindib": Target("Epsilon Indi B", 331.04414658852124, -56.78284419511611, 3981.977, -2466.832,
                       source="builtin", note="Binary brown dwarf (Ba+Bb), 4.7\"/yr."),
    "wise0855": Target("WISE 0855-0714", 133.81890825, -7.247048, -8123.7, 673.2,
                       source="builtin", note="Coldest known brown dwarf, 8.1\"/yr. "
                       "The challenge coordinates (08h55m10.83s -07d14m42.5s) are its ~2010 position."),
    "rwaur": Target("RW Aurigae", 76.95652603458, 30.40143817389, 7.23, -20.33,
                    source="builtin", note="Young variable binary star (T Tauri type)."),
    "rdra": Target("R Draconis", 248.16761083295, 66.75497941722, -20.643, 6.149,
                   source="builtin", note="Mira-type pulsating variable star."),
}
_ALIASES = {
    "barnard": "barnard", "barnardsstar": "barnard", "barnardstar": "barnard", "gj699": "barnard",
    "v2500oph": "barnard",
    "epsilonindib": "epsindib", "epsindib": "epsindib", "epsilonindiba": "epsindib",
    "epsindiba": "epsindib", "epsilonindibb": "epsindib", "epsindibab": "epsindib",
    "epsindb": "epsindib", "epsindba": "epsindib", "epsindbb": "epsindib", "epsindbab": "epsindib",
    "epsilonindb": "epsindib",
    "wise0855": "wise0855", "wise08550714": "wise0855", "wisej0855": "wise0855",
    "wisej08550714": "wise0855", "wisej085510830714425": "wise0855", "wise085510": "wise0855",
    "rwaurigae": "rwaur", "rwaur": "rwaur", "rwaurigaesystem": "rwaur",
    "rdraconis": "rdra", "rdra": "rdra",
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("ε", "eps"))


def builtin_lookup(name: str) -> Target | None:
    key = _ALIASES.get(_norm(name))
    if key is None:
        return None
    t = BUILTIN_TARGETS[key]
    return Target(**asdict(t))


# --------------------------------------------------------------------------- parsing
_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)"


def _sexa_to_deg(parts: list[str], sign_str: str) -> float:
    vals = [float(p) for p in parts]
    while len(vals) < 3:
        vals.append(0.0)
    d, m, s = abs(vals[0]), vals[1], vals[2]
    if not (0 <= m < 60 and 0 <= s < 60):
        raise ValueError("minutes/seconds out of range")
    v = d + m / 60.0 + s / 3600.0
    return -v if sign_str.strip().startswith("-") else v


def parse_coordinates(text: str) -> tuple[float, float] | None:
    """Parse an RA/Dec string. Returns (ra_deg, dec_deg) or None if it is not coordinates."""
    s = text.strip()
    if not s:
        return None
    s_clean = s.replace(",", " ").replace("°", "d").replace("′", "m").replace("″", "s")
    s_clean = s_clean.replace("'", "m").replace('"', "s")
    # Pure decimal degrees: "ra dec"
    m = re.fullmatch(rf"\s*({_NUM})\s*(?:d|deg)?\s+({_NUM})\s*(?:d|deg)?\s*", s_clean, re.I)
    if m:
        ra, dec = float(m.group(1)), float(m.group(2))
        return _validate(ra, dec)
    # Sexagesimal: split into RA tokens and Dec tokens. Dec must start with a sign or
    # the token list must be exactly 6 numbers.
    tokens = re.findall(rf"[+-]?\d+(?:\.\d*)?|[+-]?\.\d+", re.sub(r"[hHdDmMsS:]", " ", s_clean))
    if not tokens or re.search(r"[a-ce-gi-ln-rt-zA-CE-GI-LN-RT-Z]", re.sub(r"deg", "", s_clean, flags=re.I)):
        return None
    signed = [i for i, t in enumerate(tokens) if t.startswith(("+", "-")) and i > 0]
    if signed:
        k = signed[0]
    elif len(tokens) == 6:
        k = 3
    elif len(tokens) == 4:
        k = 2
    else:
        return None
    ra_parts, dec_parts = tokens[:k], tokens[k:]
    if not (1 <= len(ra_parts) <= 3 and 1 <= len(dec_parts) <= 3):
        return None
    try:
        ra_h = _sexa_to_deg(ra_parts, "+")
        dec = _sexa_to_deg(dec_parts, dec_parts[0])
    except ValueError:
        return None
    if ra_h >= 24:
        return None
    return _validate(ra_h * 15.0, dec)


def _validate(ra: float, dec: float) -> tuple[float, float] | None:
    if not (math.isfinite(ra) and math.isfinite(dec)):
        return None
    if not (-90.0 <= dec <= 90.0) or not (0.0 <= ra <= 360.0):
        return None
    return ra % 360.0, dec


# --------------------------------------------------------------------------- PM
def propagate(t: Target, mjd: float) -> tuple[float, float]:
    """Propagate position for constant space motion on the sphere (no radial velocity).

    Exact rotation along the great circle defined by the proper-motion vector,
    which stays accurate near the poles and over decades.
    """
    if not t.has_pm:
        return t.ra, t.dec
    dt_yr = (mjd - t.epoch_mjd) / DAYS_PER_YEAR
    ra, dec = math.radians(t.ra), math.radians(t.dec)
    mas = math.pi / (180.0 * 3600.0 * 1000.0)
    pmra, pmdec = t.pmra * mas, t.pmdec * mas  # rad/yr
    # Unit position vector and local east/north unit vectors.
    p = (math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec))
    e = (-math.sin(ra), math.cos(ra), 0.0)
    n = (-math.sin(dec) * math.cos(ra), -math.sin(dec) * math.sin(ra), math.cos(dec))
    mu = math.hypot(pmra, pmdec)
    ang = mu * dt_yr
    ux = [(pmra * e[i] + pmdec * n[i]) / mu for i in range(3)]
    v = [p[i] * math.cos(ang) + ux[i] * math.sin(ang) for i in range(3)]
    ra2 = math.degrees(math.atan2(v[1], v[0])) % 360.0
    dec2 = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
    return ra2, dec2


# --------------------------------------------------------------------------- Sesame
def _parse_sesame(xml_text: str) -> Target | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for res in root.iter("Resolver"):
        ra_el, de_el = res.find("jradeg"), res.find("jdedeg")
        if ra_el is None or de_el is None:
            continue
        try:
            ra, dec = float(ra_el.text), float(de_el.text)
        except (TypeError, ValueError):
            continue
        pmra = pmdec = 0.0
        pm = res.find("pm")
        if pm is not None:
            try:
                pmra = float(pm.findtext("pmRA") or 0.0)
                pmdec = float(pm.findtext("pmDE") or 0.0)
            except ValueError:
                pmra = pmdec = 0.0
        oname = (res.findtext("oname") or "").strip()
        rname = res.get("name", "")  # e.g. "S=Simbad, IDIA (via url)"
        service = re.sub(r"\s*\(.*?\)", "", rname.split("=", 1)[-1]).split(",")[0].strip() or "CDS"
        return Target(oname or "", ra, dec, pmra, pmdec, J2000_MJD, f"Sesame / {service}")
    return None


async def resolve_name(name: str) -> Target:
    clean = name.strip()
    builtin = builtin_lookup(clean)
    if builtin is not None:
        return builtin
    last_err = "not found"
    for base in config.SESAME_URLS:
        try:
            r = await net.get(base + quote(clean), retries=1, timeout=12.0)
        except net.FetchError as exc:
            last_err = str(exc)
            continue
        t = _parse_sesame(r.text)
        if t is not None:
            t.name = t.name or clean
            return t
        if "Nothing found" in r.text:
            last_err = "not found"
            break  # authoritative negative answer
    raise LookupError(f"Could not resolve '{clean}' ({last_err}). Try coordinates, "
                      "e.g. '133.795 -7.245' or '08 55 10.8 -07 14 42'.")


async def resolve(text: str) -> Target:
    coords = parse_coordinates(text)
    if coords is not None:
        return Target(f"{coords[0]:.5f} {coords[1]:+.5f}", coords[0], coords[1], source="coordinates")
    return await resolve_name(text)
