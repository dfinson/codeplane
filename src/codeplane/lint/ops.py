"""Lint operations - check and fix."""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from codeplane.lint.models import LintResult, ToolCategory, ToolResult
from codeplane.lint.tools import LintTool, registry

if TYPE_CHECKING:
    from codeplane.index.ops import IndexCoordinator


# Language name to tool language mapping
_LANGUAGE_TO_TOOL_PREFIX: dict[str, str] = {
    "python": "python",
    "javascript": "js",
    "go": "go",
    "rust": "rust",
    "jvm": "java",  # covers Java/Kotlin
    "ruby": "ruby",
    "php": "php",
    "shell": "shell",
    "docker": "docker",
    "json_yaml": "yaml",
    "markdown": "markdown",
    "sql": "sql",
}


def _generate_agentic_hint(languages: list[str]) -> str:
    """Generate agentic hint for unhandled case based on detected languages."""
    hints: list[str] = []

    lang_set = set(languages)

    if "python" in lang_set:
        hints.append("Python: Run `ruff check --fix .` or `black .` for formatting")
    if "javascript" in lang_set or "typescript" in lang_set:
        hints.append("JavaScript/TypeScript: Run `eslint --fix .` or `prettier --write .`")
    if "go" in lang_set:
        hints.append("Go: Run `go fmt ./...` and `go vet ./...`")
    if "rust" in lang_set:
        hints.append("Rust: Run `cargo fmt` and `cargo clippy --fix`")
    if "ruby" in lang_set:
        hints.append("Ruby: Run `rubocop -A .`")
    if "php" in lang_set:
        hints.append("PHP: Run `phpcs` or `php-cs-fixer fix .`")

    if not hints:
        hints.append(
            "Install and run appropriate linters for your project. "
            "Common options: eslint, prettier, ruff, black, gofmt, rustfmt, rubocop"
        )

    return "\n".join(hints)


