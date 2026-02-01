"""Shared ignore/exclude pattern matching.

Single source of truth for path exclusion logic used by:
- FileWatcher (runtime file change filtering)
- ContextProbe (validation file sampling)
- Any component needing .cplignore + UNIVERSAL_EXCLUDES support
"""

from __future__ import annotations

import fnmatch
from pathlib import Path


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
        self._patterns: list[str] = []

        # Load .cplignore patterns (created by cpl init)
        self._load_cplignore(root / ".codeplane" / ".cplignore")

        # Add extra patterns (e.g., UNIVERSAL_EXCLUDES)
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    def _load_cplignore(self, cplignore_path: Path) -> None:
        """Load patterns from a .cplignore file."""
        if not cplignore_path.exists():
            return

        try:
            content = cplignore_path.read_text()
            for line in content.splitlines():
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Normalize pattern
                if line.endswith("/"):
                    # Directory pattern - match contents
                    self._patterns.append(f"{line}**")
                else:
                    self._patterns.append(line)
        except OSError:
            pass

    def should_ignore(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        try:
            rel_path = path.relative_to(self._root)
        except ValueError:
            return True

        rel_str = str(rel_path)

        for pattern in self._patterns:
            # Handle negation patterns
            if pattern.startswith("!"):
                if fnmatch.fnmatch(rel_str, pattern[1:]):
                    return False
                continue

            # Standard matching
            if fnmatch.fnmatch(rel_str, pattern):
                return True

            # Also match against any parent directory
            for parent in rel_path.parents:
                if fnmatch.fnmatch(str(parent), pattern):
                    return True

        return False

    def is_excluded_rel(self, rel_path: str) -> bool:
        """Check if a relative path string should be ignored."""
        path_obj = Path(rel_path)

        for pattern in self._patterns:
            # Handle negation patterns
            if pattern.startswith("!"):
                if fnmatch.fnmatch(rel_path, pattern[1:]):
                    return False
                continue

            # Standard matching
            if fnmatch.fnmatch(rel_path, pattern):
                return True

            # Also match against any parent directory
            for parent in path_obj.parents:
                if parent != Path(".") and fnmatch.fnmatch(str(parent), pattern):
                    return True

        return False
