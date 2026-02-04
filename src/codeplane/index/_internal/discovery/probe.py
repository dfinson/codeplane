"""Context probe validation via file sampling.

This module implements SPEC.md §8.4 context validation. The probe
verifies that candidate contexts have the expected structure by
sampling files and checking parsability/coherence.

Probe rules:
1. Sample up to N files matching include_spec
2. Require M files parse successfully
3. Mark context as VALID or INVALID
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from codeplane.index._internal.ignore import IgnoreChecker
from codeplane.index._internal.parsing import TreeSitterParser
from codeplane.index.models import CandidateContext, LanguageFamily


@dataclass
class ProbeResult:
    """Result of context validation."""

    context: CandidateContext
    valid: bool
    files_sampled: int = 0
    files_passed: int = 0
    reason: str = ""


@dataclass
class ProbeConfig:
    """Configuration for context probe."""

    # Maximum files to sample per context
    max_sample: int = 10
    # Minimum files that must parse successfully
    min_success: int = 1
    # Minimum success ratio (if more than min_success files)
    min_ratio: float = 0.5


class ContextProbe:
    """
    Validates context candidates via file sampling.

    The probe checks that a context:
    1. Has files matching its include_spec
    2. Those files parse successfully with tree-sitter

    Usage::

        probe = ContextProbe(repo_path)
        for ctx in candidates:
            result = probe.validate(ctx)
            if result.valid:
                print(f"{ctx.root_path} is valid")
    """

    def __init__(
        self,
        repo_path: Path | str,
        config: ProbeConfig | None = None,
        parser: TreeSitterParser | None = None,
    ):
        self.repo_path = Path(repo_path)
        self.config = config or ProbeConfig()
        self.parser = parser or TreeSitterParser()
        self._family_to_ext = self._build_extension_map()
        # Shared ignore checker - loads .cplignore automatically
        self._ignore_checker = IgnoreChecker(self.repo_path)

    def _build_extension_map(self) -> dict[LanguageFamily, set[str]]:
        """Map language families to file extensions."""
        return {
            LanguageFamily.PYTHON: {".py", ".pyi"},
            LanguageFamily.JAVASCRIPT: {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
            LanguageFamily.GO: {".go"},
            LanguageFamily.RUST: {".rs"},
            # JVM languages
            LanguageFamily.JAVA: {".java"},
            LanguageFamily.KOTLIN: {".kt", ".kts"},
            LanguageFamily.SCALA: {".scala", ".sc"},
            LanguageFamily.GROOVY: {".groovy", ".gradle"},
            # .NET languages
            LanguageFamily.CSHARP: {".cs"},
            LanguageFamily.FSHARP: {".fs", ".fsx", ".fsi"},
            LanguageFamily.VBNET: {".vb"},
            LanguageFamily.C_CPP: {".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hxx"},
            LanguageFamily.OBJC: {".m", ".mm"},
            LanguageFamily.MATLAB: {".m", ".mlx"},
            LanguageFamily.SWIFT: {".swift"},
            LanguageFamily.PHP: {".php"},
            LanguageFamily.RUBY: {".rb"},
            LanguageFamily.ELIXIR: {".ex", ".exs"},
            LanguageFamily.HASKELL: {".hs", ".lhs"},
            LanguageFamily.SQL: {".sql"},
            LanguageFamily.TERRAFORM: {".tf", ".tfvars"},
            LanguageFamily.DOCKER: {"dockerfile"},
            LanguageFamily.MARKDOWN: {".md", ".mdx"},
            LanguageFamily.JSON: {".json", ".jsonc"},
            LanguageFamily.YAML: {".yaml", ".yml"},
            LanguageFamily.TOML: {".toml"},
            LanguageFamily.PROTOBUF: {".proto"},
            LanguageFamily.GRAPHQL: {".graphql", ".gql"},
        }

    def validate(self, context: CandidateContext) -> ProbeResult:
        """Validate a single context candidate."""
        extensions = self._family_to_ext.get(context.language_family, set())
        if not extensions:
            return ProbeResult(
                context=context,
                valid=False,
                reason=f"Unknown family: {context.language_family}",
            )

        # Compute context root
        ctx_root = self.repo_path / context.root_path if context.root_path else self.repo_path

        if not ctx_root.exists():
            return ProbeResult(
                context=context,
                valid=False,
                reason=f"Root path does not exist: {context.root_path}",
            )

        # Sample files
        sampled = self._sample_files(ctx_root, extensions, context.exclude_spec or [])

        if not sampled:
            return ProbeResult(
                context=context,
                valid=False,
                files_sampled=0,
                reason="No matching files found",
            )

        # Validate files
        passed = 0
        for file_path in sampled:
            if self._validate_file(file_path, context.language_family):
                passed += 1

        # Check thresholds
        valid = False
        reason = ""
        if passed >= self.config.min_success:
            if (
                len(sampled) <= self.config.min_success
                or passed / len(sampled) >= self.config.min_ratio
            ):
                valid = True
            else:
                reason = f"Low parse ratio: {passed}/{len(sampled)}"
        else:
            reason = f"Insufficient parses: {passed}/{self.config.min_success}"

        return ProbeResult(
            context=context,
            valid=valid,
            files_sampled=len(sampled),
            files_passed=passed,
            reason=reason,
        )

    def _sample_files(self, root: Path, extensions: set[str], excludes: list[str]) -> list[Path]:
        """Sample files matching extensions."""
        from codeplane.index._internal.ignore import PRUNABLE_DIRS

        files: list[Path] = []

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune excluded directories in-place for performance
            dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]

            dir_path = Path(dirpath)

            # Skip excluded directories (via shared IgnoreChecker + context excludes)
            if self._ignore_checker.should_ignore(dir_path):
                dirnames.clear()
                continue

            # Also check context-specific excludes
            rel_dir = dir_path.relative_to(root)
            if self._is_excluded_by_context(str(rel_dir), excludes):
                dirnames.clear()
                continue

            for name in filenames:
                if len(files) >= self.config.max_sample:
                    return files

                if Path(name).suffix in extensions:
                    file_path = dir_path / name
                    # Check both shared ignore and context excludes
                    if self._ignore_checker.should_ignore(file_path):
                        continue
                    rel_file = file_path.relative_to(root)
                    if not self._is_excluded_by_context(str(rel_file), excludes):
                        files.append(file_path)

        return files

    def _is_excluded_by_context(self, path: str, excludes: list[str]) -> bool:
        """Check if path matches context-specific exclude patterns."""
        for pattern in excludes:
            # Simple glob matching for context excludes
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if path == prefix or path.startswith(prefix + "/"):
                    return True
            elif pattern.endswith("/*"):
                prefix = pattern[:-2]
                if path.startswith(prefix + "/") and "/" not in path[len(prefix) + 1 :]:
                    return True
            elif path == pattern:
                return True
        return False

    def _validate_file(self, file_path: Path, family: LanguageFamily) -> bool:
        """Validate a single file."""
        # Only validate families we have parsers for
        supported = {
            LanguageFamily.PYTHON,
            LanguageFamily.JAVASCRIPT,
            LanguageFamily.GO,
            LanguageFamily.RUST,
        }

        if family not in supported:
            # For unsupported families, just check file is readable
            try:
                with open(file_path, "rb") as f:
                    content = f.read(8192)
                    try:
                        content.decode("utf-8")
                        return True
                    except UnicodeDecodeError:
                        return False
            except OSError:
                return False

        # Parse file and validate
        parse_result = self.parser.parse(file_path)
        if parse_result is None:
            return False
        validation = self.parser.validate_code_file(parse_result)
        return validation.is_valid


@dataclass
class BatchProbeResult:
    """Result of batch context validation."""

    valid: list[CandidateContext] = field(default_factory=list)
    invalid: list[ProbeResult] = field(default_factory=list)


def validate_contexts(
    repo_path: Path | str,
    candidates: list[CandidateContext],
    config: ProbeConfig | None = None,
) -> BatchProbeResult:
    """
    Validate all context candidates.

    Args:
        repo_path: Path to repository root
        candidates: List of candidate contexts
        config: Optional probe configuration

    Returns:
        BatchProbeResult with valid and invalid contexts.
    """
    probe = ContextProbe(repo_path, config)
    result = BatchProbeResult()

    for ctx in candidates:
        probe_result = probe.validate(ctx)
        if probe_result.valid:
            result.valid.append(ctx)
        else:
            result.invalid.append(probe_result)

    return result