class LintOps:
    """Lint operations for a repository.

    Uses the index for file discovery and statistics. Falls back to agentic hints
    when no tools are detected or configured.
    """

    def __init__(self, repo_root: Path, coordinator: IndexCoordinator) -> None:
        self._repo_root = repo_root
        self._coordinator = coordinator

    async def check(
        self,
        *,
        paths: list[str] | None = None,
        tools: list[str] | None = None,
        categories: list[str] | None = None,
        dry_run: bool = False,
    ) -> LintResult:
        """Run lint/format/type-check tools.

        By default, applies fixes. Use dry_run=True to preview changes.

        Args:
            paths: Paths to check (default: entire repo)
            tools: Specific tool IDs to run (default: auto-detect)
            categories: Filter by category (type_check, lint, format, security)
            dry_run: If True, show what would change without modifying files

        Returns:
            LintResult with diagnostics from all tools
        """
        start_time = time.time()
        action: Literal["check", "fix"] = "check" if dry_run else "fix"

        # Validate tools if specified
        invalid_tools: list[str] = []
        if tools:
            for tid in tools:
                if not registry.get(tid):
                    invalid_tools.append(tid)
            if invalid_tools:
                return LintResult(
                    action=action,
                    dry_run=dry_run,
                    tools_run=[],
                    duration_seconds=time.time() - start_time,
                    agentic_hint=f"Unknown tool(s): {', '.join(invalid_tools)}. "
                    f"Use lint_tools to see available tools.",
                )

        # Validate categories if specified
        valid_categories = {e.value for e in ToolCategory}
        if categories:
            invalid_cats = [c for c in categories if c not in valid_categories]
            if invalid_cats:
                return LintResult(
                    action=action,
                    dry_run=dry_run,
                    tools_run=[],
                    duration_seconds=time.time() - start_time,
                    agentic_hint=f"Unknown category(s): {', '.join(invalid_cats)}. "
                    f"Valid categories: {', '.join(sorted(valid_categories))}",
                )

        # Resolve which tools to run
        tools_to_run = await self._resolve_tools(tools, categories)

        # If no tools detected, provide agentic fallback
        if not tools_to_run:
            detected_languages = await self._get_detected_languages()
            no_tools_hint = _generate_agentic_hint(detected_languages)

            return LintResult(
                action=action,
                dry_run=dry_run,
                tools_run=[],
                duration_seconds=time.time() - start_time,
                agentic_hint=no_tools_hint,
            )

        # Resolve paths
        resolved_paths = self._resolve_paths(paths)

        # Run tools concurrently
        tasks = [self._run_tool(tool, resolved_paths, dry_run) for tool in tools_to_run]
        results = await asyncio.gather(*tasks)

        # Check for any tools that errored - provide agentic hint
        errored_tools = [r for r in results if r.status == "error"]
        agentic_hint: str | None = None
        if errored_tools:
            detected_languages = await self._get_detected_languages()
            error_details = "; ".join(
                f"{r.tool_id}: {r.error_detail}" for r in errored_tools if r.error_detail
            )
            agentic_hint = (
                f"Some tools failed: {error_details}\n\n"
                f"Manual fallback:\n{_generate_agentic_hint(detected_languages)}"
            )

        return LintResult(
            action=action,
            dry_run=dry_run,
            tools_run=list(results),
            duration_seconds=time.time() - start_time,
            agentic_hint=agentic_hint,
        )

    async def _get_detected_languages(self) -> list[str]:
        """Get detected languages from coordinator.

        Raises RuntimeError if coordinator is not initialized.
        Callers should handle this by providing explicit language list or deferring.
        """
        try:
            file_stats = await self._coordinator.get_file_stats()
            return list(file_stats.keys())
        except RuntimeError:
            # Coordinator not initialized - re-raise rather than silently defaulting
            # Callers should explicitly handle uninitialized state
            raise RuntimeError(
                "Coordinator not initialized. "
                "Provide explicit tool_ids or categories, or ensure coordinator is ready."
            ) from None

    async def _resolve_tools(
        self, tool_ids: list[str] | None, categories: list[str] | None
    ) -> list[LintTool]:
        """Resolve which tools to run.

        Queries the index first for pre-discovered tools, falls back to
        runtime detection if index is empty or not initialized.
        """
        if tool_ids:
            # Specific tools requested
            tools = []
            for tid in tool_ids:
                tool = registry.get(tid)
                if tool:
                    tools.append(tool)
            return tools

        # Try to get tools from index first
        try:
            indexed_tools = await self._coordinator.get_lint_tools(
                category=categories[0] if categories and len(categories) == 1 else None
            )
            if indexed_tools:
                # Convert indexed tools back to LintTool objects
                detected: list[LintTool] = []
                for indexed in indexed_tools:
                    tool = registry.get(indexed.tool_id)
                    if tool:
                        detected.append(tool)

                # Filter by category if multiple specified
                if categories and len(categories) > 1:
                    category_set = {
                        ToolCategory(c) for c in categories if c in [e.value for e in ToolCategory]
                    }
                    detected = [t for t in detected if t.category in category_set]

                return detected
        except (RuntimeError, AttributeError):
            # Coordinator not initialized or doesn't have get_lint_tools
            pass

        # Fallback: runtime detection
        detected_pairs = registry.detect(self._repo_root)
        detected = [t for t, _ in detected_pairs]

        # Filter by category if specified
        if categories:
            category_set = {
                ToolCategory(c) for c in categories if c in [e.value for e in ToolCategory]
            }
            detected = [t for t in detected if t.category in category_set]

        return detected

    def _resolve_paths(self, paths: list[str] | None) -> list[Path]:
        """Resolve paths to check."""
        if not paths:
            return [self._repo_root]
        return [self._repo_root / p for p in paths]

    async def _run_tool(
        self,
        tool: LintTool,
        paths: list[Path],
        dry_run: bool,
    ) -> ToolResult:
        """Run a single lint tool."""
        start_time = time.time()

        # Check if executable exists
        if not shutil.which(tool.executable):
            return ToolResult(
                tool_id=tool.tool_id,
                status="skipped",
                error_detail=f"Executable not found: {tool.executable}",
                duration_seconds=time.time() - start_time,
            )

        # Build command
        cmd = self._build_command(tool, paths, dry_run)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._repo_root,
            )
            stdout_bytes, stderr_bytes = await proc.communicate()
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            # Parse output
            parse_result = tool.parse_output(stdout, stderr)
            diagnostics = parse_result.diagnostics

            # Determine status
            status: Literal["clean", "dirty", "error", "skipped"]
            if not parse_result.success:
                # Parser error - treat as tool error
                status = "error"
            elif proc.returncode == 0 and not diagnostics:
                status = "clean"
            elif diagnostics:
                status = "dirty"
            else:
                # Non-zero exit with no diagnostics might be an error
                # or might just mean "issues found" for some tools
                status = "dirty" if proc.returncode in (1, 2) else "error"

            # Get file count from index instead of globbing
            files_checked = len({d.path for d in diagnostics})
            if not files_checked:
                files_checked = await self._get_file_count_from_index(tool, paths)
            files_modified = sum(1 for d in diagnostics if d.fix_applied)

            # Build error detail
            error_detail: str | None = None
            if status == "error":
                if parse_result.parse_error:
                    error_detail = f"Parse error: {parse_result.parse_error}"
                elif stderr:
                    error_detail = stderr

            return ToolResult(
                tool_id=tool.tool_id,
                status=status,
                diagnostics=diagnostics,
                files_checked=files_checked,
                files_modified=files_modified,
                duration_seconds=time.time() - start_time,
                command=cmd,
                error_detail=error_detail,
            )

        except OSError as e:
            return ToolResult(
                tool_id=tool.tool_id,
                status="error",
                error_detail=str(e),
                duration_seconds=time.time() - start_time,
                command=cmd,
            )

    async def _get_file_count_from_index(self, tool: LintTool, paths: list[Path]) -> int:
        """Get file count from index based on tool's language.

        Falls back to 0 if coordinator is not initialized.
        """
        try:
            # Map tool language to index language name
            tool_lang = tool.tool_id.split(".")[0]  # e.g., "python" from "python.ruff"

            # Reverse lookup from tool prefix to language name
            lang_family = None
            for name, prefix in _LANGUAGE_TO_TOOL_PREFIX.items():
                if prefix == tool_lang:
                    lang_family = name
                    break

            if not lang_family:
                # Fallback: count all indexed files
                return await self._coordinator.get_indexed_file_count()

            # Get count for specific language
            # If paths specified, we'd need to filter - for now just get language count
            if len(paths) == 1 and paths[0] == self._repo_root:
                return await self._coordinator.get_indexed_file_count(lang_family)

            # For specific paths, get files and count those matching
            indexed_files = await self._coordinator.get_indexed_files(lang_family, limit=10000)
            count = 0
            for f in indexed_files:
                for p in paths:
                    rel_path = p.relative_to(self._repo_root) if p.is_absolute() else p
                    if f.startswith(str(rel_path)):
                        count += 1
                        break
            return count
        except RuntimeError:
            # Coordinator not initialized - return 0 as fallback
            return 0

    def _build_command(
        self,
        tool: LintTool,
        paths: list[Path],
        dry_run: bool,
    ) -> list[str]:
        """Build command for a tool."""
        cmd = [tool.executable]

        # Add args based on mode
        if dry_run:
            cmd.extend(tool.dry_run_args or tool.check_args)
        else:
            cmd.extend(tool.fix_args or tool.check_args)

        # Add paths
        if tool.paths_position == "end" and paths:
            path_strs = [str(p) for p in paths]
            if tool.paths_separator:
                cmd.append(tool.paths_separator.join(path_strs))
            else:
                cmd.extend(path_strs)

        return cmd
