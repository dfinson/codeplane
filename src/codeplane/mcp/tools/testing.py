"""Testing MCP tools - test discovery and execution.

Split into verb-first tools:
- discover_test_targets: Find test targets
- run_test_targets: Execute tests
- get_test_run_status: Check run progress
- cancel_test_run: Abort a run
"""

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from fastmcp.utilities.json_schema import dereference_refs
from pydantic import Field

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from codeplane.mcp.context import AppContext
    from codeplane.testing.models import TestResult


# Session key for broad-run confirmation tokens
_BROAD_RUN_TOKEN_KEY = "__broad_run_confirmation_token__"


# =============================================================================
# Target Matching Helpers
# =============================================================================


def _normalize_selector(selector: str) -> str:
    """Normalize target selector for path matching.

    Handles Go package selectors (./path), wildcard selectors (./...),
    and project root selectors (.).
    """
    if selector in (".", "./..."):
        return ""
    if selector.startswith("./"):
        return selector[2:]
    return selector


def _target_matches_affected_files(
    target: Any,
    affected_paths: set[str],
    repo_root: Path,
) -> bool:
    """Check if a test target's scope contains any affected test file.

    For 'file' targets (e.g., Python pytest), this is an exact path match.
    For 'package' targets (e.g., Go packages), checks if any affected file
    is within the package directory.
    For 'project' targets (e.g., Maven modules, Gradle), checks if any affected
    file is within the project root scope.
    """
    ws = Path(target.workspace_root)
    sel = _normalize_selector(target.selector)
    scope_abs = ws / sel if sel else ws

    try:
        scope_rel = str(scope_abs.relative_to(repo_root))
    except ValueError:
        # Target workspace outside repo root, fall back to exact selector match
        return target.selector in affected_paths

    if scope_rel == ".":
        # Scope is the entire repo — all files match
        return bool(affected_paths)

    return any(p == scope_rel or p.startswith(scope_rel + "/") for p in affected_paths)


# =============================================================================
# Summary Helpers
# =============================================================================


def _summarize_discover(count: int, targets: list[Any] | None = None) -> str:
    if count == 0:
        return "no test targets found"
    if targets:
        # Group by language
        by_lang: dict[str, int] = {}
        for t in targets:
            lang = t.language if hasattr(t, "language") else "unknown"
            by_lang[lang] = by_lang.get(lang, 0) + 1
        # Format: "12 targets (10 python, 2 javascript)"
        lang_parts = [f"{v} {k}" for k, v in sorted(by_lang.items(), key=lambda x: -x[1])[:3]]
        if len(by_lang) > 3:
            lang_parts.append(f"+{len(by_lang) - 3} more")
        return f"{count} targets ({', '.join(lang_parts)})"
    return f"{count} test targets"


def _display_discover(count: int, targets: list[Any]) -> str:
    """Human-friendly message for discover action."""
    if count == 0:
        return "No test targets found in this repository."
    # Group by language
    by_lang: dict[str, int] = {}
    for t in targets:
        lang = t.language if hasattr(t, "language") else "unknown"
        by_lang[lang] = by_lang.get(lang, 0) + 1
    lang_parts = [f"{v} {k}" for k, v in sorted(by_lang.items(), key=lambda x: -x[1])]
    return f"Found {count} test targets: {', '.join(lang_parts)}."


def _display_run_start(result: "TestResult") -> str:
    """Human-friendly message for run action."""
    if not result.run_status:
        return "Test run initiated."
    status = result.run_status
    total = status.progress.targets.total if status.progress else 0
    return f"Test run started: {total} targets. Run ID: {status.run_id}"


