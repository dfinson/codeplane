# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.
"""Preprocessors for normalizing framework-specific event structures.

Each preprocessor transforms raw dicts into flat dicts suitable for
type_field-based YAML mapping. They handle compound discriminators,
nested structures, and field-presence-based typing.
"""

import backend.compat.traceformats.preprocessors.amazonq  # noqa: F401
import backend.compat.traceformats.preprocessors.antigravity  # noqa: F401
import backend.compat.traceformats.preprocessors.claude  # noqa: F401

# Import all preprocessor modules to trigger registration
import backend.compat.traceformats.preprocessors.cline  # noqa: F401
import backend.compat.traceformats.preprocessors.codex  # noqa: F401
import backend.compat.traceformats.preprocessors.continue_dev  # noqa: F401
import backend.compat.traceformats.preprocessors.copilot  # noqa: F401
import backend.compat.traceformats.preprocessors.copilot_vscode  # noqa: F401
import backend.compat.traceformats.preprocessors.goose  # noqa: F401
import backend.compat.traceformats.preprocessors.maf_transcript  # noqa: F401
import backend.compat.traceformats.preprocessors.openai_agents  # noqa: F401
import backend.compat.traceformats.preprocessors.opencode  # noqa: F401
import backend.compat.traceformats.preprocessors.openhands  # noqa: F401
import backend.compat.traceformats.preprocessors.pydantic_ai  # noqa: F401
import backend.compat.traceformats.preprocessors.smolagents  # noqa: F401
from backend.compat.traceformats.preprocessors.registry import (
    PreprocessorFn,
    get_preprocessor,
    register_preprocessor,
)

__all__ = [
    "PreprocessorFn",
    "get_preprocessor",
    "register_preprocessor",
]
