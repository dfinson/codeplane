"""Shared ignore/exclude pattern matching.

Single source of truth for path exclusion logic used by:
- FileWatcher (runtime file change filtering)
- ContextProbe (validation file sampling)
- ContextDiscovery (marker scanning with directory pruning)
- map_repo (filtering results)
- Any component needing .cplignore + .gitignore + UNIVERSAL_EXCLUDES support

NOTE: PRUNABLE_DIRS is now imported from codeplane.core.excludes.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

from codeplane.core.excludes import PRUNABLE_DIRS

__all__ = ["PRUNABLE_DIRS", "IgnoreChecker", "matches_glob"]


class IgnoreChecker:
    """Checks if paths should be ignored based on patterns.

    Loads patterns from .cplignore files anywhere in the repo (hierarchical,
    like .gitignore) and accepts additional patterns via constructor.

    Pattern syntax:
    - Standard glob patterns (fnmatch)
    - Directory patterns ending in / match contents
    - Negation with ! prefix

    .cplignore files themselves are NOT excluded - they need to be indexed
    so file watchers can detect changes and trigger reindexing.
    """

    # Filename for ignore files (like .gitignore but for CodePlane)
    CPLIGNORE_NAME = ".cplignore"

    def __init__(
        self,
        root: Path,
        extra_patterns: list[str] | None = None,
        *,
        respect_gitignore: bool = False,
    ) -> None:
        self._root = root
        self._patterns: list[str] = list(PRUNABLE_DIRS)
        self._cplignore_paths: list[Path] = []  # Track all loaded .cplignore files
        self._load_cplignore_recursive(root)
        if respect_gitignore:
            self._load_gitignore_recursive(root)
        if extra_patterns:
            self._patterns.extend(extra_patterns)

    @property
    def cplignore_paths(self) -> list[Path]:
        """Return list of all .cplignore files that were loaded.

        Used by Reconciler to track hashes and detect changes.
        """
        return self._cplignore_paths.copy()

    def compute_combined_hash(self) -> str | None:
        """Compute combined hash of all .cplignore file contents.

        Returns a hash that changes if ANY .cplignore file changes.
        Returns None if no .cplignore files exist.

        Used by Reconciler to detect .cplignore changes and trigger reindex.
        """
        import hashlib

        if not self._cplignore_paths:
            return None

        hasher = hashlib.sha256()
        # Sort paths for deterministic ordering
        for path in sorted(self._cplignore_paths):
            try:
                content = path.read_bytes()
                # Include path in hash so moving files is detected
                hasher.update(str(path).encode())
                hasher.update(content)
            except OSError:
                # File was deleted between loading and hashing
                hasher.update(str(path).encode())
                hasher.update(b"__DELETED__")
        return hasher.hexdigest()

    def _load_cplignore_recursive(self, root: Path) -> None:
        """Load .cplignore from root and all subdirectories.

        Handles nested .cplignore files by prefixing patterns with their
        relative directory path (same behavior as .gitignore).

        Also loads legacy .codeplane/.cplignore if it exists.
        """
        # Load legacy .codeplane/.cplignore first (highest priority)
        legacy_path = root / ".codeplane" / self.CPLIGNORE_NAME
        if legacy_path.exists():
            self._load_ignore_file(legacy_path)
            self._cplignore_paths.append(legacy_path)

        # Load root .cplignore (if not the same as legacy)
        root_cplignore = root / self.CPLIGNORE_NAME
        if root_cplignore.exists():
            self._load_ignore_file(root_cplignore)
            self._cplignore_paths.append(root_cplignore)

        # Walk for nested .cplignore files
        for dirpath, dirnames, filenames in root.walk():
            # Skip prunable dirs (but allow walking into .codeplane)
            dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS or d == ".codeplane"]

            # Skip root (already loaded) and .codeplane (legacy already loaded)
            if dirpath == root:
                continue
            if dirpath == root / ".codeplane":
                continue

            if self.CPLIGNORE_NAME in filenames:
                cplignore_path = dirpath / self.CPLIGNORE_NAME
                rel_dir = dirpath.relative_to(root)
                self._load_ignore_file(cplignore_path, prefix=str(rel_dir))
                self._cplignore_paths.append(cplignore_path)

    def _load_gitignore_recursive(self, root: Path) -> None:
        """Load .gitignore from root and all subdirectories.

        Handles nested .gitignore files by prefixing patterns with their
        relative directory path.
        """
        # Load root .gitignore
        root_gitignore = root / ".gitignore"
        if root_gitignore.exists():
            self._load_ignore_file(root_gitignore)

        # Walk for nested .gitignore files
        for dirpath, dirnames, filenames in root.walk():
            # Skip prunable dirs
            dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]

            if dirpath == root:
                continue  # Already loaded

            if ".gitignore" in filenames:
                gitignore_path = dirpath / ".gitignore"
                rel_dir = dirpath.relative_to(root)
                self._load_ignore_file(gitignore_path, prefix=str(rel_dir))

    def _load_ignore_file(self, path: Path, prefix: str = "") -> None:
        """Load patterns from an ignore file.

        Args:
            path: Path to the ignore file
            prefix: Directory prefix for nested .gitignore patterns
        """
        try:
            content = path.read_text()
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                # Handle negation
                is_negation = line.startswith("!")
                if is_negation:
                    line = line[1:]

                # Directory patterns (ending in /) match all contents
                pattern = f"{line}**" if line.endswith("/") else line

                # Apply prefix for nested .gitignore
                if prefix:
                    pattern = f"{prefix}/{pattern}"

                # Re-add negation prefix
                if is_negation:
                    pattern = f"!{pattern}"

                self._patterns.append(pattern)
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


def matches_glob(rel_path: str, pattern: str) -> bool:
    """Check if a path matches a glob pattern, with ** support."""
    if fnmatch.fnmatch(rel_path, pattern):
        return True
    # Handle **/pattern for any-depth matching
    if pattern.startswith("**/"):
        return fnmatch.fnmatch(rel_path, pattern[3:])
    return False