def _display_run_status(result: "TestResult") -> str | None:
    """Human-friendly message for status - only on completion or failure."""
    if not result.run_status:
        return None
    status = result.run_status
    if status.status == "completed":
        p = status.progress
        if p and p.cases.failed > 0:
            return (
                f"Tests completed: {p.cases.passed} passed, {p.cases.failed} FAILED "
                f"in {status.duration_seconds:.1f}s."
            )
        elif p:
            return f"Tests completed: {p.cases.passed} passed in {status.duration_seconds:.1f}s."
        return "Tests completed."
    elif status.status == "cancelled":
        return "Test run was cancelled."
    elif status.status == "failed":
        return "Test run failed to start."
    # Running - no display needed (avoid noise on polling)
    return None


def _summarize_run(result: "TestResult") -> str:
    if not result.run_status:
        return "no run status"

    status = result.run_status
    if status.progress:
        p = status.progress
        if status.status == "completed":
            if p.cases.failed > 0:
                return (
                    f"{p.cases.passed} passed, {p.cases.failed} failed "
                    f"({status.duration_seconds:.1f}s)"
                )
            return f"{p.cases.passed} passed ({status.duration_seconds:.1f}s)"
        elif status.status == "running":
            parts = [f"{p.cases.passed} passed"]
            if p.cases.failed:
                parts.append(f"{p.cases.failed} failed")
            return (
                f"running: {p.targets.completed}/{p.targets.total} targets "
                f"({', '.join(parts)})"
            )
        elif status.status == "cancelled":
            return "cancelled"
        elif status.status == "failed":
            return "run failed"
        # Other statuses
        parts: list[str] = [status.status]
        if p.cases.total > 0:
            parts.append(f"{p.cases.passed}/{p.cases.total} passed")
            if p.cases.failed:
                parts.append(f"{p.cases.failed} failed")
        return ", ".join(parts)

    return status.status


def _target_id_to_safe_name(target_id: str) -> str:
    """Convert target_id to safe filename (matches ops.py logic)."""
    return target_id.replace("/", "_").replace(":", "_")


def _build_logs_hint(
    artifact_dir: str | None,
    status_str: str,
    target_selectors: list[str] | None = None,
) -> str | None:
    """Build hint for where to find test logs.

    Args:
        artifact_dir: Path to artifact directory
        status_str: Current run status
        target_selectors: List of target selectors that were executed
    """
    if not artifact_dir:
        return None

    # Build list of actual artifact files if we have target info
    file_examples: list[str] = []
    if target_selectors:
        # Show up to 3 examples of actual file names
        for selector in target_selectors[:3]:
            # Target IDs use "test:" prefix, selectors don't - construct the target_id
            target_id = f"test:{selector}"
            safe_name = _target_id_to_safe_name(target_id)
            file_examples.append(f"  - {safe_name}.stdout.txt")
        if len(target_selectors) > 3:
            file_examples.append(f"  ... and {len(target_selectors) - 3} more targets")

    if status_str == "running":
        if file_examples:
            return (
                f"Test output is being written to: {artifact_dir}/\n"
                + "\n".join(file_examples)
                + "\n"
                "Each target also produces .stderr.txt (if any) and .xml (JUnit results).\n"
                "Use read_files to inspect logs for completed targets."
            )
        return (
            f"Test output is being written to: {artifact_dir}/\n"
            "Use read_files to inspect logs for completed targets."
        )
    elif status_str in ("completed", "failed", "cancelled"):
        if file_examples:
            return (
                f"Test logs available at: {artifact_dir}/\n" + "\n".join(file_examples) + "\n"
                "Each target also produces .stderr.txt (if any) and .xml (JUnit results).\n"
                "  - result.json: final run summary\n"
                "Use read_files to inspect specific test output."
            )
        return (
            f"Test logs available at: {artifact_dir}/\n"
            "  - result.json: final run summary\n"
            "Use read_files to inspect specific test output."
        )
    return None


