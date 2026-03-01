#!/usr/bin/env python3
"""cplcache — fetch a pre-rendered cache slice from the CodePlane server.

This script is injected into .codeplane/scripts/ at ``cpl init`` / ``cpl up``
time.  It assumes ``config.yaml`` is always at ``../config.yaml`` relative to
its own location (i.e. ``.codeplane/config.yaml``).

Zero non-stdlib dependencies.  Works on Python 3.10+.

This is a **pure facade** — it fetches a URL and prints the response body.
All formatting, chunking, and rendering logic lives on the server.

Usage::

    python3 .codeplane/scripts/cplcache.py --cache-id <ID> --slice <NAME>

Exit codes:
    0 — success (body printed to stdout)
    2 — bad CLI arguments (argparse default)
    3 — config discovery failure
    4 — connection failure / timeout
    5 — HTTP non-200 response
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_EX_DISCOVERY = 3
_EX_CONNECTION = 4
_EX_HTTP = 5
_TIMEOUT_S = 5


def _load_port() -> int:
    """Read ``port`` from ``../config.yaml`` relative to this script."""
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"cplcache: config not found: {config_path}", file=sys.stderr)
        sys.exit(_EX_DISCOVERY)
    match = re.search(r"^port:\s*(\d+)", text, re.MULTILINE)
    if not match:
        print(f"cplcache: 'port' not found in {config_path}", file=sys.stderr)
        sys.exit(_EX_DISCOVERY)
    return int(match.group(1))


def _fetch(port: int, cache_id: str, slice_name: str) -> str:
    """GET the rendered text slice from the local server."""
    url = f"http://127.0.0.1:{port}/sidecar/cache/slice?cache={cache_id}&path={slice_name}"
    req = Request(url, method="GET")  # noqa: S310
    try:
        with urlopen(req, timeout=_TIMEOUT_S) as resp:  # noqa: S310
            body: bytes = resp.read()
    except HTTPError as exc:
        print(f"cplcache: HTTP {exc.code} from server", file=sys.stderr)
        sys.exit(_EX_HTTP)
    except (URLError, OSError, TimeoutError) as exc:
        print(f"cplcache: connection failed: {exc}", file=sys.stderr)
        sys.exit(_EX_CONNECTION)
    return body.decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cplcache",
        description="Fetch a pre-rendered cache slice from the CodePlane server.",
    )
    parser.add_argument("--cache-id", required=True, help="Cache entry identifier")
    parser.add_argument("--slice", required=True, dest="slice_name", help="Slice name")
    args = parser.parse_args()

    port = _load_port()
    body = _fetch(port, args.cache_id, args.slice_name)
    print(body, end="")


if __name__ == "__main__":
    main()
