"""Extension-based language detection for fallback context.

Maps file extensions to LanguageFamily for files not claimed by
marker-based contexts. Used by the root fallback context (tier 3).

NOTE: This module re-exports from core.languages for backward compatibility.
New code should import directly from codeplane.core.languages.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.core.languages import (
    EXTENSION_TO_NAME as _EXT_MAP,
)
from codeplane.core.languages import (
    FILENAME_TO_NAME as _NAME_MAP,
)
from codeplane.core.languages import (
    detect_language_family as _detect,
)
from codeplane.core.languages import (
    get_all_indexable_extensions,
    get_all_indexable_filenames,
)
from codeplane.index.models import LanguageFamily

if TYPE_CHECKING:
    pass


def _build_enum_extension_map() -> dict[str, LanguageFamily]:
    """Build extension map with LanguageFamily enum values."""
    result: dict[str, LanguageFamily] = {}
    for ext, family_str in _EXT_MAP.items():
        with contextlib.suppress(ValueError):
            result[ext] = LanguageFamily(family_str)
    return result


def _build_enum_filename_map() -> dict[str, LanguageFamily]:
    """Build filename map with LanguageFamily enum values."""
    result: dict[str, LanguageFamily] = {}
    for name, family_str in _NAME_MAP.items():
        with contextlib.suppress(ValueError):
            result[name] = LanguageFamily(family_str)
    return result


# Backward-compatible exports with enum values
EXTENSION_TO_NAME: dict[str, LanguageFamily] = _build_enum_extension_map()
FILENAME_TO_NAME: dict[str, LanguageFamily] = _build_enum_filename_map()

# Backward compatibility aliases (deprecated)
EXTENSION_TO_FAMILY = EXTENSION_TO_NAME
FILENAME_TO_FAMILY = FILENAME_TO_NAME


def detect_language_family(path: str | Path) -> LanguageFamily | None:
    """Detect language family from file path.

    Uses extension-based detection with special handling for certain filenames.

    Args:
        path: File path (relative or absolute)

    Returns:
        LanguageFamily if detected, None otherwise
    """
    family_str = _detect(path)
    if family_str is None:
        return None
    try:
        return LanguageFamily(family_str)
    except ValueError:
        return None


__all__ = [
    "EXTENSION_TO_NAME",
    "FILENAME_TO_NAME",
    "EXTENSION_TO_FAMILY",  # Deprecated alias
    "FILENAME_TO_FAMILY",  # Deprecated alias
    "detect_language_family",
    "get_all_indexable_extensions",
    "get_all_indexable_filenames",
]
