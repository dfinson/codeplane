#!/usr/bin/env python3
"""cplcache -- read a pre-rendered cache slice from disk JSON.

This script is injected into .codeplane/scripts/ at ``cpl init`` / ``cpl up``
time.  Cache files live at ``../cache/<cache_id>.json`` relative to this
script (i.e. ``.codeplane/cache/``).

Zero non-stdlib dependencies.  Works on Python 3.10+.

Each cache JSON file is a flat dict of ``slice_name -> rendered_text``.
The server pre-renders all slices at cache-put time so this script is a
pure read-and-print facade.

Usage::

    python3 .codeplane/scripts/cplcache.py --cache-id <ID> --slice <NAME>

Exit codes:
    0 -- success (body printed to stdout)
    2 -- bad CLI arguments (argparse default)
    3 -- cache file not found
    5 -- slice not found in cache
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_EX_DISCOVERY = 3
_EX_SLICE = 5


def _read_local(cache_id: str, slice_name: str) -> str:
    """Read a pre-rendered slice from the local disk cache."""
    cache_dir = Path(__file__).resolve().parent.parent / "cache"
    cache_file = cache_dir / f"{cache_id}.json"
    if not cache_file.exists():
        print(f"cplcache: cache file not found: {cache_file}", file=sys.stderr)
        sys.exit(_EX_DISCOVERY)
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"cplcache: failed to read cache: {exc}", file=sys.stderr)
        sys.exit(_EX_DISCOVERY)
    if slice_name not in data:
        available = ", ".join(sorted(data.keys()))
        print(
            f"cplcache: slice '{slice_name}' not found. Available: {available}",
            file=sys.stderr,
        )
        sys.exit(_EX_SLICE)
    return str(data[slice_name])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cplcache",
        description="Read a pre-rendered cache slice from disk JSON.",
    )
    parser.add_argument("--cache-id", required=True, help="Cache entry identifier")
    parser.add_argument("--slice", required=True, dest="slice_name", help="Slice name")
    args = parser.parse_args()

    body = _read_local(args.cache_id, args.slice_name)
    print(body, end="")


if __name__ == "__main__":
    main()
