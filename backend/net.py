"""Shared async HTTP client with bounded concurrency, retries and safe range reads.

Range requests are streamed and aborted immediately if a server ignores the Range
header (HTTP 200), so a 65 MB SPHEREx file can never be downloaded by accident.
"""
from __future__ import annotations

import asyncio
import contextvars
import random
import re

import httpx

from . import config

_client: httpx.AsyncClient | None = None
_sem: asyncio.Semaphore | None = None
bytes_downloaded = 0  # process-wide counter (diagnostics)
# Per-job byte counter: a job sets a one-element list; child tasks inherit the context.
job_bytes: contextvars.ContextVar[list | None] = contextvars.ContextVar("job_bytes", default=None)


def _count(n: int) -> None:
    global bytes_downloaded
    bytes_downloaded += n
    acc = job_bytes.get()
    if acc is not None:
        acc[0] += n


class FetchError(RuntimeError):
    pass


class RangeNotSupported(FetchError):
    pass


def client() -> httpx.AsyncClient:
    global _client, _sem
    if _client is None:
        limits = httpx.Limits(max_connections=config.HTTP_CONCURRENCY + 4,
                              max_keepalive_connections=config.HTTP_CONCURRENCY + 4,
                              keepalive_expiry=30.0)
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.HTTP_TIMEOUT_S, connect=15.0),
            limits=limits, follow_redirects=True,
            headers={"User-Agent": config.USER_AGENT},
        )
        _sem = asyncio.Semaphore(config.HTTP_CONCURRENCY)
    return _client


async def close() -> None:
    global _client, _sem
    if _client is not None:
        await _client.aclose()
    _client, _sem = None, None


_RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _backoff(attempt: int) -> float:
    return min(8.0, 0.6 * 2 ** attempt) + random.random() * 0.4


async def get(url: str, *, params=None, headers=None, retries: int = config.HTTP_RETRIES,
              timeout: float | None = None) -> httpx.Response:
    """Plain GET (small documents) with exponential back-off on transient failures."""
    c = client()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with _sem:  # type: ignore[union-attr]
                r = await c.get(url, params=params, headers=headers,
                                timeout=timeout if timeout else httpx.USE_CLIENT_DEFAULT)
            _count(len(r.content))
            if r.status_code in _RETRY_STATUS:
                last = FetchError(f"HTTP {r.status_code} from {url}")
            elif r.status_code >= 400:
                raise FetchError(f"HTTP {r.status_code} from {url}")
            elif len(r.content) < 64 and b"busy" in r.content.lower():
                last = FetchError(f"server busy: {url}")  # IRSA's soft rate limit
            else:
                return r
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last = exc
        if attempt < retries:
            await asyncio.sleep(_backoff(attempt))
    raise FetchError(str(last) if last else f"failed: {url}")


async def _range_request(url: str, range_header: str, max_bytes: int,
                         retries: int = config.HTTP_RETRIES) -> tuple[str, bytes]:
    """Return (content_type, body) of a 206 response. Never reads a 200 body."""
    c = client()
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with _sem:  # type: ignore[union-attr]
                async with c.stream("GET", url, headers={"Range": range_header}) as r:
                    if r.status_code == 206:
                        chunks, n = [], 0
                        async for chunk in r.aiter_bytes():
                            n += len(chunk)
                            if n > max_bytes:
                                raise FetchError(f"range response too large from {url}")
                            chunks.append(chunk)
                        _count(n)
                        return r.headers.get("content-type", ""), b"".join(chunks)
                    if r.status_code == 200:
                        raise RangeNotSupported(f"{url} ignored the Range header")
                    if r.status_code == 416:
                        raise FetchError(f"range not satisfiable for {url}")
                    if r.status_code in _RETRY_STATUS:
                        last = FetchError(f"HTTP {r.status_code} from {url}")
                    else:
                        raise FetchError(f"HTTP {r.status_code} from {url}")
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last = exc
        if attempt < retries:
            await asyncio.sleep(_backoff(attempt))
    raise FetchError(str(last) if last else f"failed: {url}")


async def get_range(url: str, start: int, end: int) -> bytes:
    """Bytes [start, end] inclusive (may be shorter only at end of file)."""
    if end < start:
        return b""
    _, body = await _range_request(url, f"bytes={start}-{end}", end - start + 1 + 1024)
    return body[: end - start + 1]


async def get_suffix(url: str, nbytes: int) -> bytes:
    _, body = await _range_request(url, f"bytes=-{nbytes}", nbytes + 1024)
    return body


_CR = re.compile(rb"content-range:\s*bytes\s+(\d+)-(\d+)/", re.I)


def parse_multipart(content_type: str, body: bytes) -> list[tuple[int, bytes]]:
    """Parse a multipart/byteranges body into [(start_offset, data), ...]."""
    m = re.search(r'boundary="?([^";]+)"?', content_type)
    if not m:
        raise FetchError("multipart response without boundary")
    delim = b"--" + m.group(1).encode()
    parts: list[tuple[int, bytes]] = []
    pos = body.find(delim)
    while pos != -1:
        pos += len(delim)
        if body[pos:pos + 2] == b"--":
            break
        hdr_end = body.find(b"\r\n\r\n", pos)
        if hdr_end == -1:
            break
        cr = _CR.search(body[pos:hdr_end])
        if not cr:
            raise FetchError("multipart part without Content-Range")
        a, b = int(cr.group(1)), int(cr.group(2))
        data_start = hdr_end + 4
        data = body[data_start:data_start + (b - a + 1)]
        if len(data) != b - a + 1:
            raise FetchError("truncated multipart part")
        parts.append((a, data))
        pos = body.find(delim, data_start + len(data))
    return parts


async def get_multirange(url: str, ranges: list[tuple[int, int]], per_request: int = 96) -> list[bytes]:
    """Fetch many small disjoint byte ranges using multipart/byteranges requests.

    Raises RangeNotSupported if the server does not support multi-range (e.g. S3).
    """
    out: list[bytes] = []
    for i in range(0, len(ranges), per_request):
        batch = ranges[i:i + per_request]
        hdr = "bytes=" + ",".join(f"{a}-{b}" for a, b in batch)
        total = sum(b - a + 1 for a, b in batch)
        ctype, body = await _range_request(url, hdr, total + 300 * len(batch) + 4096)
        if len(batch) == 1 or not ctype.lower().startswith("multipart/byteranges"):
            if len(batch) == 1 and len(body) >= total:
                out.append(body[:total])
                continue
            raise RangeNotSupported(f"{url} returned a single part for a multi-range request")
        parts = parse_multipart(ctype, body)
        for a, b in batch:
            for ps, pdata in parts:
                if ps <= a and a - ps + (b - a + 1) <= len(pdata):
                    out.append(pdata[a - ps:a - ps + (b - a + 1)])
                    break
            else:
                raise FetchError("multipart response missing a requested range")
    return out