def _build_coverage_hint(
    coverage_artifacts: list[dict[str, str]],
    target_selectors: list[str] | None = None,
) -> str:
    """Build guidance for interpreting coverage data.

    The coverage file includes all source files in the project, not just
    those exercised by the tests. This hint tells the agent which source
    files are likely relevant based on the test targets.
    """
    if not coverage_artifacts:
        return "No coverage data available."

    hints: list[str] = []

    # Add executed targets if available
    if target_selectors:
        hints.append(
            "Executed test targets:\n"
            + "\n".join(f"  - {sel}" for sel in target_selectors[:10])
            + (
                f"\n  ... and {len(target_selectors) - 10} more"
                if len(target_selectors) > 10
                else ""
            )
        )

    # Dedupe coverage artifacts by path (multiple targets may share same output file)
    seen_paths: set[str] = set()
    deduped: list[dict[str, str]] = []
    for cov in coverage_artifacts:
        path = cov.get("path", "")
        if path and path not in seen_paths:
            seen_paths.add(path)
            deduped.append(cov)

    for cov in deduped:
        fmt = cov.get("format", "unknown")
        path = cov.get("path", "")
        pack_id = cov.get("pack_id", "")

        if fmt == "lcov":
            hints.append(
                f"Coverage file: {path}\n"
                "  Format: LCOV (line-by-line coverage)\n"
                "  Reading: Look for 'SF:' (source file), 'DA:line,count' (line hits), "
                "'LF:' (lines found), 'LH:' (lines hit)\n"
                "  Note: File includes ALL project sources. Focus on files matching your test "
                "paths - e.g., if testing 'tests/foo/test_bar.py', look for "
                "'src/*/foo/bar.py' entries."
            )
        elif fmt == "istanbul":
            hints.append(
                f"Coverage directory: {path}\n"
                "  Format: Istanbul/NYC (JSON + LCOV)\n"
                "  Files: coverage-summary.json (overview), lcov.info (line detail)\n"
                "  Note: Focus on source files corresponding to your test targets."
            )
        elif fmt == "gocov":
            hints.append(
                f"Coverage file: {path}\n"
                "  Format: Go coverage profile\n"
                "  Reading: 'mode: set/count/atomic', then 'file:start.col,end.col count'\n"
                "  Note: Go coverage is package-scoped - results match tested packages."
            )
        elif fmt == "jacoco":
            hints.append(
                f"Coverage directory: {path}\n"
                "  Format: JaCoCo (XML + HTML)\n"
                "  Files: jacoco.xml (machine-readable), index.html (human-readable)\n"
                "  Note: Coverage tied to modules configured in build file."
            )
        else:
            hints.append(f"Coverage: {path} (format: {fmt}, runner: {pack_id})")

    return "\n\n".join(hints)


