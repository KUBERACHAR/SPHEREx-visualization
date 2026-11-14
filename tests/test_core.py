"""Offline unit tests (no network). Run:  python -m pytest -q   (or  python tests/test_core.py)"""
from __future__ import annotations

import asyncio
import io
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import fitsrange, net, processing, resolver  # noqa: E402


# ----------------------------------------------------------------------------- resolver
def test_parse_decimal_and_sexagesimal():
    assert resolver.parse_coordinates("133.795 -7.245") == (133.795, -7.245)
    assert resolver.parse_coordinates("269.452, +4.693") == (269.452, 4.693)
    ra, dec = resolver.parse_coordinates("08 55 10.831 -07 14 42.53")
    assert abs(ra - 133.79512917) < 1e-7 and abs(dec - (-7.24514722)) < 1e-7
    ra2, dec2 = resolver.parse_coordinates("08h55m10.831s -07d14m42.53s")
    assert abs(ra2 - ra) < 1e-9 and abs(dec2 - dec) < 1e-9
    ra3, dec3 = resolver.parse_coordinates("08:55:10.831 -07:14:42.53")
    assert abs(ra3 - ra) < 1e-9 and abs(dec3 - dec) < 1e-9
    # negative zero declination keeps its sign
    _, d0 = resolver.parse_coordinates("12 00 00 -00 30 00")
    assert abs(d0 + 0.5) < 1e-12


def test_parse_rejects_names_and_invalid():
    for s in ["M31", "R Dra", "WISE 0855", "Barnard's Star", "HD 1234", "133.0 95.0", "25 00 00 +10 00 00", ""]:
        assert resolver.parse_coordinates(s) is None, s


def test_builtin_aliases():
    assert resolver.builtin_lookup("WISE 0855").name.startswith("WISE 0855")
    assert resolver.builtin_lookup("barnard's star").name == "Barnard's Star"
    assert resolver.builtin_lookup("eps Ind B").name == "Epsilon Indi B"
    assert resolver.builtin_lookup("R Draconis").name == "R Draconis"
    assert resolver.builtin_lookup("RW Aur").name == "RW Aurigae"
    assert resolver.builtin_lookup("Vega") is None


def test_propagation_matches_astropy():
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.time import Time

    for key in ("barnard", "wise0855", "epsindib"):
        t = resolver.BUILTIN_TARGETS[key]
        mjd = 61100.0
        ra, dec = resolver.propagate(t, mjd)
        c = SkyCoord(ra=t.ra * u.deg, dec=t.dec * u.deg, pm_ra_cosdec=t.pmra * u.mas / u.yr,
                     pm_dec=t.pmdec * u.mas / u.yr, obstime=Time(t.epoch_mjd, format="mjd"))
        with np.errstate(all="ignore"):
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                c2 = c.apply_space_motion(new_obstime=Time(mjd, format="mjd"))
        sep = SkyCoord(ra * u.deg, dec * u.deg).separation(SkyCoord(c2.ra, c2.dec)).arcsec
        assert sep < 0.05, (key, sep)  # 50 mas over ~26 yr


def test_gaia_barnard_epoch_consistency():
    # Gaia DR3 position of Barnard's Star at epoch 2016.0 (from VizieR I/355/gaiadr3)
    ra, dec = resolver.propagate(resolver.BUILTIN_TARGETS["barnard"], 57388.5)
    assert abs((ra - 269.44850252544) * math.cos(math.radians(dec))) * 3600 < 0.1
    assert abs(dec - 4.73942005111) * 3600 < 0.1


def test_sesame_parser():
    xml = """<?xml version="1.0"?><Sesame><Target><Resolver name="S=Simbad (via url)">
      <oname>NAME Barnard's star</oname><jradeg>269.4520769586187</jradeg><jdedeg>4.69336496657667</jdedeg>
      <pm><v>10393</v><pmRA>-801.551</pmRA><pmDE>10362.394</pmDE></pm></Resolver></Target></Sesame>"""
    t = resolver._parse_sesame(xml)
    assert t is not None and t.source == "Sesame / Simbad"
    assert abs(t.pmdec - 10362.394) < 1e-9 and abs(t.ra - 269.4520769586187) < 1e-12


