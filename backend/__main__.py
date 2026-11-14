"""Run with:  python -m backend  [--host 127.0.0.1] [--port 8000] [--no-browser]"""
from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn

from . import config


def main() -> None:
    ap = argparse.ArgumentParser(description="Planet X - SPHEREx Explorer")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    args = ap.parse_args()
    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}/"
    print(f"\n  Planet X - SPHEREx Explorer  ->  {url}\n  (Ctrl+C to stop)\n")
    if not args.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    # One worker, no reload, no access log: smallest RAM/CPU footprint.
    uvicorn.run("backend.app:app", host=args.host, port=args.port, workers=1,
                log_level="warning", access_log=False, loop="asyncio", http="h11")


if __name__ == "__main__":
    main()