def _serialize_test_result(result: "TestResult", is_action: bool = False) -> dict[str, Any]:
    """Convert TestResult to dict.

    Args:
        result: The test result to serialize
        is_action: If True, include display_to_user for run start
    """
    output: dict[str, Any] = {
        "action": result.action,
        "summary": _summarize_run(result),
    }

    # Add display_to_user for actions and terminal states
    if is_action:
        output["display_to_user"] = _display_run_start(result)
    else:
        display = _display_run_status(result)
        if display:
            output["display_to_user"] = display

    if result.run_status:
        status = result.run_status
        # Compute poll hint based on current progress
        poll_hint = status.compute_poll_hint()

        output["run_status"] = {
            "run_id": status.run_id,
            "status": status.status,
            "duration_seconds": status.duration_seconds,
            "artifact_dir": status.artifact_dir,
            "poll_after_seconds": poll_hint,
        }
        if status.progress:
            progress = status.progress
            output["run_status"]["progress"] = {
                "targets": {
                    "total": progress.targets.total,
                    "completed": progress.targets.completed,
                    "running": progress.targets.running,
                    "failed": progress.targets.failed,
                },
                "cases": {
                    "total": progress.cases.total,
                    "passed": progress.cases.passed,
                    "failed": progress.cases.failed,
                    "skipped": progress.cases.skipped,
                    "errors": progress.cases.errors,
                },
                "total": progress.total,
                "completed": progress.completed,
                "passed": progress.passed,
                "failed": progress.failed,
                "skipped": progress.skipped,
            }
        if status.failures:
            output["run_status"]["failures"] = [
                {
                    "name": f.name,
                    "path": f.path,
                    "line": f.line,
                    "message": f.message,
                    "traceback": f.traceback,
                    "classname": f.classname,
                    "duration_seconds": f.duration_seconds,
                }
                for f in status.failures
            ]
        if status.diagnostics:
            output["run_status"]["diagnostics"] = [
                {
                    "target_id": d.target_id,
                    "error_type": d.error_type,
                    "error_detail": d.error_detail,
                    "suggested_action": d.suggested_action,
                    "command": d.command,
                    "working_directory": d.working_directory,
                    "exit_code": d.exit_code,
                }
                for d in status.diagnostics
            ]
        if status.coverage:
            output["run_status"]["coverage"] = status.coverage
            # Add coverage guidance for the agent (text hint)
            output["run_status"]["coverage_hint"] = _build_coverage_hint(
                status.coverage,
                status.target_selectors,
            )
            # Add parsed coverage stats (structured data)
            from codeplane.testing.coverage import CoverageArtifact, parse_coverage_summary

            coverage_stats: list[dict[str, Any]] = []
            for cov_dict in status.coverage:
                artifact = CoverageArtifact(
                    format=cov_dict.get("format", "unknown"),
                    path=Path(cov_dict.get("path", "")),
                    pack_id=cov_dict.get("pack_id", ""),
                    invocation_id="",
                )
                summary = parse_coverage_summary(artifact)
                if summary and summary.is_valid:
                    coverage_stats.append(summary.to_dict())
            if coverage_stats:
                output["run_status"]["coverage_stats"] = coverage_stats

        # Add logs hint for status checks (not for initial run action)
        if not is_action:
            logs_hint = _build_logs_hint(
                status.artifact_dir,
                status.status,
                status.target_selectors,
            )
            if logs_hint:
                output["run_status"]["logs_hint"] = logs_hint

    if result.agentic_hint:
        output["agentic_hint"] = result.agentic_hint

    return output


# =============================================================================
# Tool Registration
# =============================================================================