# ----------------------------------------------------------------------------- processing
def test_grid_and_bilinear():
    g = processing.make_grid(10.0, 20.0, 4.0)
    assert g.n % 2 == 1 and g.n == 39
    c = g.n // 2
    assert abs(g.ra[c, c] - 10.0) < 1e-9 and abs(g.dec[c, c] - 20.0) < 1e-9
    # north up: dec increases with FITS row; east left: ra decreases with x
    assert g.dec[c + 1, c] > g.dec[c, c] and g.ra[c, c + 1] < g.ra[c, c]
    img = np.arange(25, dtype=np.float32).reshape(5, 5)
    xs = np.array([[1.5, 2.0, 0.0]]); ys = np.array([[1.0, 2.5, 4.0]])
    out = processing.bilinear(img, xs, ys)
    assert np.allclose(out, [[6.5, 14.5, 20.0]])
    img[2, 2] = np.nan  # minor NaN neighbour is dropped and weights renormalised
    v = processing.bilinear(img, np.array([[2.75]]), np.array([[2.0]]))[0, 0]
    assert abs(v - 13.0) < 1e-5
    # dominant NaN neighbour (weight 0.75) -> no reliable value
    assert np.isnan(processing.bilinear(img, np.array([[2.25]]), np.array([[2.0]]))[0, 0])
    assert np.isnan(processing.bilinear(img, np.array([[7.0]]), np.array([[1.0]]))[0, 0])


def test_robust_stats():
    rng = np.random.default_rng(1)
    v = rng.normal(5.0, 2.0, 20000)
    v[:500] = 1e4  # bright stars / cosmic rays
    med, sig = processing.robust_stats(v)
    assert abs(med - 5.0) < 0.1 and abs(sig - 2.0) < 0.1


def test_epochs_and_selection():
    t = [60800.7, 60801.5, 60990.0, 60980.0, 61021.0, 61160.0]
    lab = processing.group_epochs(t, 40.0)
    assert lab == [0, 0, 1, 1, 1, 2]
    items = [(e, d, i) for e in range(3) for d in range(1, 7) for i in range(10)]
    sel = processing.select_round_robin(items, lambda x: (x[0], x[1]), 36)
    assert len(sel) == 36
    groups = {(x[0], x[1]) for x in sel}
    assert len(groups) == 18  # every epoch x detector represented
    order = processing._spread_order(10)
    assert sorted(order) == list(range(10)) and order[0] == 0 and order[1] == 5


def test_clipped_mean_rejects_cosmic_ray():
    rng = np.random.default_rng(2)
    cube = rng.normal(0, 1, (8, 20, 20)).astype(np.float32)
    cube[3, 10, 10] = 500.0
    mean, nused, rej = processing.clipped_weighted_mean(cube, np.ones(8, np.float32))
    assert abs(mean[10, 10]) < 2.0 and nused[10, 10] == 7 and rej > 0


def test_epoch_frames_consistent_bands():
    n = 9

    def res(det, t, val):
        img = np.full((n, n), val, np.float32)
        return processing.ExposureResult({"detector": det, "t_mjd": t, "noise": 1.0, "lam_um": 1.0 + det}, img,
                                         np.zeros((3, 3)))

    rs = [res(1, 0, 10), res(2, 0, 20), res(1, 100, 10), res(2, 100, 20), res(1, 200, 10)]
    frames, metas, warns = processing.build_epoch_frames(rs, [0, 0, 1, 1, 2])
    # epochs 0 and 1 have D1 and D2 -> equal-weight mean 15; epoch 2 only D1 -> flagged partial
    assert np.allclose(frames[0], 15) and np.allclose(frames[1], 15) and np.allclose(frames[2], 10)
    assert metas[0]["band_complete"] and not metas[2]["band_complete"]
    assert any("lack" in w for w in warns)


