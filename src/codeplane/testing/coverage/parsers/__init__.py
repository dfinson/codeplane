"""Coverage parser registry and auto-detection.

This module provides:
- PARSER_REGISTRY: All available parsers
- detect_parser: Auto-detect format from file/directory
- parse_artifact: Convenience function to parse with auto-detection
"""

from collections.abc import Sequence
from pathlib import Path

from codeplane.testing.coverage.models import CoverageParseError, CoverageReport

from .base import CoverageParser
from .clover import CloverParser
from .cobertura import CoberturaParser
from .gocov import GocovParser
from .istanbul import IstanbulParser
from .jacoco import JacocoParser
from .lcov import LcovParser
from .opencover import OpencoverParser
from .simplecov import SimplecovParser

# Parser registry - order matters for detection priority
# More specific formats first, generic ones last
PARSER_REGISTRY: Sequence[CoverageParser] = (
    GocovParser(),  # Go .out files (very specific format)
    SimplecovParser(),  # Ruby .resultset.json
    IstanbulParser(),  # JS coverage-final.json
    OpencoverParser(),  # .NET SequencePoint XML
    JacocoParser(),  # Java <report> with counters
    CloverParser(),  # PHP/Kotlin <coverage> XML
    CoberturaParser(),  # Generic <coverage> XML (last XML fallback)
    LcovParser(),  # LCOV text (last text fallback)
)

# Format ID to parser mapping
PARSER_BY_FORMAT: dict[str, CoverageParser] = {p.format_id: p for p in PARSER_REGISTRY}

__all__ = [
    "PARSER_REGISTRY",
    "PARSER_BY_FORMAT",
    "detect_parser",
    "parse_artifact",
    "CoverageParser",
    "CloverParser",
    "CoberturaParser",
    "GocovParser",
    "IstanbulParser",
    "JacocoParser",
    "LcovParser",
    "OpencoverParser",
    "SimplecovParser",
]


def detect_parser(path: Path) -> CoverageParser | None:
    """Auto-detect the appropriate parser for a coverage artifact.

    Args:
        path: Path to coverage file or directory.

    Returns:
        Parser instance if format detected, None otherwise.

    Detection strategy:
    1. Try each parser in registry order (most specific first)
    2. Return first parser that claims it can_parse the path
    """
    for parser in PARSER_REGISTRY:
        if parser.can_parse(path):
            return parser
    return None


def parse_artifact(
    path: Path,
    *,
    format_id: str | None = None,
    base_path: Path | None = None,
) -> CoverageReport:
    """Parse a coverage artifact into a unified CoverageReport.

    Args:
        path: Path to coverage file or directory.
        format_id: Force specific format (skip auto-detection).
        base_path: Base path for normalizing file paths within report.

    Returns:
        Parsed CoverageReport.

    Raises:
        CoverageParseError: If format unknown or parsing fails.
    """
    if format_id:
        parser = PARSER_BY_FORMAT.get(format_id)
        if not parser:
            valid = ", ".join(sorted(PARSER_BY_FORMAT.keys()))
            raise CoverageParseError(
                f"Unknown coverage format: {format_id!r}. Valid formats: {valid}"
            )
    else:
        parser = detect_parser(path)
        if not parser:
            raise CoverageParseError(
                f"Could not detect coverage format for: {path}. "
                "Supported formats: lcov, cobertura, opencover, istanbul, "
                "jacoco, gocov, clover, simplecov"
            )

    return parser.parse(path, base_path=base_path)
