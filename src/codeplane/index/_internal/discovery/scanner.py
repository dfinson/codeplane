"""Context discovery for automatic project boundary detection.

Implements Phase A of SPEC.md §8.4: Discovery (Candidate Generation).
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from codeplane.core.excludes import PRUNABLE_DIRS, UNIVERSAL_EXCLUDE_GLOBS
from codeplane.core.languages import (
    AMBIENT_NAMES as _AMBIENT_NAMES_STR,
)
from codeplane.core.languages import (
    build_include_specs,
    build_marker_definitions,
)
from codeplane.index.models import (
    CandidateContext,
    LanguageFamily,
    MarkerTier,
    ProbeStatus,
)

# Build from canonical registry
_MARKER_DEFS = build_marker_definitions()
_INCLUDE_SPECS = build_include_specs()
UNIVERSAL_EXCLUDES: list[str] = list(UNIVERSAL_EXCLUDE_GLOBS)

# Convert to LanguageFamily-keyed dicts for runtime use
MARKER_DEFINITIONS: dict[LanguageFamily, dict[MarkerTier, list[str]]] = {}
for family_str, tiers in _MARKER_DEFS.items():
    with contextlib.suppress(ValueError):
        family = LanguageFamily(family_str)
        MARKER_DEFINITIONS[family] = {
            MarkerTier.WORKSPACE: list(tiers["workspace"]),
            MarkerTier.PACKAGE: list(tiers["package"]),
        }

INCLUDE_SPECS: dict[LanguageFamily, list[str]] = {}
for family_str, globs in _INCLUDE_SPECS.items():
    with contextlib.suppress(ValueError):
        INCLUDE_SPECS[LanguageFamily(family_str)] = list(globs)

# Convert to LanguageFamily enum for runtime use
AMBIENT_NAMES: frozenset[LanguageFamily] = frozenset(
    LanguageFamily(f) for f in _AMBIENT_NAMES_STR if f in [e.value for e in LanguageFamily]
)


def _walk_with_pruning(root: Path) -> list[tuple[str, str]]:
    """Walk all files, pruning PRUNABLE_DIRS. Returns (rel_dir_posix, filename)."""
    results: list[tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PRUNABLE_DIRS]
        rel_dir_path = Path(dirpath).relative_to(root)
        rel_dir_posix = str(rel_dir_path).replace("\\", "/")
        if rel_dir_posix == ".":
            rel_dir_posix = ""
        for filename in filenames:
            results.append((rel_dir_posix, filename))
    return results


@dataclass
class DiscoveredMarker:
    path: str
    family: LanguageFamily
    tier: MarkerTier


@dataclass
class DiscoveryResult:
    candidates: list[CandidateContext] = field(default_factory=list)
    markers: list[DiscoveredMarker] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ContextDiscovery:
    """Discovers project contexts by scanning for marker files."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root

    def discover_all(self) -> DiscoveryResult:
        result = DiscoveryResult()
        markers = self._scan_markers()
        result.markers = markers

        candidates_by_family: dict[LanguageFamily, list[CandidateContext]] = {}

        for marker in markers:
            if marker.family not in candidates_by_family:
                candidates_by_family[marker.family] = []

            marker_dir = str(Path(marker.path).parent)
            if marker_dir == ".":
                marker_dir = ""

            existing = next(
                (c for c in candidates_by_family[marker.family] if c.root_path == marker_dir),
                None,
            )

            if existing:
                existing.markers.append(marker.path)
                if marker.tier == MarkerTier.WORKSPACE and existing.tier != 1:
                    existing.tier = 1
            else:
                candidate = CandidateContext(
                    language_family=marker.family,
                    root_path=marker_dir,
                    tier=1 if marker.tier == MarkerTier.WORKSPACE else 2,
                    markers=[marker.path],
                    include_spec=INCLUDE_SPECS.get(marker.family, []),
                    exclude_spec=UNIVERSAL_EXCLUDES,
                    probe_status=ProbeStatus.PENDING,
                )
                candidates_by_family[marker.family].append(candidate)

        # Add ambient contexts
        for family in AMBIENT_NAMES:
            if family not in candidates_by_family:
                candidates_by_family[family] = [
                    CandidateContext(
                        language_family=family,
                        root_path="",
                        tier=None,
                        markers=[],
                        include_spec=INCLUDE_SPECS.get(family, []),
                        exclude_spec=UNIVERSAL_EXCLUDES,
                        probe_status=ProbeStatus.PENDING,
                    )
                ]

        # Root fallback (tier 3) - catch-all for unclaimed files
        result.candidates.append(
            CandidateContext(
                language_family=LanguageFamily.UNKNOWN,
                root_path="",
                tier=3,
                markers=[],
                include_spec=["**/*"],
                exclude_spec=UNIVERSAL_EXCLUDES,
                probe_status=ProbeStatus.VALID,
                is_root_fallback=True,
            )
        )

        for family_candidates in candidates_by_family.values():
            result.candidates.extend(family_candidates)

        return result

    def discover_family(self, family: LanguageFamily) -> DiscoveryResult:
        result = DiscoveryResult()
        markers = self._scan_markers_for_family(family)
        result.markers = markers

        candidates: list[CandidateContext] = []

        for marker in markers:
            marker_dir = str(Path(marker.path).parent)
            if marker_dir == ".":
                marker_dir = ""

            existing = next((c for c in candidates if c.root_path == marker_dir), None)

            if existing:
                existing.markers.append(marker.path)
                if marker.tier == MarkerTier.WORKSPACE and existing.tier != 1:
                    existing.tier = 1
            else:
                candidates.append(
                    CandidateContext(
                        language_family=family,
                        root_path=marker_dir,
                        tier=1 if marker.tier == MarkerTier.WORKSPACE else 2,
                        markers=[marker.path],
                        include_spec=INCLUDE_SPECS.get(family, []),
                        exclude_spec=UNIVERSAL_EXCLUDES,
                        probe_status=ProbeStatus.PENDING,
                    )
                )

        if not candidates and family in AMBIENT_NAMES:
            candidates.append(
                CandidateContext(
                    language_family=family,
                    root_path="",
                    tier=None,
                    markers=[],
                    include_spec=INCLUDE_SPECS.get(family, []),
                    exclude_spec=UNIVERSAL_EXCLUDES,
                    probe_status=ProbeStatus.PENDING,
                )
            )

        result.candidates = candidates
        return result

    def _scan_markers(self) -> list[DiscoveredMarker]:
        all_files = _walk_with_pruning(self.repo_root)

        marker_lookup: dict[str, list[tuple[LanguageFamily, MarkerTier]]] = {}
        for family, tier_markers in MARKER_DEFINITIONS.items():
            for tier, marker_names in tier_markers.items():
                for name in marker_names:
                    marker_lookup.setdefault(name, []).append((family, tier))

        dotnet_extensions = {".sln", ".csproj", ".fsproj", ".vbproj"}
        markers: list[DiscoveredMarker] = []

        for rel_dir, filename in all_files:
            rel_path = f"{rel_dir}/{filename}" if rel_dir else filename

            if filename in marker_lookup:
                for family, tier in marker_lookup[filename]:
                    markers.append(DiscoveredMarker(path=rel_path, family=family, tier=tier))

            ext = Path(filename).suffix.lower()
            if ext in dotnet_extensions:
                tier = MarkerTier.WORKSPACE if ext == ".sln" else MarkerTier.PACKAGE
                markers.append(
                    DiscoveredMarker(path=rel_path, family=LanguageFamily.CSHARP, tier=tier)
                )

        markers = self._handle_rust_workspaces(markers)
        markers = self._handle_js_workspaces(markers)
        markers = self._handle_maven_modules(markers)

        return markers

    def _scan_markers_for_family(self, family: LanguageFamily) -> list[DiscoveredMarker]:
        all_files = _walk_with_pruning(self.repo_root)
        tier_markers = MARKER_DEFINITIONS.get(family, {})
        markers: list[DiscoveredMarker] = []

        for rel_dir, filename in all_files:
            rel_path = f"{rel_dir}/{filename}" if rel_dir else filename
            for tier, marker_names in tier_markers.items():
                if filename in marker_names:
                    markers.append(DiscoveredMarker(path=rel_path, family=family, tier=tier))

        return markers

    def _handle_rust_workspaces(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        result: list[DiscoveredMarker] = []
        for marker in markers:
            if marker.family == LanguageFamily.RUST and marker.path.endswith("Cargo.toml"):
                try:
                    content = (self.repo_root / marker.path).read_text()
                    if "[workspace]" in content:
                        result.append(
                            DiscoveredMarker(marker.path, marker.family, MarkerTier.WORKSPACE)
                        )
                        continue
                except (OSError, UnicodeDecodeError):
                    pass
            result.append(marker)
        return result

    def _handle_js_workspaces(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        result: list[DiscoveredMarker] = []
        for marker in markers:
            if (
                marker.family == LanguageFamily.JAVASCRIPT
                and marker.path.endswith("package.json")
                and marker.tier == MarkerTier.PACKAGE
            ):
                try:
                    data = json.loads((self.repo_root / marker.path).read_text())
                    if "workspaces" in data:
                        result.append(
                            DiscoveredMarker(marker.path, marker.family, MarkerTier.WORKSPACE)
                        )
                        continue
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    pass
            result.append(marker)
        return result

    def _handle_maven_modules(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        result: list[DiscoveredMarker] = []
        for marker in markers:
            if (
                marker.family == LanguageFamily.JAVA
                and marker.path.endswith("pom.xml")
                and marker.tier == MarkerTier.PACKAGE
            ):
                try:
                    content = (self.repo_root / marker.path).read_text()
                    if "<modules>" in content:
                        result.append(
                            DiscoveredMarker(marker.path, marker.family, MarkerTier.WORKSPACE)
                        )
                        continue
                except (OSError, UnicodeDecodeError):
                    pass
            result.append(marker)
        return result