def test_channels_and_spectrum():
    ch = processing.channel_edges()
    assert len(ch) == 102 and ch[0][0] == 1 and ch[-1][0] == 102
    assert all(abs(ch[i][3] - ch[i + 1][2]) < 1e-12 for i in range(101) if ch[i][1] == ch[i + 1][1])
    n = 21
    cube = np.zeros((3, n, n), np.float32)
    cube[:, 10, 10] = 1.0  # a point source of 1 MJy/sr in one pixel
    metas = [{"noise": 0.01, "detector": d, "t_mjd": 0.0, "bw_um": 0.05} for d in (1, 3, 6)]
    lam3 = np.stack([np.full((3, 3), v) for v in (0.9, 2.0, 4.6)])
    sp = processing.aperture_spectrum(cube, metas, lam3, 10, 10, 6.15)
    omega = (6.15 / 206264.806) ** 2
    assert len(sp["points"]) == 3
    assert abs(sp["points"][0]["flux"] - omega * 1e9) < 1e-12
    assert [p["lam"] for p in sp["points"]] == [0.9, 2.0, 4.6]


def test_interp_lam3():
    lam3 = np.array([[1.0, 1.1, 1.2], [1.0, 1.1, 1.2], [1.0, 1.1, 1.2]])
    assert abs(processing.interp_lam3(lam3, 0, 0, 21) - 1.0) < 1e-12
    assert abs(processing.interp_lam3(lam3, 10, 5, 21) - 1.1) < 1e-12
    assert abs(processing.interp_lam3(lam3, 15, 20, 21) - 1.15) < 1e-12


# ----------------------------------------------------------------------------- FITS range reading
def _synthetic_mef() -> bytes:
    from astropy.io import fits

    rng = np.random.default_rng(3)
    img = rng.normal(1.0, 0.1, (64, 50)).astype(np.float32)
    flags = rng.integers(0, 2 ** 22, (64, 50)).astype(np.int32)
    flags[5:9] = 0
    h = fits.Header()
    h["MSKN0000"] = "TRANSIENT"
    h["MSKN0021"] = "SOURCE"
    hdul = fits.HDUList([
        fits.PrimaryHDU(header=fits.Header({"VERSION": "test"})),
        fits.ImageHDU(img, name="IMAGE"),
        fits.CompImageHDU(flags, header=h, name="FLAGS", compression_type="RICE_1", tile_shape=(1, 50)),
        fits.ImageHDU(np.zeros((64, 50), np.float32), name="VARIANCE"),
        fits.BinTableHDU.from_columns([fits.Column("X", "J", array=np.arange(3))], name="WCS-WAVE"),
    ])
    buf = io.BytesIO()
    hdul.writeto(buf)
    return buf.getvalue(), img, flags


def test_range_reader_on_synthetic_file(monkeypatch=None):
    data, img, flags = _synthetic_mef()

    async def fake_range(url, start, end):
        return data[start:end + 1]

    async def fake_suffix(url, nbytes):
        return data[-nbytes:]

    async def fake_multi(url, ranges, per_request=96):
        return [data[a:b + 1] for a, b in ranges]

    orig = (net.get_range, net.get_suffix, net.get_multirange)
    net.get_range, net.get_suffix, net.get_multirange = fake_range, fake_suffix, fake_multi
    fitsrange.net.get_range, fitsrange.net.get_suffix = fake_range, fake_suffix
    try:
        async def go():
            lay = await fitsrange.read_layout("mem://x", with_flags=True)
            assert lay.version == "test" and lay.wave_hdu is not None
            blk = await fitsrange.read_image_block("mem://x", lay, 10, 30, 3, 12)  # multirange path
            assert np.array_equal(blk, img[10:31, 3:13])
            blk2 = await fitsrange.read_image_block("mem://x", lay, 10, 30, 0, 49)  # contiguous path
            assert np.array_equal(blk2, img[10:31])
            fl = await fitsrange.read_flag_rows("mem://x", lay, 7, 40)
            assert fl is not None and np.array_equal(fl, flags[7:41])
            assert fitsrange.flag_bits(lay) == {"TRANSIENT": 0, "SOURCE": 21}
            # JSON round trip of the cached layout
            lay2 = fitsrange.FileLayout.from_json(json.loads(json.dumps(lay.to_json())))
            assert lay2.image_data_offset == lay.image_data_offset and lay2.wave_hdu == lay.wave_hdu
        asyncio.run(go())
    finally:
        net.get_range, net.get_suffix, net.get_multirange = orig


