"""Project context — cached project metadata for the action policy monitor.

Current context may be built lazily, but only an explicit pre-execution capture
can establish an initial structural-approval baseline.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import structlog

log = structlog.get_logger()

# Manifest files to scan — bounded, known set per ecosystem
_MANIFEST_FILES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
)

_CONFIG_FILES = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Makefile",
    "Dockerfile",
)

# Regex to extract hostnames from env files and docker-compose
_HOST_RE = re.compile(
    r"(?:https?://|//)"  # scheme or protocol-relative
    r"([a-zA-Z0-9._-]+\.[a-zA-Z]{2,})"  # hostname with TLD
    r"(?::\d+)?",  # optional port
)

# Known dependency key patterns per manifest type
_PYPROJECT_DEP_RE = re.compile(r"""^([a-zA-Z0-9_-]+)""")


class ProjectContext:
    """Cached project metadata for a single job's worktree."""

    def __init__(self, worktree: str, repo: str | None = None) -> None:
        self.worktree = worktree
        self.repo = repo
        self.dependencies: set[str] = set()
        self.configured_hosts: set[str] = set()
        self.services: set[str] = set()
        self.narrative: str = ""
        self._built = False
        # Snapshot of initial context at job start.  Only entries that
        # existed before the agent touched anything are trusted for
        # structural auto-approval.  Entries added after a context
        # rebuild (triggered by the agent modifying a manifest) are
        # tracked for LLM prompts but cannot bypass the gate on their own.
        self._initial_dependencies: frozenset[str] | None = None
        self._initial_hosts: frozenset[str] | None = None
        self._initial_services: frozenset[str] | None = None
        self._initial_workspace: tuple[str, int, int] | None = None

    @property
    def built(self) -> bool:
        return self._built

    async def build(self) -> None:
        """Scan manifest and config files."""
        root = Path(self.worktree)

        # Read manifest files for dependencies
        for name in _MANIFEST_FILES:
            path = root / name
            if path.is_file():
                try:
                    self._parse_manifest(name, path.read_text(errors="replace"))
                except Exception:
                    log.debug("project_context_manifest_error", file=name, exc_info=True)

        # Read config files for hosts and services
        for name in _CONFIG_FILES:
            path = root / name
            if path.is_file():
                try:
                    self._parse_config(name, path.read_text(errors="replace"))
                except Exception:
                    log.debug("project_context_config_error", file=name, exc_info=True)

        self._built = True

        log.info(
            "project_context_built",
            dependencies=len(self.dependencies),
            hosts=len(self.configured_hosts),
            services=len(self.services),
            has_narrative=bool(self.narrative),
        )

    async def capture_initial(self) -> None:
        """Called only by pre-execution setup, never by lazy monitor evaluation."""
        if self._initial_workspace is not None:
            return
        root = Path(self.worktree).resolve(strict=True)
        before = root.stat()
        await self.build()
        after = root.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise RuntimeError("Monitor workspace changed during baseline capture")
        self._initial_dependencies = frozenset(self.dependencies)
        self._initial_hosts = frozenset(self.configured_hosts)
        self._initial_services = frozenset(self.services)
        self._initial_workspace = (str(root), after.st_dev, after.st_ino)

    def matches_initial_workspace(self, workspace: str) -> bool:
        if self._initial_workspace is None:
            return False
        try:
            root = Path(workspace).resolve(strict=True)
            current = root.stat()
        except (OSError, RuntimeError, ValueError):
            return False
        return (str(root), current.st_dev, current.st_ino) == self._initial_workspace

    def has_dependency(self, name: str) -> bool:
        """Check if a dependency name (case-insensitive) is in the project."""
        return name.lower() in self.dependencies

    def has_initial_dependency(self, name: str) -> bool:
        """Check against the pre-agent snapshot only (safe for auto-approval)."""
        if self._initial_dependencies is None or not self.matches_initial_workspace(self.worktree):
            return False
        return name.lower() in self._initial_dependencies

    def has_host(self, host: str) -> bool:
        """Check if a hostname is referenced in config files."""
        return host.lower() in self.configured_hosts

    def has_initial_host(self, host: str) -> bool:
        """Check against the pre-agent snapshot only (safe for auto-approval)."""
        if self._initial_hosts is None or not self.matches_initial_workspace(self.worktree):
            return False
        return host.lower() in self._initial_hosts

    @property
    def initial_services(self) -> frozenset[str]:
        """Return pre-agent service names (safe for auto-approval)."""
        if not self.matches_initial_workspace(self.worktree):
            return frozenset()
        return self._initial_services or frozenset()

    def invalidate(self) -> None:
        """Mark context as needing rebuild (manifest file was modified)."""
        self._built = False

    # -- Internal parsers ---

    def _parse_manifest(self, name: str, content: str) -> None:
        if name == "package.json":
            self._parse_package_json(content)
        elif name == "pyproject.toml":
            self._parse_pyproject_toml(content)
        elif name in ("requirements.txt", "Pipfile"):
            self._parse_requirements_txt(content)
        elif name == "Cargo.toml":
            self._parse_cargo_toml(content)
        elif name == "go.mod":
            self._parse_go_mod(content)
        elif name == "composer.json":
            self._parse_composer_json(content)

    def _parse_config(self, name: str, content: str) -> None:
        # Extract hostnames from any config file
        for match in _HOST_RE.finditer(content):
            host = match.group(1).lower()
            if host not in ("localhost", "example.com", "127.0.0.1"):
                self.configured_hosts.add(host)

        # Docker-compose service names
        if "docker-compose" in name:
            self._parse_docker_compose(content)

    def _parse_package_json(self, content: str) -> None:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            deps = data.get(key, {})
            if isinstance(deps, dict):
                for dep_name in deps:
                    self.dependencies.add(dep_name.lower())

    def _parse_pyproject_toml(self, content: str) -> None:
        # Simple line-by-line extraction — avoid toml dependency
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and "dependencies" in stripped.lower():
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps:
                m = _PYPROJECT_DEP_RE.match(stripped)
                if m:
                    self.dependencies.add(m.group(1).lower())
        # Also look for requires= lines
        for line in content.splitlines():
            if "requires" in line.lower() and "=" in line:
                # Extract quoted package names
                for pkg in re.findall(r'"([a-zA-Z0-9_-]+)', line):
                    self.dependencies.add(pkg.lower())

    def _parse_requirements_txt(self, content: str) -> None:
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = _PYPROJECT_DEP_RE.match(line)
            if m:
                self.dependencies.add(m.group(1).lower())

    def _parse_cargo_toml(self, content: str) -> None:
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and "dependencies" in stripped.lower():
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps and "=" in stripped:
                name = stripped.split("=")[0].strip()
                if name:
                    self.dependencies.add(name.lower())

    def _parse_go_mod(self, content: str) -> None:
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("require") or stripped.startswith("//"):
                continue
            parts = stripped.split()
            if len(parts) >= 2 and "/" in parts[0]:
                # go module paths like github.com/stripe/stripe-go
                module = parts[0].rstrip(")")
                # Use the last path component as the dependency name
                dep_name = module.rsplit("/", 1)[-1]
                self.dependencies.add(dep_name.lower())

    def _parse_composer_json(self, content: str) -> None:
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return
        for key in ("require", "require-dev"):
            deps = data.get(key, {})
            if isinstance(deps, dict):
                for dep_name in deps:
                    # Composer packages are vendor/name — use the name part
                    name = dep_name.rsplit("/", 1)[-1]
                    self.dependencies.add(name.lower())

    def _parse_docker_compose(self, content: str) -> None:
        # Simple extraction of top-level service names under "services:"
        in_services = False
        for line in content.splitlines():
            stripped = line.rstrip()
            if stripped == "services:":
                in_services = True
                continue
            if in_services:
                # Service names are indented exactly 2 spaces with trailing colon
                if stripped and not stripped[0].isspace():
                    in_services = False
                    continue
                if stripped.startswith("  ") and not stripped.startswith("    ") and stripped.endswith(":"):
                    svc_name = stripped.strip().rstrip(":")
                    self.services.add(svc_name.lower())


def is_manifest_file(path: str) -> bool:
    """Check if a file path is a manifest or config file that affects ProjectContext."""
    name = Path(path).name
    return name in _MANIFEST_FILES or name in _CONFIG_FILES
