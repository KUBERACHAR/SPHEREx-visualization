"""Partial (HTTP Range) reading of SPHEREx Level-2 multi-extension FITS files.

A SPHEREx spectral image file is ~65 MB:

    0 PRIMARY   (header only)
    1 IMAGE     2040x2040 float32, uncompressed        <- read only the needed rows
    2 FLAGS     RICE_1 tile-compressed, 1 row per tile <- read only the needed tiles
    3 VARIANCE  4 ZODI  5 EPSF (~4 MB)                 <- never downloaded
    6 WCS-WAVE  spectral WCS lookup table              <- read from the file tail

The official IRSA cutout service returns every extension (including the ~4 MB
EPSF table) for each cutout, so for a small sky patch this module transfers
roughly 5-20x fewer bytes and works against the public AWS S3 mirror.
"""
from __future__ import annotations

import io
import struct
import warnings
from dataclasses import dataclass, field

import numpy as np
from astropy.io import fits

from . import net

BLOCK = 2880
_PRIMARY_STUB = fits.PrimaryHDU().header.tostring().encode("ascii")
assert len(_PRIMARY_STUB) == BLOCK


class FITSLayoutError(RuntimeError):
    pass


def _find_end(buf: bytes) -> int | None:
    """Return byte length of the header (multiple of 2880) if END card present."""
    nblocks = len(buf) // BLOCK
    for b in range(nblocks):
        blk = buf[b * BLOCK:(b + 1) * BLOCK]
        for c in range(36):
            card = blk[c * 80:(c + 1) * 80]
            if card[:8] == b"END     ":
                return (b + 1) * BLOCK
    return None


def parse_header(buf: bytes) -> tuple[fits.Header, int]:
    n = _find_end(buf)
    if n is None:
        raise FITSLayoutError("END card not found")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        hdr = fits.Header.fromstring(buf[:n].decode("ascii", errors="replace"))
    return hdr, n


def data_nbytes(hdr: fits.Header) -> int:
    """Size of the data unit incl. heap, padded to a 2880 multiple (FITS standard 4.4.1)."""
    naxis = int(hdr.get("NAXIS", 0))
    if naxis == 0:
        return 0
    n = 1
    for i in range(1, naxis + 1):
        n *= int(hdr[f"NAXIS{i}"])
    nbytes = abs(int(hdr["BITPIX"])) // 8 * int(hdr.get("GCOUNT", 1)) * (int(hdr.get("PCOUNT", 0)) + n)
    return (nbytes + BLOCK - 1) // BLOCK * BLOCK


async def read_header_at(url: str, offset: int, guess_blocks: int = 12) -> tuple[fits.Header, int, bytes]:
    buf = b""
    want = guess_blocks
    while True:
        chunk = await net.get_range(url, offset + len(buf), offset + want * BLOCK - 1)
        if not chunk:
            raise FITSLayoutError("unexpected end of file while reading header")
        buf += chunk
        n = _find_end(buf)
        if n is not None:
            hdr, n = parse_header(buf)
            return hdr, n, buf[:n]
        if want > 400:
            raise FITSLayoutError("header too long")
        want *= 2


@dataclass
class FileLayout:
    """Everything needed to locate pixels inside one remote SPHEREx file (cacheable)."""
    image_header: str
    image_data_offset: int
    flags_header: str | None
    flags_data_offset: int | None
    wave_hdu: bytes | None          # raw bytes of the WCS-WAVE HDU (header + data)
    version: str = ""

    def to_json(self) -> dict:
        import base64
        return {
            "image_header": self.image_header, "image_data_offset": self.image_data_offset,
            "flags_header": self.flags_header, "flags_data_offset": self.flags_data_offset,
            "wave_hdu": base64.b64encode(self.wave_hdu).decode() if self.wave_hdu else None,
            "version": self.version,
        }

    @classmethod
    def from_json(cls, d: dict) -> "FileLayout":
        import base64
        return cls(d["image_header"], int(d["image_data_offset"]), d.get("flags_header"),
                   d.get("flags_data_offset"),
                   base64.b64decode(d["wave_hdu"]) if d.get("wave_hdu") else None,
                   d.get("version", ""))

    # Parsed views (not serialized) -------------------------------------------------
    _hdr_cache: dict = field(default_factory=dict, repr=False, compare=False)

    def image_hdr(self) -> fits.Header:
        if "img" not in self._hdr_cache:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._hdr_cache["img"] = fits.Header.fromstring(self.image_header)
        return self._hdr_cache["img"]

    def flags_hdr(self) -> fits.Header | None:
        if self.flags_header is None:
            return None
        if "flg" not in self._hdr_cache:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self._hdr_cache["flg"] = fits.Header.fromstring(self.flags_header)
        return self._hdr_cache["flg"]

    def wave_hdulist(self) -> fits.HDUList | None:
        if not self.wave_hdu:
            return None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return fits.open(io.BytesIO(_PRIMARY_STUB + self.wave_hdu), memmap=False, lazy_load_hdus=False)


