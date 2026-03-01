#!/usr/bin/env python3
"""cplcache — fetch a pre-computed cache slice from the CodePlane server.

This script is injected into .codeplane/scripts/ at ``cpl init`` / ``cpl up``
time.  It assumes ``config.yaml`` is always at ``../config.yaml`` relative to
its own location (i.e. ``.codeplane/config.yaml``).

Zero non-stdlib dependencies.  Works on Python 3.10+.

Usage::

    python3 .codeplane/scripts/cplcache.py --cache-id <ID> --slice <NAME>

The JSON envelope from the server is stripped automatically: the ``value``
field is extracted and printed directly.  Strings (file content, scaffold
code) are printed raw; dicts/lists are printed as compact JSON.

Exit codes:
    0 — success (body printed to stdout)
    2 — bad CLI arguments (argparse default)
    3 — config discovery failure
    4 — connection failure / timeout
    5 — HTTP non-200 response
    6 — server returned an error payload
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_EX_DISCOVERY = 3
_EX_CONNECTION = 4
_EX_HTTP = 5
_EX_PAYLOAD = 6
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
    """GET the slice from the local server and return the response body."""
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


def _format_file_item(item: dict) -> str:  # type: ignore[type-arg]
    """Format a resolved or scaffold file item with a metadata header.

    Prints a concise ``# path | key=val ...`` header line followed by the
    raw content (for resolved items) or formatted scaffold text.
    """
    parts: list[str] = []
    path = item.get("path", "")
    if path:
        parts.append(path)
    cid = item.get("candidate_id", "")
    if cid:
        parts.append(f"candidate_id={cid}")
    sha = item.get("file_sha256", "")
    if sha:
        parts.append(f"sha256={sha[:16]}")
    lc = item.get("line_count")
    if lc is not None:
        parts.append(f"{lc} lines")
    ticket = item.get("edit_ticket", "")
    if ticket:
        parts.append(f"edit_ticket={ticket}")
    span = item.get("span")
    if span:
        parts.append(f"span={span.get('start_line')}-{span.get('end_line')}")

    header = "# " + " | ".join(parts) if parts else ""

    # Resolved items have content as a string
    content = item.get("content")
    if isinstance(content, str):
        body = content
    else:
        # Scaffold items — scaffold may be a dict or a string
        scaffold = item.get("scaffold")
        if isinstance(scaffold, dict):
            body = _format_scaffold(scaffold)
        elif isinstance(scaffold, str):
            body = scaffold
        else:
            body = ""

    if header:
        return header + "\n" + body
    return body


def _format_scaffold(scaffold: dict) -> str:  # type: ignore[type-arg]
    """Format a scaffold dict as concise readable text."""
    lines: list[str] = []
    summary = scaffold.get("summary", "")
    if summary:
        lines.append(summary)
    else:
        # Build a fallback summary line
        lang = scaffold.get("language", "")
        total = scaffold.get("total_lines")
        meta = []
        if lang:
            meta.append(lang)
        if total is not None:
            meta.append(f"{total} lines")
        if meta:
            lines.append(" | ".join(meta))
    imports = scaffold.get("imports", [])
    if imports:
        lines.append(f"imports: {', '.join(str(i) for i in imports)}")
    symbols = scaffold.get("symbols", [])
    if symbols:
        lines.append(f"symbols: {', '.join(str(s) for s in symbols)}")
    return "\n".join(lines)


def _unwrap_list(items: list) -> str:  # type: ignore[type-arg]
    """Format a list of items from a chunked sidecar section.

    Items that look like file entries (have ``content`` or ``scaffold``)
    are formatted with metadata headers.  Others are printed as compact JSON.
    """
    formatted: list[str] = []
    for item in items:
        if isinstance(item, dict) and ("content" in item or "scaffold" in item):
            formatted.append(_format_file_item(item))
        elif isinstance(item, dict):
            formatted.append(json.dumps(item, separators=(",", ":")))
        else:
            formatted.append(str(item))
    return "\n---\n".join(formatted)


def _unwrap(body: str) -> str:
    """Extract ``value`` from the JSON envelope and format for terminal.

    Lists of file items (from chunked sections) are iterated and each item
    is printed with a concise metadata header.  Single resolved/scaffold
    items get the same treatment.  Plain strings are printed raw.  Other
    values are printed as compact JSON.  Error payloads go to stderr.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return body  # server sent non-JSON — pass through

    if isinstance(data, dict) and "error" in data:
        print(f"cplcache: {data['error']}", file=sys.stderr)
        sys.exit(_EX_PAYLOAD)

    if isinstance(data, dict) and "value" in data:
        val = data["value"]
        # List of items (chunked section) — format each item
        if isinstance(val, list):
            return _unwrap_list(val)
        # Single resolved or scaffold file item
        if isinstance(val, dict) and ("content" in val or "scaffold" in val):
            return _format_file_item(val)
        if isinstance(val, str):
            return val
        return json.dumps(val, separators=(",", ":"))

    # No value key — return compact JSON of whatever we got
    return json.dumps(data, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cplcache",
        description="Fetch a pre-computed cache slice from the CodePlane server.",
    )
    parser.add_argument("--cache-id", required=True, help="Cache entry identifier")
    parser.add_argument("--slice", required=True, dest="slice_name", help="Slice name")
    args = parser.parse_args()

    port = _load_port()
    body = _fetch(port, args.cache_id, args.slice_name)
    output = _unwrap(body)

    print(output, end="")


if __name__ == "__main__":
    main()