def register_tools(mcp: "FastMCP", app_ctx: "AppContext") -> None:
    """Register testing tools with FastMCP server."""

    @mcp.tool
    async def discover_test_targets(
        ctx: Context,
        paths: list[str] | None = Field(None, description="Paths to search for tests"),
        affected_by: list[str] | None = Field(
            None,
            description="Changed file paths. When provided, returns only test targets "
            "affected by these changes (via import graph analysis). "
            "Includes confidence assessment — 'complete' means all files resolved, "
            "'partial' means some files could not be mapped.",
        ),
    ) -> dict[str, Any]:
        """Find test targets in the repository. Returns testable files/directories with runner info."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.discover(paths=paths)
        targets = result.targets or []

        # Impact-aware filtering: use import graph to select affected targets
        impact_info: dict[str, Any] | None = None
        low_confidence_hint: str | None = None
        if affected_by and targets:
            graph_result = await app_ctx.coordinator.get_affected_test_targets(affected_by)
            affected_paths = set(graph_result.test_files)
            targets = [
                t
                for t in targets
                if _target_matches_affected_files(t, affected_paths, app_ctx.repo_root)
            ]

            impact_info = {
                "confidence": graph_result.confidence.tier,
                "resolved_ratio": graph_result.confidence.resolved_ratio,
                "changed_modules": len(graph_result.changed_modules),
                "reasoning": graph_result.confidence.reasoning,
                "total_matches": len(graph_result.matches),
                "high_confidence": len(graph_result.high_confidence_tests),
                "low_confidence": len(graph_result.low_confidence_tests),
            }
            if graph_result.confidence.unresolved_files:
                impact_info["unresolved_files"] = graph_result.confidence.unresolved_files
            if graph_result.low_confidence_tests:
                low_confidence_hint = (
                    f"{len(graph_result.low_confidence_tests)} test(s) matched with low "
                    "confidence (parent module prefix only). Use inspect_affected_tests "
                    "to review uncertain matches before deciding whether to include them."
                )

        output: dict[str, Any] = {
            "action": result.action,
            "targets": [
                {
                    "target_id": t.target_id,
                    "selector": t.selector,
                    "kind": t.kind,
                    "language": t.language,
                    "runner_pack_id": t.runner_pack_id,
                    "workspace_root": t.workspace_root,
                    "estimated_cost": t.estimated_cost,
                    "test_count": t.test_count,
                    "path": t.path,
                    "runner": t.runner,
                }
                for t in targets
            ],
            "summary": _summarize_discover(len(targets), targets),
            "display_to_user": _display_discover(len(targets), targets),
        }

        if impact_info:
            output["impact"] = impact_info
            if low_confidence_hint:
                output["agentic_hint"] = low_confidence_hint
        if result.agentic_hint and not impact_info:
            output["agentic_hint"] = result.agentic_hint
        return output

    @mcp.tool
    async def inspect_affected_tests(
        ctx: Context,
        changed_files: list[str] = Field(
            ...,
            description="Changed file paths to analyze for test impact.",
        ),
    ) -> dict[str, Any]:
        """Inspect how changed files map to test targets via the import graph.

        Returns detailed match information including confidence levels for each
        test file. Use this to review uncertain matches before running tests.
        Analogous to refactor_inspect — surfaces uncertainty so the agent can decide.
        """
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        graph_result = await app_ctx.coordinator.get_affected_test_targets(changed_files)

        matches_out: list[dict[str, Any]] = []
        for m in graph_result.matches:
            matches_out.append(
                {
                    "test_file": m.test_file,
                    "confidence": m.confidence,
                    "source_modules": m.source_modules,
                    "reason": m.reason,
                }
            )

        output: dict[str, Any] = {
            "action": "inspect_affected_tests",
            "changed_modules": graph_result.changed_modules,
            "confidence": {
                "tier": graph_result.confidence.tier,
                "resolved_ratio": graph_result.confidence.resolved_ratio,
                "reasoning": graph_result.confidence.reasoning,
            },
            "matches": matches_out,
            "summary": (
                f"{len(graph_result.high_confidence_tests)} high-confidence, "
                f"{len(graph_result.low_confidence_tests)} low-confidence matches"
            ),
        }

        if graph_result.confidence.unresolved_files:
            output["confidence"]["unresolved_files"] = graph_result.confidence.unresolved_files

        # Coverage gap info
        try:
            gaps = await app_ctx.coordinator.get_coverage_gaps()
            if gaps:
                output["coverage_gaps"] = [
                    {"module": g.module, "file_path": g.file_path}
                    for g in gaps[:20]  # Cap at 20 to avoid noise
                ]
                output["coverage_gaps_total"] = len(gaps)
        except Exception:  # noqa: BLE001
            pass

        if graph_result.low_confidence_tests:
            output["agentic_hint"] = (
                f"{len(graph_result.low_confidence_tests)} match(es) are low-confidence "
                "(parent module prefix only). Review the 'matches' list and "
                "decide whether to include these tests or run only high-confidence ones."
            )
        elif graph_result.confidence.tier == "partial":
            output["agentic_hint"] = (
                "Some changed files could not be resolved to modules. "
                "Review 'confidence.unresolved_files' and consider running "
                "a broader test set if those files are significant."
            )
        else:
            output["agentic_hint"] = (
                "All matches are high-confidence (direct import traced). "
                "Safe to run the listed test targets."
            )

        output["display_to_user"] = (
            f"Import graph analysis: {len(graph_result.matches)} affected test(s) found "
            f"({graph_result.confidence.tier} confidence)."
        )

        return output

    @mcp.tool
    async def run_test_targets(
        ctx: Context,
        targets: list[str] | None = Field(
            None,
            description="Target IDs from discover to run. Use discover_test_targets first to get IDs.",
        ),
        affected_by: list[str] | None = Field(
            None,
            description="Changed file paths for impact-aware test selection. "
            "Internally discovers affected tests and runs only those. "
            "This is the recommended approach for efficient testing after code changes.",
        ),
        target_filter: str | None = Field(
            None,
            description="Filter which TARGETS to run by path substring (e.g. 'test_excludes' runs "
            "only targets containing 'test_excludes' in their path). Fails if no targets match.",
        ),
        test_filter: str | None = Field(
            None,
            description="Filter which TEST NAMES to run within targets (passed to pytest -k, jest "
            "--testNamePattern). Does NOT filter which targets are executed.",
        ),
        tags: list[str] | None = Field(None, description="Filter tests by tags"),
        failed_only: bool = Field(False, description="Run only previously failed tests"),
        parallelism: int | None = Field(None, description="Number of parallel workers"),
        timeout_sec: int | None = Field(None, description="Timeout in seconds"),
        fail_fast: bool = Field(False, description="Stop on first failure"),
        coverage: bool = Field(False, description="Collect coverage data"),
        coverage_dir: str | None = Field(
            None,
            description="Directory to write coverage artifacts (required when coverage=True). "
            "Use map_repo to understand project structure and determine the appropriate "
            "source directory.",
        ),
        confirm_broad_run: str | None = Field(
            None,
            description="Required when using target_filter without explicit targets or affected_by. "
            "Provide a reason (min 15 chars) explaining why a broad run is needed.",
        ),
        confirmation_token: str | None = Field(
            None,
            description="Token from initial blocked call. Required with confirm_broad_run.",
        ),
    ) -> dict[str, Any]:
        """Execute tests.

        RECOMMENDED: Use affected_by for efficient impact-aware testing:
        - run_test_targets(affected_by=["src/changed_file.py"])

        This automatically discovers and runs only tests affected by the changed files.

        Alternative workflows:
        - targets: Explicit target IDs (from discover_test_targets)
        - target_filter: Substring match (requires two-phase confirmation)

        To run a single test file, use: targets=['test:path/to/test_file.py']

        Coverage:
        When coverage=True, coverage_dir MUST be provided.
        """
        session = app_ctx.session_manager.get_or_create(ctx.session_id)

        # If affected_by provided, discover affected tests first
        effective_targets = targets
        impact_info: dict[str, Any] | None = None
        if affected_by:
            discover_result = await app_ctx.test_ops.discover(paths=None)
            all_targets = discover_result.targets or []

            # Use import graph to filter to affected targets
            graph_result = await app_ctx.coordinator.get_affected_test_targets(affected_by)
            affected_paths = set(graph_result.test_files)
            filtered = [
                t
                for t in all_targets
                if _target_matches_affected_files(t, affected_paths, app_ctx.repo_root)
            ]
            effective_targets = [t.target_id for t in filtered]

            impact_info = {
                "affected_by": affected_by,
                "targets_discovered": len(effective_targets),
                "confidence": graph_result.confidence.tier,
            }
            if not effective_targets:
                return {
                    "action": "run",
                    "run_status": {"status": "completed", "run_id": ""},
                    "impact": impact_info,
                    "summary": "no affected tests found",
                    "agentic_hint": (
                        "No tests import the changed files. Either the changes are untested, "
                        "or tests use dynamic imports not tracked by the index."
                    ),
                }

        # Two-phase confirmation for broad runs (target_filter without targets or affected_by)
        needs_confirmation = target_filter and not effective_targets and not affected_by
        if needs_confirmation:
            stored_token = session.fingerprints.get(_BROAD_RUN_TOKEN_KEY)

            # Check for partial confirmation (one param but not both)
            if bool(confirmation_token) != bool(confirm_broad_run):
                missing = "confirm_broad_run" if confirmation_token else "confirmation_token"
                return {
                    "action": "run",
                    "run_status": {"status": "blocked", "run_id": ""},
                    "error": "INCOMPLETE_CONFIRMATION",
                    "message": f"Both confirmation_token AND confirm_broad_run are required. Missing: {missing}",
                }

            # Phase 2: Validate token + reason and execute
            if confirmation_token and confirm_broad_run:
                if not stored_token:
                    return {
                        "action": "run",
                        "run_status": {"status": "blocked", "run_id": ""},
                        "error": "INVALID_CONFIRMATION",
                        "message": "No pending broad run confirmation. Call without token first.",
                    }
                if confirmation_token != stored_token:
                    return {
                        "action": "run",
                        "run_status": {"status": "blocked", "run_id": ""},
                        "error": "TOKEN_MISMATCH",
                        "message": "Confirmation token does not match. Request a new token.",
                    }
                if len(confirm_broad_run.strip()) < 15:
                    return {
                        "action": "run",
                        "run_status": {"status": "blocked", "run_id": ""},
                        "error": "REASON_TOO_SHORT",
                        "message": "confirm_broad_run must be at least 15 characters.",
                    }
                # Valid - clear token and proceed
                del session.fingerprints[_BROAD_RUN_TOKEN_KEY]

            # Phase 1: Generate token and block
            else:
                token = secrets.token_urlsafe(16)
                session.fingerprints[_BROAD_RUN_TOKEN_KEY] = token

                return {
                    "action": "run",
                    "run_status": {"status": "blocked", "run_id": ""},
                    "requires_confirmation": True,
                    "confirmation_token": token,
                    "target_filter": target_filter,
                    "agentic_hint": (
                        "BLOCKED: Using target_filter without impact analysis runs many tests. "
                        "Preferred: run_test_targets(affected_by=['changed_file.py']) for "
                        "automatic impact-aware selection. "
                        "If a broad run is truly needed, retry with BOTH: "
                        f"confirmation_token='{token}' AND confirm_broad_run='<reason min 15 chars>'."
                    ),
                    "summary": "BLOCKED: broad test run requires confirmation",
                }

        # Validate coverage_dir is provided when coverage is requested
        if coverage and not coverage_dir:
            return {
                "action": "run",
                "run_status": {"status": "failed", "run_id": ""},
                "agentic_hint": (
                    "coverage=True requires coverage_dir to be specified. "
                    "Use map_repo to understand your project structure and provide "
                    "the appropriate source directory path."
                ),
            }

        result = await app_ctx.test_ops.run(
            targets=effective_targets,
            target_filter=target_filter if not affected_by else None,
            test_filter=test_filter,
            tags=tags,
            failed_only=failed_only,
            parallelism=parallelism,
            timeout_sec=timeout_sec,
            fail_fast=fail_fast,
            coverage=coverage,
            coverage_dir=coverage_dir,
        )
        serialized = _serialize_test_result(result, is_action=True)
        if impact_info:
            serialized["impact"] = impact_info
        return serialized

    @mcp.tool
    async def get_test_run_status(
        ctx: Context,
        run_id: str = Field(..., description="ID of the test run to check"),
    ) -> dict[str, Any]:
        """Check progress of a running test. Returns pass/fail counts and any failures."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.status(run_id)
        return _serialize_test_result(result, is_action=False)

    @mcp.tool
    async def cancel_test_run(
        ctx: Context,
        run_id: str = Field(..., description="ID of the test run to cancel"),
    ) -> dict[str, Any]:
        """Abort a running test execution."""
        _ = app_ctx.session_manager.get_or_create(ctx.session_id)

        result = await app_ctx.test_ops.cancel(run_id)
        return _serialize_test_result(result, is_action=True)

    # Flatten schemas to remove $ref/$defs for Claude compatibility
    for tool in mcp._tool_manager._tools.values():
        tool.parameters = dereference_refs(tool.parameters)