async def _read_wave_hdu(url: str) -> bytes | None:
    """WCS-WAVE is the last HDU and is tiny: scan the file tail for its header block."""
    for nblocks in (3, 12):  # normally header (1 block) + data (1 block)
        tail = await net.get_suffix(url, nblocks * BLOCK)
        if len(tail) % BLOCK:
            tail = tail[len(tail) % BLOCK:]  # keep alignment relative to EOF
        nb = len(tail) // BLOCK
        for b in range(nb - 1, -1, -1):
            blk = tail[b * BLOCK:(b + 1) * BLOCK]
            if blk.startswith(b"XTENSION=") and b"WCS-WAVE" in blk:
                return tail[b * BLOCK:]
    return None


async def read_layout(url: str, with_flags: bool = False) -> FileLayout:
    import asyncio

    wave_task = asyncio.create_task(_read_wave_hdu(url))
    try:
        # HDU0 and HDU1 (IMAGE) headers normally fit in the first 12 blocks: one request.
        buf = await net.get_range(url, 0, 12 * BLOCK - 1)
        h0, n0 = parse_header(buf)
        if int(h0.get("NAXIS", 0)) != 0:
            raise FITSLayoutError("unexpected primary data")
        if _find_end(buf[n0:]) is not None:
            h1, n1 = parse_header(buf[n0:])
            raw1 = buf[n0:n0 + n1]
        else:
            h1, n1, raw1 = await read_header_at(url, n0, guess_blocks=16)
        if str(h1.get("EXTNAME", "")).strip() != "IMAGE":
            raise FITSLayoutError(f"HDU1 is {h1.get('EXTNAME')!r}, expected IMAGE")
        img_off = n0 + n1
        try:
            wave = await wave_task
        except (FITSLayoutError, net.FetchError):
            wave = None
    except BaseException:
        wave_task.cancel()
        raise
    layout = FileLayout(raw1.decode("ascii", errors="replace"), img_off, None, None, wave,
                        str(h0.get("VERSION", "")))
    if with_flags:
        await load_flags_header(url, layout)
    return layout


async def load_flags_header(url: str, layout: FileLayout) -> bool:
    """Locate the FLAGS HDU (directly after the IMAGE data). Returns True on success."""
    if layout.flags_header is not None:
        return True
    off = layout.image_data_offset + data_nbytes(layout.image_hdr())
    try:
        h2, n2, raw2 = await read_header_at(url, off, guess_blocks=6)
    except (FITSLayoutError, net.FetchError):
        return False
    if str(h2.get("EXTNAME", "")).strip() != "FLAGS":
        return False
    layout.flags_header = raw2.decode("ascii", errors="replace")
    layout.flags_data_offset = off + n2
    layout._hdr_cache.pop("flg", None)
    return True


async def read_image_block(url: str, layout: FileLayout, y0: int, y1: int, x0: int, x1: int,
                           multirange: bool = True) -> np.ndarray:
    """IMAGE pixels [y0..y1, x0..x1] (inclusive, 0-based, clipped to the array) as float32.

    With ``multirange`` one multipart/byteranges request fetches only the needed
    columns of every row (~50x less data than whole rows). Servers without
    multi-range support (S3) transparently fall back to one contiguous row span.
    """
    h = layout.image_hdr()
    nx, ny = int(h["NAXIS1"]), int(h["NAXIS2"])
    bitpix = int(h["BITPIX"])
    y0, y1 = max(0, y0), min(ny - 1, y1)
    x0, x1 = max(0, x0), min(nx - 1, x1)
    if y1 < y0 or x1 < x0:
        return np.empty((0, 0), np.float32)
    bpp = abs(bitpix) // 8
    dtype = {-32: ">f4", -64: ">f8", 16: ">i2", 32: ">i4"}.get(bitpix)
    if dtype is None:
        raise FITSLayoutError(f"unsupported BITPIX {bitpix}")
    nrows, ncols = y1 - y0 + 1, x1 - x0 + 1
    base = layout.image_data_offset
    arr = None
    if multirange and nrows > 1 and ncols < nx // 3:
        ranges = [(base + (y * nx + x0) * bpp, base + (y * nx + x1) * bpp + bpp - 1)
                  for y in range(y0, y1 + 1)]
        try:
            parts = await net.get_multirange(url, ranges)
            arr = np.frombuffer(b"".join(parts), dtype=dtype).reshape(nrows, ncols)
        except net.RangeNotSupported:
            arr = None
    if arr is None:
        start = base + y0 * nx * bpp
        raw = await net.get_range(url, start, start + nrows * nx * bpp - 1)
        if len(raw) != nrows * nx * bpp:
            raise FITSLayoutError("short read of image rows")
        arr = np.frombuffer(raw, dtype=dtype).reshape(nrows, nx)[:, x0:x1 + 1]
    arr = arr.astype(np.float32)
    bscale, bzero = float(h.get("BSCALE", 1.0)), float(h.get("BZERO", 0.0))
    if bscale != 1.0 or bzero != 0.0:
        arr = arr * np.float32(bscale) + np.float32(bzero)
    return arr


