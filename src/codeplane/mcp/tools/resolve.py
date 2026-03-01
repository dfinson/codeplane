"""Recon resolve tool — fetch full content + sha256 for selected files.

This is the bridge between ``recon`` (discovery) and ``refactor_edit`` (mutation).
After recon returns scaffold + lite tiers, agents call ``recon_resolve`` to
fetch full file content and sha256 hashes for the specific files they want to
read or edit.

Design:
- Requires candidate_id values from a prior ``recon`` call.
- Raw file paths are NOT accepted — forces use of recon output.
- Requires a justification (50+ chars) explaining the resolve batch.
- Returns full content + sha256 for each requested file.
- Agentic hint routes agents to the right next tool.
"""

import hashlib
from typing import TYPE_CHECKING, Any

import structlog
from fastmcp import Context
from pydantic import BaseModel, Field

from codeplane.mcp.delivery import wrap_response
from codeplane.mcp.errors import MCPError, MCPErrorCode
from codeplane.mcp.session import EditTicket

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext

log = structlog.get_logger(__name__)


# ── Constants ──
_MAX_TARGETS = 10
_MAX_SPAN_LINES = 500
_MIN_JUSTIFICATION_CHARS = 50


# ── Parameter Models ──


class ResolveTarget(BaseModel):
    """A file (or span) to resolve, identified by candidate_id from recon."""

    candidate_id: str = Field(
        description="candidate_id from recon scaffold_files or lite_files output.",
    )
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
                "Files to resolve. Each target uses candidate_id from "
                "recon output (not raw paths). Max 10 targets per call."
            ),
        ),
        justification: str = Field(
            description=(
                "Explain your resolve batch (50+ chars): how many files, "
                "whether this is the complete working bundle you need, "
                "and your intent (read, edit, review)."
            ),
        ),
    ) -> dict[str, Any]:
        """Fetch full content and sha256 for files found via recon.

        After recon returns scaffolds and lite summaries, call this tool
        to get the actual content you need to read or edit.  The sha256
        hash is required by refactor_edit to ensure edits target the
        correct file version.

        Requires candidate_id values from recon output — raw file paths
        are not accepted.  Include a justification (50+ chars) explaining
        your resolve batch.

        Returns per-file: path, content (or span), sha256, line_count.
        """
        # ── Validate justification ──
        if not justification or len(justification.strip()) < _MIN_JUSTIFICATION_CHARS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=(
                    f"justification must be at least {_MIN_JUSTIFICATION_CHARS} "
                    f"characters (got {len(justification.strip()) if justification else 0})."
                ),
                remediation=(
                    "Explain: how many files you're requesting, whether this is "
                    "the complete set you'll need, and your intent (read/edit/review)."
                ),
            )

        # ── Validate target count ──
        if not targets:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message="targets list must not be empty.",
                remediation="Provide at least one ResolveTarget with a candidate_id.",
            )
        if len(targets) > _MAX_TARGETS:
            raise MCPError(
                code=MCPErrorCode.INVALID_PARAMS,
                message=f"Too many targets ({len(targets)}). Max is {_MAX_TARGETS}.",
                remediation=f"Split into batches of {_MAX_TARGETS} or fewer.",
            )

        # ── Resolve candidate_ids → paths via session mapping ──
        id_to_path: dict[str, str] = {}
        try:
            session = app_ctx.session_manager.get_or_create(ctx.session_id)
            # Merge all recon candidate maps for this session
            for cmap in session.candidate_maps.values():
                id_to_path.update(cmap)
        except Exception:  # noqa: BLE001
            pass

        if not id_to_path:
            return {
                "error": {
                    "code": "RECON_REQUIRED",
                    "message": (
                        "No candidate mappings found. "
                        "Call recon first to discover files, then use "
                        "candidate_id values from the recon output."
                    ),
                },
                "tool_hint": {
                    "tool": "recon",
                    "params": {"task": "<describe your task>"},
                },
            }

        repo_root = app_ctx.coordinator.repo_root
        resolved: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for target in targets:
            # Look up path from candidate_id
            path = id_to_path.get(target.candidate_id)
            if path is None:
                errors.append(
                    {
                        "candidate_id": target.candidate_id,
                        "error": (
                            f"Unknown candidate_id '{target.candidate_id}'. "
                            "Use candidate_id values from recon scaffold_files "
                            "or lite_files output."
                        ),
                    }
                )
                continue

            file_path = repo_root / path
            if not file_path.exists():
                errors.append(
                    {
                        "candidate_id": target.candidate_id,
                        "path": path,
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
                        "candidate_id": target.candidate_id,
                        "path": path,
                        "error": f"Read failed: {exc}",
                    }
                )
                continue

            # Check for binary content
            if b"\x00" in raw[:512]:
                errors.append(
                    {
                        "candidate_id": target.candidate_id,
                        "path": path,
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
                            "candidate_id": target.candidate_id,
                            "path": path,
                            "error": (
                                f"Span too large ({end - start} lines). Max is {_MAX_SPAN_LINES}."
                            ),
                        }
                    )
                    continue

                span_lines = all_lines[start:end]
                resolved.append(
                    {
                        "candidate_id": target.candidate_id,
                        "path": path,
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
                        "candidate_id": target.candidate_id,
                        "path": path,
                        "content": content_str,
                        "file_sha256": sha256,
                        "line_count": len(all_lines),
                    }
                )

        # ── Mint edit tickets ──
        ticket_ids: list[str] = []
        try:
            session = app_ctx.session_manager.get_or_create(ctx.session_id)
            # Legacy: keep resolved_files for backward compat
            if "resolved_files" not in session.counters:
                session.counters["resolved_files"] = {}  # type: ignore[assignment]
            resolved_files: dict[str, str] = session.counters["resolved_files"]  # type: ignore[assignment]
            for r in resolved:
                resolved_files[r["path"]] = r["file_sha256"]

            # Mint one EditTicket per resolved file
            for r in resolved:
                cid = r["candidate_id"]
                sha_prefix = r["file_sha256"][:8]
                ticket_id = f"{cid}:{sha_prefix}"
                session.edit_tickets[ticket_id] = EditTicket(
                    ticket_id=ticket_id,
                    path=r["path"],
                    sha256=r["file_sha256"],
                    candidate_id=cid,
                    issued_by="resolve",
                )
                r["edit_ticket"] = ticket_id
                ticket_ids.append(ticket_id)
        except Exception:  # noqa: BLE001
            pass

        # ── Build agentic hint ──
        resolved_paths = [r["path"] for r in resolved]
        paths_str = ", ".join(resolved_paths[:5])
        if len(resolved_paths) > 5:
            paths_str += f" (+{len(resolved_paths) - 5} more)"

        ticket_str = ", ".join(ticket_ids[:5])
        if len(ticket_ids) > 5:
            ticket_str += f" (+{len(ticket_ids) - 5} more)"

        agentic_hint = (
            f"Resolved {len(resolved)} file(s): {paths_str}.\n"
            f"Edit tickets minted: {ticket_str}\n\n"
            "NEXT STEPS — choose the action that matches your intent:\n\n"
            "EDIT CODE → refactor_edit(edits=[{edit_ticket, old_content, "
            "new_content}])  — use edit_ticket (NOT path+sha256)\n"
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

        return wrap_response(
            response,
            resource_kind="resolve_result",
            session_id=ctx.session_id,
        )
