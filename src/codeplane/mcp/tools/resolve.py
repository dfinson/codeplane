"""Recon resolve tool — fetch full content + sha256 for selected files.

This is the bridge between ``recon`` (discovery) and ``refactor_edit`` (mutation).
After recon returns scaffold + lite tiers, agents call ``recon_resolve`` to
fetch full file content and sha256 hashes for the specific files they want to
read or edit.

Design:
- Requires a prior ``recon`` call in the session (flow gate).
- Accepts a list of file paths (and optional span ranges).
- Returns full content + sha256 for each requested file.
- Agentic hint routes agents to the right next tool.
"""

import hashlib
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import BaseModel, Field

from codeplane.mcp.delivery import wrap_existing_response
from codeplane.mcp.errors import MCPError, MCPErrorCode

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


# ── Constants ──
_MAX_TARGETS = 10
_MAX_SPAN_LINES = 500


# ── Parameter Models ──


class ResolveTarget(BaseModel):
    """A file (or span within a file) to resolve."""

    path: str = Field(description="Repository-relative file path.")
    start_line: int | None = Field(
        None,
        description="Optional start line (1-based). If omitted, full file is returned.",
    )
    end_line: int | None = Field(
        None,
        description="Optional end line (1-based, inclusive). Required if start_line is set.",
    )


# ── Helpers ──


def _compute_file_sha256(content: bytes) -> str:
    """Compute SHA256 of raw file bytes."""
    return hashlib.sha256(content).hexdigest()


# ── Tool Registration ──


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register the recon_resolve tool."""

    @mcp.tool(
        annotations={
            "title": "Resolve: fetch full content + sha256",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def recon_resolve(
        ctx: Context,  # noqa: ARG001
        targets: list[ResolveTarget] = Field(
            description=(
                "Files to resolve. Each target specifies a path and "
                "optionally a line range. Max 10 targets per call."
            ),
        ),
    ) -> dict[str, Any]:
        """Fetch full content and sha256 for files found via recon.

        After recon returns scaffolds and lite summaries, call this tool
        to get the actual content you need to read or edit.  The sha256
        hash is required by refactor_edit to ensure edits target the
        correct file version.

        Returns per-file: path, content (or span), sha256, line_count.
        """
        # ── Validate target count ──
        if not targets:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="targets list must not be empty.",
                remediation="Provide at least one ResolveTarget in the targets list.",
            )
        if len(targets) > _MAX_TARGETS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=f"Too many targets ({len(targets)}). Max is {_MAX_TARGETS}.",
                remediation=f"Split into batches of {_MAX_TARGETS} or fewer.",
            )

        # ── Flow gate: require prior recon call ──
        try:
            session = app_ctx.session_manager.get_or_create(ctx.session_id)
            recon_called = session.counters.get("recon_called", 0)
            if not recon_called:
                return {
                    "error": {
                        "code": "RECON_REQUIRED",
                        "message": (
                            "recon_resolve requires a prior recon call. "
                            "Call recon first to discover relevant files."
                        ),
                    },
                    "tool_hint": {
                        "tool": "recon",
                        "params": {"task": "<describe your task>"},
                    },
                }
        except Exception:  # noqa: BLE001
            pass  # If session mgmt fails, allow the call

        repo_root = app_ctx.coordinator.repo_root
        resolved: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for target in targets:
            file_path = repo_root / target.path
            if not file_path.exists():
                errors.append(
                    {
                        "path": target.path,
                        "error": "File not found",
                    }
                )
                continue

            # Read file content
            try:
                raw = file_path.read_bytes()
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "path": target.path,
                        "error": f"Read failed: {exc}",
                    }
                )
                continue

            # Check for binary content
            if b"\x00" in raw[:512]:
                errors.append(
                    {
                        "path": target.path,
                        "error": "Binary file — cannot resolve content",
                    }
                )
                continue

            content_str = raw.decode("utf-8", errors="replace")
            sha256 = _compute_file_sha256(raw)
            all_lines = content_str.splitlines(keepends=True)

            # Handle span extraction
            if target.start_line is not None:
                end_line = target.end_line or len(all_lines)
                start = max(1, target.start_line) - 1  # Convert to 0-based
                end = min(len(all_lines), end_line)

                if end - start > _MAX_SPAN_LINES:
                    errors.append(
                        {
                            "path": target.path,
                            "error": (
                                f"Span too large ({end - start} lines). Max is {_MAX_SPAN_LINES}."
                            ),
                        }
                    )
                    continue

                span_lines = all_lines[start:end]
                resolved.append(
                    {
                        "path": target.path,
                        "content": "".join(span_lines),
                        "file_sha256": sha256,
                        "line_count": len(all_lines),
                        "span": {
                            "start_line": start + 1,
                            "end_line": end,
                        },
                    }
                )
            else:
                resolved.append(
                    {
                        "path": target.path,
                        "content": content_str,
                        "file_sha256": sha256,
                        "line_count": len(all_lines),
                    }
                )

        # Track resolved files + sha256 in session for refactor_edit gate
        try:
            session = app_ctx.session_manager.get_or_create(ctx.session_id)
            if "resolved_files" not in session.counters:
                session.counters["resolved_files"] = {}  # type: ignore[assignment]
            resolved_files: dict[str, str] = session.counters["resolved_files"]  # type: ignore[assignment]
            for r in resolved:
                resolved_files[r["path"]] = r["file_sha256"]
        except Exception:  # noqa: BLE001
            pass

        # ── Build agentic hint ──
        resolved_paths = [r["path"] for r in resolved]
        paths_str = ", ".join(resolved_paths[:5])
        if len(resolved_paths) > 5:
            paths_str += f" (+{len(resolved_paths) - 5} more)"

        agentic_hint = (
            f"Resolved {len(resolved)} file(s): {paths_str}.\n\n"
            "NEXT STEPS — choose the action that matches your intent:\n\n"
            "EDIT CODE → refactor_edit(edits=[{path, old_content, new_content, "
            "expected_file_sha256}])\n"
            'RENAME A SYMBOL → refactor_rename(symbol="OldName", new_name="NewName")\n'
            'MOVE A FILE → refactor_move(source="old/path.py", '
            'destination="new/path.py")\n'
            'DELETE/REMOVE CODE → refactor_impact(symbol="SymbolName") first\n'
            "RESEARCH / REVIEW (no changes) → respond directly. "
            'semantic_diff(base="...") for branch comparison.\n\n'
            "After ANY code change → checkpoint(changed_files=[...], "
            'commit_message="...")\n'
            "  Ask the user whether they want push=True or push=False."
        )

        response: dict[str, Any] = {
            "resolved": resolved,
            "agentic_hint": agentic_hint,
        }
        if errors:
            response["errors"] = errors

        return wrap_existing_response(
            response,
            resource_kind="resolve_result",
        )