def flag_bits(layout: FileLayout) -> dict[str, int]:
    """Map flag name -> bit number from the MSKNnnnn keywords of the FLAGS header."""
    h = layout.flags_hdr()
    out: dict[str, int] = {}
    if h is None:
        return out
    for key in h:
        if key.startswith("MSKN") and key[4:].isdigit():
            out[str(h[key]).strip().upper()] = int(key[4:])
    return out


async def read_flag_rows(url: str, layout: FileLayout, y0: int, y1: int) -> np.ndarray | None:
    """Decode rows y0..y1 of the RICE tile-compressed FLAGS extension.

    Only the tiles covering the requested rows are downloaded. A minimal valid
    compressed-image FITS file is rebuilt in memory and decoded with astropy's
    public API, so no private decompression code is used. Returns ``None`` if
    the layout is not row-tiled (caller then simply skips flag masking).
    """
    h = layout.flags_hdr()
    if h is None or layout.flags_data_offset is None or not h.get("ZIMAGE", False):
        return None
    try:
        znx, zny = int(h["ZNAXIS1"]), int(h["ZNAXIS2"])
        tile_x, tile_y = int(h.get("ZTILE1", znx)), int(h.get("ZTILE2", 1))
        if tile_x != znx or int(h["TFIELDS"]) != 1:
            return None
        tform = str(h["TFORM1"]).strip().upper()
        if "Q" in tform:
            dsize, dfmt = 16, ">qq"
        elif "P" in tform:
            dsize, dfmt = 8, ">ii"
        else:
            return None
        rowlen, nrows_tab = int(h["NAXIS1"]), int(h["NAXIS2"])
        if rowlen != dsize:
            return None
        y0, y1 = max(0, y0), min(zny - 1, y1)
        t0, t1 = y0 // tile_y, y1 // tile_y
        t1 = min(t1, nrows_tab - 1)
        ntiles = t1 - t0 + 1
        tab_off = layout.flags_data_offset
        desc_raw = await net.get_range(url, tab_off + t0 * dsize, tab_off + (t1 + 1) * dsize - 1)
        if len(desc_raw) != ntiles * dsize:
            return None
        descs = [struct.unpack(dfmt, desc_raw[i * dsize:(i + 1) * dsize]) for i in range(ntiles)]
        theap = int(h.get("THEAP", rowlen * nrows_tab))
        lo = min(off for n, off in descs)
        hi = max(off + n for n, off in descs)  # element size of 'B' arrays is 1 byte
        if "PB" not in tform and "QB" not in tform:
            return None
        if hi - lo > 8_000_000:
            return None
        heap = await net.get_range(url, tab_off + theap + lo, tab_off + theap + hi - 1) if hi > lo else b""
        if len(heap) != hi - lo:
            return None
        # Rebuild a compact compressed HDU containing only these tiles.
        nh = h.copy()
        rows_out = min(ntiles * tile_y, zny - t0 * tile_y)
        nh["NAXIS2"] = ntiles
        nh["ZNAXIS2"] = rows_out
        nh["PCOUNT"] = len(heap)
        nh["THEAP"] = ntiles * dsize
        for k in ("CHECKSUM", "DATASUM", "ZDATASUM"):
            nh.remove(k, ignore_missing=True)
        table = b"".join(struct.pack(dfmt, n, off - lo) for n, off in descs)
        body = table + heap
        body += b"\0" * ((BLOCK - len(body) % BLOCK) % BLOCK)
        hdr_bytes = nh.tostring().encode("ascii")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with fits.open(io.BytesIO(_PRIMARY_STUB + hdr_bytes + body), memmap=False) as hl:
                data = np.asarray(hl[1].data)
        start_row = t0 * tile_y
        sub = data[y0 - start_row:y1 - start_row + 1]
        return sub.astype(np.int32, copy=False)
    except (KeyError, ValueError, OSError, struct.error, IndexError, TypeError):
        return None
