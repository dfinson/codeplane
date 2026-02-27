"""Entry point for ``python3 -m cplcache``.

Fetches and prints a single pre-computed slice from the running CodePlane
server.  The server computes slices; the client is a pure transport shim.

Usage::

    python3 -m cplcache --cache-id <ID> --slice <SLICE_NAME>

Exit codes:
    0 — success
    2 — bad CLI arguments (argparse default)
    3 — repo root or config discovery failure
    4 — connection failure / timeout
    5 — HTTP non-200 response
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

_EX_DISCOVERY = 3
_EX_CONNECTION = 4
_EX_HTTP = 5

# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _find_repo_root() -> Path:
    """Walk up from cwd until a ``.git/`` directory is found."""
    current = Path.cwd().resolve()
    while True:
        if (current / ".git").is_dir():
            return current
        parent = current.parent
        if parent == current:
            # Filesystem root
            print("cplcache: .git/ not found (are you in a git repo?)", file=sys.stderr)
            sys.exit(_EX_DISCOVERY)
        current = parent


def _load_port(repo_root: Path) -> int:
    """Read ``port`` from ``.cpl/run/config.json`` under *repo_root*."""
    config_path = repo_root / ".cpl" / "run" / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        port = int(data["port"])
    except (FileNotFoundError, KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"cplcache: cannot read port from {config_path}: {exc}", file=sys.stderr)
        sys.exit(_EX_DISCOVERY)
    return port


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_TIMEOUT_S = 5


def _fetch(port: int, cache_id: str, slice_name: str) -> str:
    """GET the slice from the local server and return the response body."""
    url = f"http://127.0.0.1:{port}/cache?id={cache_id}&slice={slice_name}"
    req = Request(url, method="GET")  # noqa: S310  — localhost only
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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cplcache",
        description="Fetch a pre-computed cache slice from the CodePlane server.",
    )
    parser.add_argument("--cache-id", required=True, help="Cache entry identifier")
    parser.add_argument("--slice", required=True, dest="slice_name", help="Slice name")
    args = parser.parse_args()

    repo_root = _find_repo_root()
    port = _load_port(repo_root)
    body = _fetch(port, args.cache_id, args.slice_name)
    print(body, end="")


if __name__ == "__main__":
    main()
