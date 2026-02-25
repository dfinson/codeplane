"""Graph expansion — utility helpers for source reading and signatures.

Single Responsibility: Low-level file I/O and def signature formatting.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from codeplane.index.models import DefFact
    from codeplane.mcp.context import AppContext


# ===================================================================
# Low-level helpers
# ===================================================================


def _compute_sha256(full_path: Path) -> str:
    """Compute SHA256 of file contents."""
    return hashlib.sha256(full_path.read_bytes()).hexdigest()


def _read_lines(full_path: Path, start: int, end: int) -> str:
    """Read lines [start, end] (1-indexed, inclusive) from a file."""
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    lines = text.splitlines(keepends=True)
    s = max(0, start - 1)
    e = min(len(lines), end)
    return "".join(lines[s:e])


def _def_signature_text(d: DefFact) -> str:
    """Build a compact one-line signature for a DefFact."""
    parts = [f"{d.kind} {d.name}"]
    if d.signature_text:
        sig = d.signature_text if d.signature_text.startswith("(") else f"({d.signature_text})"
        parts.append(sig)
    if d.return_type:
        parts.append(f" -> {d.return_type}")
    return "".join(parts)


async def _file_path_for_id(app_ctx: AppContext, file_id: int) -> str:
    """Resolve a file_id to its repo-relative path."""
    from codeplane.index.models import File as FileModel

    with app_ctx.coordinator.db.session() as session:
        f = session.get(FileModel, file_id)
        return f.path if f else "unknown"
