"""Shared ignore/exclude pattern matching.

Single source of truth for path exclusion logic used by:
- FileWatcher (runtime file change filtering)
- ContextProbe (validation file sampling)
- ContextDiscovery (marker scanning with directory pruning)
- Any component needing .cplignore + UNIVERSAL_EXCLUDES support
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

# Directory names to prune during os.walk traversal (for performance).
# These are checked by exact name match, not glob patterns.
PRUNABLE_DIRS: frozenset[str] = frozenset(
    {
        # Version control
        ".git",
        ".svn",
        ".hg",
        # CodePlane
        ".codeplane",
        # JavaScript/Node
        "node_modules",
        ".npm",
        ".yarn",
        ".pnpm-store",
        # Python
        "venv",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        "eggs",
        ".eggs",
        "site-packages",
        ".ipynb_checkpoints",
        # Go
        "vendor",
        "pkg",
        # Rust
        "target",
        # JVM (Java/Kotlin/Scala)
        ".gradle",
        ".m2",
        "out",
        # .NET
        "bin",
        "obj",
        "packages",
        # Terraform
        ".terraform",
        # Ruby
        ".bundle",
        # PHP
        # (uses vendor, already listed under Go)
        # Build outputs (general)
        "dist",
        "build",
        "_build",
        # Coverage/testing
        "coverage",
        ".coverage",
        ".nyc_output",
        "htmlcov",
        # IDE/editor
        ".idea",
        ".vscode",
        # Caches
        ".cache",
        "tmp",
        "temp",
    }
)


class IgnoreChecker:
    """Checks if paths should be ignored based on patterns.

    Loads patterns from .cplignore and accepts additional patterns
    (e.g., UNIVERSAL_EXCLUDES) via constructor.

    Pattern syntax:
    - Standard glob patterns (fnmatch)
    - Directory patterns ending in / match contents
    - Negation with ! prefix
    """

    def __init__(
        self,
        root: Path,
        extra_patterns: list[str] | None = None,
    ) -> None:
        self._root = root
        self._patterns: list[str] = list(PRUNABLE_DIRS)
        self._load_cplignore(root / ".codeplane" / ".cplignore")
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    def _load_cplignore(self, cplignore_path: Path) -> None:
        if not cplignore_path.exists():
            return

        try:
            content = cplignore_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Directory patterns (ending in /) match all contents
                if line.endswith("/"):
                    self._patterns.append(f"{line}**")
                else:
                    self._patterns.append(line)
        except OSError:
            pass

    def should_ignore(self, path: Path) -> bool:
        try:
            rel_path = path.relative_to(self._root)
        except ValueError:
            return True

        rel_str = str(rel_path)

        for pattern in self._patterns:
            if pattern.startswith("!"):
                if fnmatch.fnmatch(rel_str, pattern[1:]):
                    return False
                continue

            if fnmatch.fnmatch(rel_str, pattern):
                return True

            for parent in rel_path.parents:
                if fnmatch.fnmatch(str(parent), pattern):
                    return True

        return False

    def is_excluded_rel(self, rel_path: str) -> bool:
        path_obj = Path(rel_path)

        for pattern in self._patterns:
            if pattern.startswith("!"):
                if fnmatch.fnmatch(rel_path, pattern[1:]):
                    return False
                continue

            if fnmatch.fnmatch(rel_path, pattern):
                return True

            for parent in path_obj.parents:
                if parent != Path(".") and fnmatch.fnmatch(str(parent), pattern):
                    return True

        return False
