"""Context discovery for automatic project boundary detection.

This module implements Phase A of SPEC.md §8.4: Discovery (Candidate Generation).
It scans for marker files (package.json, go.mod, Cargo.toml, etc.) and generates
CandidateContext objects.

Key concepts:
- Tier 1 markers: Workspace fences (define authority boundaries)
- Tier 2 markers: Package roots (potential contexts)
- Ambient families: Fallback contexts at repo root for marker-less families
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from codeplane.index.models import (
    CandidateContext,
    LanguageFamily,
    MarkerTier,
    ProbeStatus,
)

if TYPE_CHECKING:
    pass


# Marker file definitions per language family
# WORKSPACE: Monorepo/workspace boundaries that define authority
# PACKAGE: Individual package roots (potential contexts)
MARKER_DEFINITIONS: dict[LanguageFamily, dict[MarkerTier, list[str]]] = {
    LanguageFamily.JAVASCRIPT: {
        MarkerTier.WORKSPACE: [
            "pnpm-workspace.yaml",
            "lerna.json",
            "nx.json",
            "turbo.json",
            "rush.json",
        ],
        MarkerTier.PACKAGE: [
            "package.json",
            "deno.json",
            "deno.jsonc",
            "tsconfig.json",
            "jsconfig.json",
        ],
    },
    LanguageFamily.PYTHON: {
        MarkerTier.WORKSPACE: [
            "uv.lock",
            "poetry.lock",
            "Pipfile.lock",
        ],
        MarkerTier.PACKAGE: [
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "requirements.txt",
            "Pipfile",
        ],
    },
    LanguageFamily.GO: {
        MarkerTier.WORKSPACE: ["go.work"],
        MarkerTier.PACKAGE: ["go.mod"],
    },
    LanguageFamily.RUST: {
        MarkerTier.WORKSPACE: [],  # Cargo.toml with [workspace] is handled specially
        MarkerTier.PACKAGE: ["Cargo.toml"],
    },
    LanguageFamily.JVM: {
        MarkerTier.WORKSPACE: ["settings.gradle", "settings.gradle.kts"],
        MarkerTier.PACKAGE: [
            "build.gradle",
            "build.gradle.kts",
            "pom.xml",
            "build.sbt",
        ],
    },
    LanguageFamily.DOTNET: {
        MarkerTier.WORKSPACE: [],  # .sln files handled via glob
        MarkerTier.PACKAGE: [],  # .csproj, .fsproj handled via glob
    },
    LanguageFamily.CPP: {
        MarkerTier.WORKSPACE: [],
        MarkerTier.PACKAGE: [
            "CMakeLists.txt",
            "Makefile",
            "meson.build",
            "BUILD",
            "BUILD.bazel",
            "compile_commands.json",
        ],
    },
    LanguageFamily.TERRAFORM: {
        MarkerTier.WORKSPACE: [".terraform.lock.hcl"],
        MarkerTier.PACKAGE: ["main.tf", "versions.tf"],
    },
    LanguageFamily.RUBY: {
        MarkerTier.WORKSPACE: ["Gemfile.lock"],
        MarkerTier.PACKAGE: ["Gemfile"],
    },
    LanguageFamily.PHP: {
        MarkerTier.WORKSPACE: ["composer.lock"],
        MarkerTier.PACKAGE: ["composer.json"],
    },
    LanguageFamily.CONFIG: {
        MarkerTier.WORKSPACE: ["flake.lock"],
        MarkerTier.PACKAGE: ["flake.nix"],
    },
    LanguageFamily.PROTOBUF: {
        MarkerTier.WORKSPACE: ["buf.work.yaml"],
        MarkerTier.PACKAGE: ["buf.yaml"],
    },
}

# Families that get ambient (root-level) contexts if no markers found
AMBIENT_FAMILIES: frozenset[LanguageFamily] = frozenset(
    {
        LanguageFamily.SQL,
        LanguageFamily.DOCKER,
        LanguageFamily.MARKDOWN,
        LanguageFamily.JSON_YAML,
        LanguageFamily.GRAPHQL,
    }
)

# Include specs per family (canonical globs from SPEC.md §8.4.4)
INCLUDE_SPECS: dict[LanguageFamily, list[str]] = {
    LanguageFamily.JAVASCRIPT: [
        "**/*.js",
        "**/*.jsx",
        "**/*.mjs",
        "**/*.cjs",
        "**/*.vue",
        "**/*.svelte",
        "**/*.astro",
        "**/*.ts",
        "**/*.tsx",
        "**/*.cts",
        "**/*.mts",
    ],
    LanguageFamily.PYTHON: [
        "**/*.py",
        "**/*.pyi",
        "**/*.pyw",
        "**/*.pyx",
        "**/*.pxd",
        "**/*.pxi",
    ],
    LanguageFamily.GO: ["**/*.go"],
    LanguageFamily.RUST: ["**/*.rs"],
    LanguageFamily.JVM: [
        "**/*.java",
        "**/*.kt",
        "**/*.kts",
        "**/*.scala",
        "**/*.sc",
    ],
    LanguageFamily.DOTNET: ["**/*.cs", "**/*.fs", "**/*.fsx", "**/*.vb"],
    LanguageFamily.CPP: [
        "**/*.cpp",
        "**/*.cc",
        "**/*.cxx",
        "**/*.c",
        "**/*.h",
        "**/*.hpp",
        "**/*.hxx",
    ],
    LanguageFamily.RUBY: ["**/*.rb", "**/*.rake", "**/Gemfile"],
    LanguageFamily.PHP: ["**/*.php"],
    LanguageFamily.SWIFT: ["**/*.swift"],
    LanguageFamily.ELIXIR: ["**/*.ex", "**/*.exs"],
    LanguageFamily.HASKELL: ["**/*.hs"],
    LanguageFamily.TERRAFORM: ["**/*.tf", "**/*.hcl"],
    LanguageFamily.SQL: ["**/*.sql"],
    LanguageFamily.DOCKER: [
        "**/Dockerfile",
        "**/*.Dockerfile",
        "**/docker-compose.yml",
        "**/docker-compose.yaml",
    ],
    LanguageFamily.MARKDOWN: ["**/*.md", "**/*.markdown", "**/*.mdx"],
    LanguageFamily.JSON_YAML: [
        "**/*.json",
        "**/*.yaml",
        "**/*.yml",
        "**/*.toml",
        "**/*.jsonc",
    ],
    LanguageFamily.PROTOBUF: ["**/*.proto"],
    LanguageFamily.GRAPHQL: ["**/*.graphql", "**/*.gql"],
    LanguageFamily.CONFIG: ["**/*.nix"],
}

# Universal excludes (applied to all contexts)
UNIVERSAL_EXCLUDES: list[str] = [
    "**/node_modules/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.git/**",
    "**/target/**",
    "**/dist/**",
    "**/build/**",
    "**/vendor/**",
]


@dataclass
class DiscoveredMarker:
    """A marker file discovered during scanning."""

    path: str  # Relative POSIX path
    family: LanguageFamily
    tier: MarkerTier


@dataclass
class DiscoveryResult:
    """Result of context discovery."""

    candidates: list[CandidateContext] = field(default_factory=list)
    markers: list[DiscoveredMarker] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ContextDiscovery:
    """
    Discovers project contexts by scanning for marker files.

    Implements Phase A of SPEC.md §8.4: Discovery (Candidate Generation).

    Usage::

        discovery = ContextDiscovery(repo_root)
        result = discovery.discover_all()

        for candidate in result.candidates:
            print(f"{candidate.language_family}: {candidate.root_path}")
    """

    def __init__(self, repo_root: Path) -> None:
        """
        Initialize context discovery.

        Args:
            repo_root: Path to repository root
        """
        self.repo_root = repo_root

    def discover_all(self) -> DiscoveryResult:
        """
        Discover all candidate contexts in the repository.

        Returns:
            DiscoveryResult with candidates and discovered markers.
        """
        result = DiscoveryResult()

        # Phase A.1: Scan for markers
        markers = self._scan_markers()
        result.markers = markers

        # Phase A.2: Generate candidates from markers
        candidates_by_family: dict[LanguageFamily, list[CandidateContext]] = {}

        for marker in markers:
            if marker.family not in candidates_by_family:
                candidates_by_family[marker.family] = []

            # Find or create candidate for this marker's directory
            marker_dir = str(Path(marker.path).parent)
            if marker_dir == ".":
                marker_dir = ""

            existing = next(
                (c for c in candidates_by_family[marker.family] if c.root_path == marker_dir),
                None,
            )

            if existing:
                existing.markers.append(marker.path)
                # Upgrade tier if needed
                if marker.tier == MarkerTier.WORKSPACE and existing.tier != 1:
                    existing.tier = 1
            else:
                candidate = CandidateContext(
                    language_family=marker.family,
                    root_path=marker_dir,
                    tier=1 if marker.tier == MarkerTier.WORKSPACE else 2,
                    markers=[marker.path],
                    include_spec=INCLUDE_SPECS.get(marker.family, []),
                    exclude_spec=list(UNIVERSAL_EXCLUDES),
                    probe_status=ProbeStatus.PENDING,
                )
                candidates_by_family[marker.family].append(candidate)

        # Phase A.3: Add ambient contexts for families without markers
        for family in AMBIENT_FAMILIES:
            if family not in candidates_by_family:
                candidate = CandidateContext(
                    language_family=family,
                    root_path="",
                    tier=None,  # Ambient
                    markers=[],
                    include_spec=INCLUDE_SPECS.get(family, []),
                    exclude_spec=list(UNIVERSAL_EXCLUDES),
                    probe_status=ProbeStatus.PENDING,
                )
                candidates_by_family[family] = [candidate]

        # Phase A.4: Add root fallback context (tier 3)
        # This catches all files not claimed by marker-based or ambient contexts
        root_fallback = CandidateContext(
            language_family=LanguageFamily.CONFIG,  # Placeholder, actual detection per-file
            root_path="",
            tier=3,  # Lowest priority
            markers=[],
            include_spec=["**/*"],  # Include everything
            exclude_spec=list(UNIVERSAL_EXCLUDES),
            probe_status=ProbeStatus.VALID,  # Always valid
            is_root_fallback=True,  # Flag for special handling
        )
        result.candidates.append(root_fallback)

        # Flatten to list
        for family_candidates in candidates_by_family.values():
            result.candidates.extend(family_candidates)

        return result

    def discover_family(self, family: LanguageFamily) -> DiscoveryResult:
        """
        Discover contexts for a specific language family.

        Args:
            family: Language family to discover

        Returns:
            DiscoveryResult with candidates for the family.
        """
        result = DiscoveryResult()

        markers = self._scan_markers_for_family(family)
        result.markers = markers

        candidates: list[CandidateContext] = []

        for marker in markers:
            marker_dir = str(Path(marker.path).parent)
            if marker_dir == ".":
                marker_dir = ""

            existing = next(
                (c for c in candidates if c.root_path == marker_dir),
                None,
            )

            if existing:
                existing.markers.append(marker.path)
                if marker.tier == MarkerTier.WORKSPACE and existing.tier != 1:
                    existing.tier = 1
            else:
                candidate = CandidateContext(
                    language_family=family,
                    root_path=marker_dir,
                    tier=1 if marker.tier == MarkerTier.WORKSPACE else 2,
                    markers=[marker.path],
                    include_spec=INCLUDE_SPECS.get(family, []),
                    exclude_spec=list(UNIVERSAL_EXCLUDES),
                    probe_status=ProbeStatus.PENDING,
                )
                candidates.append(candidate)

        # Add ambient if no markers and family is ambient
        if not candidates and family in AMBIENT_FAMILIES:
            candidates.append(
                CandidateContext(
                    language_family=family,
                    root_path="",
                    tier=None,
                    markers=[],
                    include_spec=INCLUDE_SPECS.get(family, []),
                    exclude_spec=list(UNIVERSAL_EXCLUDES),
                    probe_status=ProbeStatus.PENDING,
                )
            )

        result.candidates = candidates
        return result

    def _scan_markers(self) -> list[DiscoveredMarker]:
        """Scan repository for all marker files."""
        markers: list[DiscoveredMarker] = []

        for family in MARKER_DEFINITIONS:
            markers.extend(self._scan_markers_for_family(family))

        # Handle special cases: .sln files for dotnet
        for sln_path in self.repo_root.rglob("*.sln"):
            rel_path = str(sln_path.relative_to(self.repo_root)).replace("\\", "/")
            if not self._is_excluded(rel_path):
                markers.append(
                    DiscoveredMarker(
                        path=rel_path,
                        family=LanguageFamily.DOTNET,
                        tier=MarkerTier.WORKSPACE,
                    )
                )

        # Handle .csproj, .fsproj, .vbproj for dotnet
        for pattern in ["*.csproj", "*.fsproj", "*.vbproj"]:
            for proj_path in self.repo_root.rglob(pattern):
                rel_path = str(proj_path.relative_to(self.repo_root)).replace("\\", "/")
                if not self._is_excluded(rel_path):
                    markers.append(
                        DiscoveredMarker(
                            path=rel_path,
                            family=LanguageFamily.DOTNET,
                            tier=MarkerTier.PACKAGE,
                        )
                    )

        # Handle Cargo.toml with [workspace] specially
        markers = self._handle_rust_workspaces(markers)

        # Handle package.json with workspaces
        markers = self._handle_js_workspaces(markers)

        # Handle pom.xml with <modules>
        markers = self._handle_maven_modules(markers)

        return markers

    def _scan_markers_for_family(self, family: LanguageFamily) -> list[DiscoveredMarker]:
        """Scan for markers of a specific family."""
        markers: list[DiscoveredMarker] = []

        tier_markers = MARKER_DEFINITIONS.get(family, {})

        for tier, marker_names in tier_markers.items():
            for marker_name in marker_names:
                for marker_path in self.repo_root.rglob(marker_name):
                    rel_path = str(marker_path.relative_to(self.repo_root)).replace("\\", "/")
                    if not self._is_excluded(rel_path):
                        markers.append(
                            DiscoveredMarker(
                                path=rel_path,
                                family=family,
                                tier=tier,
                            )
                        )

        return markers

    def _is_excluded(self, path: str) -> bool:
        """Check if path matches universal excludes."""
        excluded_parts = {
            "node_modules",
            "venv",
            "__pycache__",
            ".git",
            "target",
            "dist",
            "build",
            "vendor",
        }
        parts = path.split("/")
        return any(part in excluded_parts for part in parts)

    def _handle_rust_workspaces(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        """Upgrade Cargo.toml with [workspace] to Tier 1."""
        result: list[DiscoveredMarker] = []

        for marker in markers:
            if marker.family == LanguageFamily.RUST and marker.path.endswith("Cargo.toml"):
                full_path = self.repo_root / marker.path
                try:
                    content = full_path.read_text()
                    if "[workspace]" in content:
                        result.append(
                            DiscoveredMarker(
                                path=marker.path,
                                family=marker.family,
                                tier=MarkerTier.WORKSPACE,
                            )
                        )
                    else:
                        result.append(marker)
                except OSError:
                    result.append(marker)
            else:
                result.append(marker)

        return result

    def _handle_js_workspaces(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        """Upgrade package.json with workspaces to Tier 1."""
        result: list[DiscoveredMarker] = []

        for marker in markers:
            if (
                marker.family == LanguageFamily.JAVASCRIPT
                and marker.path.endswith("package.json")
                and marker.tier == MarkerTier.PACKAGE
            ):
                full_path = self.repo_root / marker.path
                try:
                    content = full_path.read_text()
                    data = json.loads(content)
                    if "workspaces" in data:
                        result.append(
                            DiscoveredMarker(
                                path=marker.path,
                                family=marker.family,
                                tier=MarkerTier.WORKSPACE,
                            )
                        )
                    else:
                        result.append(marker)
                except (OSError, json.JSONDecodeError):
                    result.append(marker)
            else:
                result.append(marker)

        return result

    def _handle_maven_modules(self, markers: list[DiscoveredMarker]) -> list[DiscoveredMarker]:
        """Upgrade pom.xml with <modules> to Tier 1."""
        result: list[DiscoveredMarker] = []

        for marker in markers:
            if (
                marker.family == LanguageFamily.JVM
                and marker.path.endswith("pom.xml")
                and marker.tier == MarkerTier.PACKAGE
            ):
                full_path = self.repo_root / marker.path
                try:
                    content = full_path.read_text()
                    if "<modules>" in content:
                        result.append(
                            DiscoveredMarker(
                                path=marker.path,
                                family=marker.family,
                                tier=MarkerTier.WORKSPACE,
                            )
                        )
                    else:
                        result.append(marker)
                except OSError:
                    result.append(marker)
            else:
                result.append(marker)

        return result
