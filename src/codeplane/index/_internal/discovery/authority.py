"""Tier 1 authority filter for context discovery.

This module implements Phase A.2 of SPEC.md §8.4: Tier 1 Authority Filter.
It filters Tier 2 candidates based on Tier 1 workspace configuration.

For example:
- pnpm-workspace.yaml lists which packages are part of the workspace
- go.work lists which modules are included
- settings.gradle lists which subprojects are included

Unlisted Tier 2 candidates are marked as "detached" (no owner).
"""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from codeplane.index.models import CandidateContext, LanguageFamily, ProbeStatus

if TYPE_CHECKING:
    pass


@dataclass
class AuthorityResult:
    """Result of authority filtering."""

    pending: list[CandidateContext]
    detached: list[CandidateContext]


class Tier1AuthorityFilter:
    """
    Filters Tier 2 candidates based on Tier 1 workspace configuration.

    Implements Phase A.2 of SPEC.md §8.4.3.

    For families with strict workspace management (javascript, go, rust, jvm),
    Tier 2 candidates must be listed in the Tier 1 configuration to be valid.
    Unlisted candidates are marked as "detached".

    Usage::

        filter = Tier1AuthorityFilter(repo_root)
        result = filter.apply(candidates)

        # result.pending - candidates that passed
        # result.detached - candidates that were excluded
    """

    def __init__(self, repo_root: Path) -> None:
        """Initialize authority filter."""
        self.repo_root = repo_root

    def apply(self, candidates: list[CandidateContext]) -> AuthorityResult:
        """
        Apply Tier 1 authority filter to candidates.

        Args:
            candidates: List of candidate contexts from discovery

        Returns:
            AuthorityResult with pending and detached candidates.
        """
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        # Group by family
        by_family: dict[LanguageFamily, list[CandidateContext]] = {}
        for c in candidates:
            if c.language_family not in by_family:
                by_family[c.language_family] = []
            by_family[c.language_family].append(c)

        # Apply family-specific filtering
        for family, family_candidates in by_family.items():
            if family == LanguageFamily.JAVASCRIPT:
                p, d = self._filter_javascript(family_candidates)
            elif family == LanguageFamily.GO:
                p, d = self._filter_go(family_candidates)
            elif family == LanguageFamily.RUST:
                p, d = self._filter_rust(family_candidates)
            elif family in (
                LanguageFamily.JAVA,
                LanguageFamily.KOTLIN,
                LanguageFamily.SCALA,
                LanguageFamily.GROOVY,
            ):
                # JVM languages share Gradle/Maven workspace systems
                p, d = self._filter_jvm(family_candidates)
            elif family in (
                LanguageFamily.CSHARP,
                LanguageFamily.FSHARP,
                LanguageFamily.VBNET,
            ):
                # .NET languages share solution file workspace system
                p, d = self._filter_dotnet(family_candidates)
            else:
                # These families have no Tier 1 workspace mechanism -
                # they only have Tier 2 project markers (e.g., pyproject.toml,
                # Gemfile, composer.json). All candidates pass through.
                p, d = family_candidates, []

            pending.extend(p)
            detached.extend(d)

        return AuthorityResult(pending=pending, detached=detached)

    def _filter_javascript(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter JavaScript candidates based on workspace config."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        # Find Tier 1 candidates (workspace roots)
        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            # No workspace config - all candidates are pending
            return candidates, []

        # Get workspace globs from each Tier 1
        workspace_globs: dict[str, list[str]] = {}
        for t1 in tier1:
            globs = self._get_js_workspace_globs(t1)
            if globs:
                workspace_globs[t1.root_path] = globs

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            # Check if candidate is under a Tier 1 and matches its globs
            matched = False
            for t1_root, globs in workspace_globs.items():
                if self._is_inside(candidate.root_path, t1_root):
                    rel_path = self._relative_to(candidate.root_path, t1_root)
                    if self._matches_any_glob(rel_path, globs):
                        matched = True
                        break

            if matched or not workspace_globs:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached

    def _filter_go(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter Go candidates based on go.work."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get modules from go.work
        workspace_modules: dict[str, list[str]] = {}
        for t1 in tier1:
            modules = self._get_go_work_modules(t1)
            if modules:
                workspace_modules[t1.root_path] = modules

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, modules in workspace_modules.items():
                if self._is_inside(candidate.root_path, t1_root):
                    rel_path = self._relative_to(candidate.root_path, t1_root)
                    if rel_path in modules or f"./{rel_path}" in modules:
                        matched = True
                        break

            if matched or not workspace_modules:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached

    def _filter_rust(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter Rust candidates based on Cargo workspace."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get members from Cargo.toml [workspace]
        workspace_members: dict[str, list[str]] = {}
        for t1 in tier1:
            members = self._get_cargo_workspace_members(t1)
            if members:
                workspace_members[t1.root_path] = members

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, members in workspace_members.items():
                if self._is_inside(candidate.root_path, t1_root):
                    rel_path = self._relative_to(candidate.root_path, t1_root)
                    if self._matches_any_glob(rel_path, members):
                        matched = True
                        break

            if matched or not workspace_members:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached

    def _filter_jvm(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter JVM candidates based on settings.gradle or Maven pom.xml."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get includes from settings.gradle or Maven pom.xml
        workspace_includes: dict[str, tuple[list[str], bool]] = {}
        for t1 in tier1:
            # Try Gradle first
            includes, is_strict = self._get_gradle_includes(t1)
            if not includes:
                # Fall back to Maven
                includes = self._get_maven_modules(t1)
                is_strict = bool(includes)  # Maven modules are strict
            workspace_includes[t1.root_path] = (includes, is_strict)

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            strict_mode = False
            for t1_root, (includes, is_strict) in workspace_includes.items():
                if self._is_inside(candidate.root_path, t1_root):
                    strict_mode = is_strict
                    rel_path = self._relative_to(candidate.root_path, t1_root)
                    # Gradle uses : as path separator
                    gradle_path = rel_path.replace("/", ":")
                    if gradle_path in includes or rel_path in includes:
                        matched = True
                        break

            # In permissive mode, all are pending
            if matched or not strict_mode:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached

    def _filter_dotnet(
        self, candidates: list[CandidateContext]
    ) -> tuple[list[CandidateContext], list[CandidateContext]]:
        """Filter .NET candidates based on .sln solution files."""
        pending: list[CandidateContext] = []
        detached: list[CandidateContext] = []

        tier1 = [c for c in candidates if c.tier == 1]

        if not tier1:
            return candidates, []

        # Get project paths from each .sln file
        solution_projects: dict[str, list[str]] = {}
        for t1 in tier1:
            projects = self._get_sln_projects(t1)
            if projects:
                solution_projects[t1.root_path] = projects

        for candidate in candidates:
            if candidate.tier == 1:
                pending.append(candidate)
                continue

            matched = False
            for t1_root, projects in solution_projects.items():
                if self._is_inside(candidate.root_path, t1_root):
                    rel_path = self._relative_to(candidate.root_path, t1_root)
                    # Check if candidate path matches any project directory
                    for proj_path in projects:
                        # Project paths in .sln are relative to solution dir
                        # and point to .csproj files - we want the directory
                        proj_dir = str(Path(proj_path).parent).replace("\\", "/")
                        if rel_path == proj_dir or proj_path.replace("\\", "/").startswith(
                            rel_path + "/"
                        ):
                            matched = True
                            break
                    if matched:
                        break

            if matched or not solution_projects:
                pending.append(candidate)
            else:
                candidate.probe_status = ProbeStatus.DETACHED
                detached.append(candidate)

        return pending, detached

    def _get_js_workspace_globs(self, t1: CandidateContext) -> list[str]:
        """Extract workspace globs from JavaScript workspace config."""
        globs: list[str] = []

        for marker in t1.markers:
            marker_path = self.repo_root / marker
            try:
                if marker.endswith("pnpm-workspace.yaml"):
                    content = yaml.safe_load(marker_path.read_text())
                    if content and "packages" in content:
                        globs.extend(content["packages"])
                elif marker.endswith("package.json"):
                    content = json.loads(marker_path.read_text())
                    if "workspaces" in content:
                        ws = content["workspaces"]
                        if isinstance(ws, list):
                            globs.extend(ws)
                        elif isinstance(ws, dict) and "packages" in ws:
                            globs.extend(ws["packages"])
                elif marker.endswith("lerna.json"):
                    content = json.loads(marker_path.read_text())
                    if "packages" in content:
                        globs.extend(content["packages"])
            except (OSError, json.JSONDecodeError, yaml.YAMLError):
                pass

        return globs

    def _get_go_work_modules(self, t1: CandidateContext) -> list[str]:
        """Extract modules from go.work."""
        modules: list[str] = []

        for marker in t1.markers:
            if marker.endswith("go.work"):
                marker_path = self.repo_root / marker
                try:
                    content = marker_path.read_text()
                    # Parse "use (" block
                    in_use_block = False
                    for line in content.split("\n"):
                        line = line.strip()
                        if line.startswith("use ("):
                            in_use_block = True
                            continue
                        if in_use_block:
                            if line == ")":
                                break
                            if line and not line.startswith("//"):
                                modules.append(line)
                        elif line.startswith("use "):
                            # Single use directive
                            modules.append(line[4:].strip())
                except OSError:
                    pass

        return modules

    def _get_cargo_workspace_members(self, t1: CandidateContext) -> list[str]:
        """Extract members from Cargo.toml [workspace]."""
        members: list[str] = []

        for marker in t1.markers:
            if marker.endswith("Cargo.toml"):
                marker_path = self.repo_root / marker
                try:
                    content = marker_path.read_text()
                    # Simple TOML parsing for members
                    in_workspace = False
                    in_members = False
                    for line in content.split("\n"):
                        line = line.strip()
                        if line == "[workspace]":
                            in_workspace = True
                            continue
                        if in_workspace and line.startswith("["):
                            break
                        if in_workspace and line.startswith("members"):
                            in_members = True
                            # Check for inline array
                            if "=" in line and "[" in line:
                                # Parse inline: members = ["a", "b"]
                                match = re.search(r"\[([^\]]+)\]", line)
                                if match:
                                    items = match.group(1)
                                    for item in items.split(","):
                                        item = item.strip().strip('"').strip("'")
                                        if item:
                                            members.append(item)
                                in_members = False
                            continue
                        if in_members:
                            if line == "]":
                                in_members = False
                            elif line and not line.startswith("#"):
                                item = line.strip('",').strip("',")
                                if item:
                                    members.append(item)
                except OSError:
                    pass

        return members

    def _get_gradle_includes(self, t1: CandidateContext) -> tuple[list[str], bool]:
        """Extract includes from settings.gradle."""
        includes: list[str] = []
        is_strict = True  # Assume strict unless we find variables

        for marker in t1.markers:
            if marker.endswith(("settings.gradle", "settings.gradle.kts")):
                marker_path = self.repo_root / marker
                try:
                    content = marker_path.read_text()
                    # Check for variable usage (makes it permissive)
                    if "${" in content or "$" in content:
                        is_strict = False

                    # Parse include statements
                    for match in re.finditer(r"include\s*\(\s*['\"]([^'\"]+)['\"]", content):
                        includes.append(match.group(1))
                    # Also handle include ':path' syntax
                    for match in re.finditer(r"include\s+['\"]([^'\"]+)['\"]", content):
                        includes.append(match.group(1))
                except OSError:
                    pass

        return includes, is_strict

    def _get_maven_modules(self, t1: CandidateContext) -> list[str]:
        """Extract modules from Maven pom.xml."""
        modules: list[str] = []

        for marker in t1.markers:
            if marker.endswith("pom.xml"):
                marker_path = self.repo_root / marker
                try:
                    content = marker_path.read_text()
                    # Parse <modules><module>...</module></modules>
                    # Simple regex - doesn't handle comments but covers typical cases
                    modules_match = re.search(r"<modules>\s*(.*?)\s*</modules>", content, re.DOTALL)
                    if modules_match:
                        modules_block = modules_match.group(1)
                        for module_match in re.finditer(
                            r"<module>\s*([^<]+?)\s*</module>", modules_block
                        ):
                            modules.append(module_match.group(1))
                except OSError:
                    pass

        return modules

    def _get_sln_projects(self, t1: CandidateContext) -> list[str]:
        """Extract project paths from .sln solution file."""
        projects: list[str] = []

        for marker in t1.markers:
            if marker.endswith(".sln"):
                marker_path = self.repo_root / marker
                try:
                    content = marker_path.read_text()
                    # .sln format: Project("{GUID}") = "Name", "path\to\project.csproj", "{GUID}"
                    for match in re.finditer(
                        r'Project\("[^"]+"\)\s*=\s*"[^"]+",\s*"([^"]+)"', content
                    ):
                        proj_path = match.group(1)
                        # Filter to actual project files, not solution folders
                        if proj_path.endswith((".csproj", ".fsproj", ".vbproj")):
                            projects.append(proj_path)
                except OSError:
                    pass

        return projects

    def _is_inside(self, file_path: str, root_path: str) -> bool:
        """Segment-safe containment check."""
        if root_path == "":
            return True
        if file_path == root_path:
            return True
        return file_path.startswith(root_path + "/")

    def _relative_to(self, path: str, root: str) -> str:
        """Get path relative to root."""
        if root == "":
            return path
        if path == root:
            return ""
        if path.startswith(root + "/"):
            return path[len(root) + 1 :]
        return path

    def _matches_any_glob(self, path: str, globs: list[str]) -> bool:
        """Check if path matches any of the globs."""
        for glob in globs:
            # Remove trailing /**
            if glob.endswith("/**"):
                glob = glob[:-3]
            # Remove leading ./
            if glob.startswith("./"):
                glob = glob[2:]
            if fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path, glob + "/*"):
                return True
            # Also check exact match
            if path == glob:
                return True
        return False
