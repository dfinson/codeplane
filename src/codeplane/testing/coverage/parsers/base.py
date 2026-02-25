"""Coverage parser protocol."""

from pathlib import Path
from typing import Protocol

from codeplane.testing.coverage.models import CoverageReport


class CoverageParser(Protocol):
    """Protocol for coverage format parsers.

    Each parser handles one coverage format and converts it to the
    unified CoverageReport model.
    """

    @property
    def format_id(self) -> str:
        """Format identifier (e.g., 'lcov', 'cobertura')."""
        ...

    def can_parse(self, path: Path) -> bool:
        """Check if this parser can handle the given file.

        Uses extension and content sniffing for auto-detection.
        """
        ...

    def parse(self, path: Path, *, base_path: Path | None = None) -> CoverageReport:
        """Parse coverage file into unified model.

        Args:
            path: Path to coverage file or directory.
            base_path: Workspace root for resolving relative paths.
                      If None, paths in coverage data are used as-is.

        Returns:
            CoverageReport with per-file, per-line coverage data.

        Raises:
            CoverageParseError: If parsing fails.
        """
        ...