def test_multipart_parser():
    body = (b"--XYZ\r\nContent-Type: application/fits\r\nContent-Range: bytes 10-13/100\r\n\r\nabcd\r\n"
            b"--XYZ\r\nContent-Type: application/fits\r\nContent-Range: bytes 50-52/100\r\n\r\nxyz\r\n--XYZ--\r\n")
    parts = net.parse_multipart('multipart/byteranges; boundary=XYZ', body)
    assert parts == [(10, b"abcd"), (50, b"xyz")]


def test_vizier_tsv_parser_by_column_name():
    from backend import catalogs
    lines = ["#comment", "", "_r\tW2\tRA_ICRS\tDE_ICRS\tpmRA", "arcsec\tmag\tdeg\tdeg\tmas/yr",
             "------\t--\t--\t--\t--", "1.0\t13.9\t133.7788\t-7.2437\t-7905.3", "2.0\t\t133.70\t-7.20\t"]
    tsv = "\n".join(lines) + "\n"
    rows = catalogs._parse_tsv(tsv, ["RA_ICRS", "DE_ICRS", "pmRA", "W2"])
    assert rows == [{"RA_ICRS": 133.7788, "DE_ICRS": -7.2437, "pmRA": -7905.3, "W2": 13.9},
                    {"RA_ICRS": 133.70, "DE_ICRS": -7.20, "pmRA": None, "W2": None}]
    assert catalogs._parse_tsv("a\tb\n", ["RA_ICRS"]) is None


# ----------------------------------------------------------------------------- JS projection
def test_js_projection_matches_astropy_wcs():
    node = shutil.which("node")
    if not node:
        print("node not found - skipping JS projection test")
        return
    grid = {"ra0": 133.76, "dec0": -7.2422, "n": 39, "scale": 6.15}
    pts = [[0, 0], [38, 38], [5.5, 30.25], [19, 19]]
    js = f"""
      import {{ makeProjection }} from '{(ROOT / 'frontend/js/coords.js').as_uri()}';
      const p = makeProjection({json.dumps(grid)});
      const out = {json.dumps(pts)}.map(([x, y]) => {{ const s = p.toSky(x, y); const b = p.toPix(s.ra, s.dec); return [s.ra, s.dec, b.x, b.y]; }});
      console.log(JSON.stringify(out));
    """
    r = subprocess.run([node, "--input-type=module", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    w = processing.make_wcs(grid["ra0"], grid["dec0"], grid["n"], grid["scale"])
    for (x, y), (ra, dec, bx, by) in zip(pts, out):
        ra_ref, dec_ref = w.wcs_pix2world([[x, grid["n"] - 1 - y]], 0)[0]  # display -> FITS row flip
        assert abs((ra - ra_ref) * math.cos(math.radians(dec_ref))) * 3600 < 1e-3, (x, y)
        assert abs(dec - dec_ref) * 3600 < 1e-3
        assert abs(bx - x) < 1e-6 and abs(by - y) < 1e-6


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {exc!r}")
    print("all passed" if not fails else f"{fails} failed")
    sys.exit(1 if fails else 0)
