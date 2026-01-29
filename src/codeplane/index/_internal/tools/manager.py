"""Tool management for external SCIP indexers.

This module implements the Gatekeeper pattern from the Semantic Layer design.
It manages the lifecycle of external SCIP indexers: knowledge, isolation,
and verification.

Key principles:
1. Strict Partition: Only External Twelve families use external tools
2. Isolation: Tools installed in ~/.codeplane/bin, never system-wide
3. JIT Installation: Tools downloaded on-demand, not during discovery
4. Fail-Fast: Jobs check tool availability before running
"""

from __future__ import annotations

import gzip
import hashlib
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen

if TYPE_CHECKING:
    pass

from codeplane.index.models import LanguageFamily


class ToolStatus(str, Enum):
    """Status of an external tool."""

    NOT_INSTALLED = "not_installed"
    INSTALLED = "installed"
    BUNDLED = "bundled"  # Built-in (e.g., scip-python via pip)
    UPDATE_AVAILABLE = "update_available"


class Architecture(str, Enum):
    """Supported CPU architectures."""

    X86_64 = "x86_64"
    ARM64 = "arm64"
    UNKNOWN = "unknown"


class OperatingSystem(str, Enum):
    """Supported operating systems."""

    LINUX = "linux"
    MACOS = "darwin"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


@dataclass
class ToolRecipe:
    """Recipe for downloading and verifying an external SCIP indexer."""

    name: str
    family: LanguageFamily
    version: str
    # URL template with {os}, {arch}, {version} placeholders
    url_template: str
    # Command to verify installation (e.g., ["scip-go", "--version"])
    verify_command: list[str]
    # Expected substring in verify output
    verify_contains: str | None = None
    # Archive type: "tar.gz", "zip", or "binary"
    archive_type: str = "tar.gz"
    # Binary name inside archive (if different from tool name)
    binary_name: str | None = None
    # SHA256 checksums per platform (optional)
    checksums: dict[str, str] = field(default_factory=dict)
    # Estimated download size in bytes
    download_size: int = 0
    # Whether this tool is bundled (installed via pip)
    bundled: bool = False


# Tool recipes for each external SCIP indexer
TOOL_RECIPES: dict[LanguageFamily, ToolRecipe] = {
    LanguageFamily.PYTHON: ToolRecipe(
        name="scip-python",
        family=LanguageFamily.PYTHON,
        version="0.5.0",
        url_template="",  # Installed via pip
        verify_command=["scip-python", "--version"],
        verify_contains="scip-python",
        bundled=True,
    ),
    LanguageFamily.GO: ToolRecipe(
        name="scip-go",
        family=LanguageFamily.GO,
        version="0.4.0",
        url_template=(
            "https://github.com/sourcegraph/scip-go/releases/download/"
            "v{version}/scip-go_{os}_{arch}.tar.gz"
        ),
        verify_command=["scip-go", "version"],
        verify_contains="scip-go",
        archive_type="tar.gz",
        download_size=15_000_000,
    ),
    LanguageFamily.RUST: ToolRecipe(
        name="rust-analyzer",
        family=LanguageFamily.RUST,
        version="2024-01-01",
        url_template=(
            "https://github.com/rust-lang/rust-analyzer/releases/download/"
            "{version}/rust-analyzer-{arch}-{os}.gz"
        ),
        verify_command=["rust-analyzer", "--version"],
        verify_contains="rust-analyzer",
        archive_type="binary",
        download_size=45_000_000,
    ),
    LanguageFamily.JAVASCRIPT: ToolRecipe(
        name="scip-typescript",
        family=LanguageFamily.JAVASCRIPT,
        version="0.3.14",
        url_template="",  # Requires npm/npx
        verify_command=["npx", "@sourcegraph/scip-typescript", "--version"],
        verify_contains="scip-typescript",
        bundled=False,  # Requires Node.js runtime
    ),
    LanguageFamily.JVM: ToolRecipe(
        name="scip-java",
        family=LanguageFamily.JVM,
        version="0.8.30",
        url_template=(
            "https://github.com/sourcegraph/scip-java/releases/download/" "v{version}/scip-java.jar"
        ),
        verify_command=["java", "-jar", "scip-java.jar", "--version"],
        verify_contains="scip-java",
        archive_type="binary",
        download_size=50_000_000,
    ),
    LanguageFamily.DOTNET: ToolRecipe(
        name="scip-dotnet",
        family=LanguageFamily.DOTNET,
        version="0.5.0",
        url_template=(
            "https://github.com/sourcegraph/scip-dotnet/releases/download/"
            "v{version}/scip-dotnet-{os}-{arch}"
        ),
        verify_command=["scip-dotnet", "--version"],
        verify_contains="scip-dotnet",
        archive_type="binary",
        download_size=25_000_000,
    ),
    LanguageFamily.RUBY: ToolRecipe(
        name="scip-ruby",
        family=LanguageFamily.RUBY,
        version="0.4.0",
        url_template=(
            "https://github.com/sourcegraph/scip-ruby/releases/download/"
            "v{version}/scip-ruby-{os}-{arch}.tar.gz"
        ),
        verify_command=["scip-ruby", "--version"],
        verify_contains="scip-ruby",
        archive_type="tar.gz",
        download_size=30_000_000,
    ),
    LanguageFamily.PHP: ToolRecipe(
        name="scip-php",
        family=LanguageFamily.PHP,
        version="0.2.0",
        url_template="",  # Requires composer
        verify_command=["composer", "exec", "scip-php", "--", "--version"],
        verify_contains="scip-php",
        bundled=False,  # Requires PHP runtime
    ),
    LanguageFamily.CPP: ToolRecipe(
        name="scip-clang",
        family=LanguageFamily.CPP,
        version="0.3.0",
        url_template=(
            "https://github.com/sourcegraph/scip-clang/releases/download/"
            "v{version}/scip-clang-{os}-{arch}.tar.gz"
        ),
        verify_command=["scip-clang", "--version"],
        verify_contains="scip-clang",
        archive_type="tar.gz",
        download_size=100_000_000,
    ),
    LanguageFamily.SWIFT: ToolRecipe(
        name="indexstore-db",
        family=LanguageFamily.SWIFT,
        version="main",
        url_template="",  # Requires Swift toolchain
        verify_command=["swift", "--version"],
        verify_contains="Swift",
        bundled=False,  # Requires Swift runtime
    ),
    LanguageFamily.ELIXIR: ToolRecipe(
        name="elixir-ls",
        family=LanguageFamily.ELIXIR,
        version="0.20.0",
        url_template="",  # Requires mix
        verify_command=["mix", "--version"],
        verify_contains="Mix",
        bundled=False,  # Requires Elixir runtime
    ),
    LanguageFamily.HASKELL: ToolRecipe(
        name="hie-bios",
        family=LanguageFamily.HASKELL,
        version="0.14.0",
        url_template="",  # Requires ghcup
        verify_command=["ghc", "--version"],
        verify_contains="Haskell",
        bundled=False,  # Requires GHC runtime
    ),
}


@dataclass
class ToolInfo:
    """Information about an installed or available tool."""

    recipe: ToolRecipe
    status: ToolStatus
    installed_version: str | None = None
    install_path: Path | None = None
    error: str | None = None


@dataclass
class InstallResult:
    """Result of a tool installation attempt."""

    success: bool
    tool_name: str
    version: str
    install_path: Path | None = None
    error: str | None = None


@dataclass
class ShoppingListItem:
    """Item in the tool installation shopping list."""

    family: LanguageFamily
    tool_name: str
    version: str
    download_url: str
    download_size: int
    requires_runtime: str | None = None  # e.g., "Node.js", "Java"


class ToolManager:
    """
    Central gatekeeper for external SCIP indexers.

    Responsibilities:
    - Knowledge: Holds recipes for all supported tools
    - Isolation: Installs tools in ~/.codeplane/bin
    - Verification: Checks tool availability

    Usage::

        manager = ToolManager()

        # Check if tool is available
        if manager.is_available(LanguageFamily.GO):
            # Run indexing
            pass
        else:
            # Fail-fast with MISSING_TOOL
            pass

        # Get shopping list for missing tools
        shopping_list = manager.get_shopping_list([
            LanguageFamily.GO,
            LanguageFamily.RUST,
        ])

        # Install tools after user confirmation
        for item in shopping_list:
            result = manager.install(item.family)
    """

    def __init__(self, install_dir: Path | None = None):
        """
        Initialize the ToolManager.

        Args:
            install_dir: Directory for tool installation.
                         Defaults to ~/.codeplane/bin
        """
        if install_dir is None:
            install_dir = Path.home() / ".codeplane" / "bin"
        self.install_dir = install_dir
        self._os = self._detect_os()
        self._arch = self._detect_arch()
        self._cache: dict[LanguageFamily, ToolInfo] = {}

    def _detect_os(self) -> OperatingSystem:
        """Detect the current operating system."""
        system = platform.system().lower()
        if system == "linux":
            return OperatingSystem.LINUX
        if system == "darwin":
            return OperatingSystem.MACOS
        if system == "windows":
            return OperatingSystem.WINDOWS
        return OperatingSystem.UNKNOWN

    def _detect_arch(self) -> Architecture:
        """Detect the current CPU architecture."""
        machine = platform.machine().lower()
        if machine in ("x86_64", "amd64"):
            return Architecture.X86_64
        if machine in ("arm64", "aarch64"):
            return Architecture.ARM64
        return Architecture.UNKNOWN

    def is_available(self, family: LanguageFamily) -> bool:
        """
        Check if the tool for a language family is ready to use.

        This is the primary "gatekeeper" check used by the Fail-Fast protocol.

        Args:
            family: Language family to check

        Returns:
            True if tool is installed and verified.
        """
        # Internal families never need external tools
        if family.is_internal:
            return True

        info = self.get_tool_info(family)
        return info.status in (ToolStatus.INSTALLED, ToolStatus.BUNDLED)

    def get_tool_info(self, family: LanguageFamily) -> ToolInfo:
        """Get detailed information about a tool."""
        if family in self._cache:
            return self._cache[family]

        recipe = TOOL_RECIPES.get(family)
        if recipe is None:
            return ToolInfo(
                recipe=ToolRecipe(
                    name="unknown",
                    family=family,
                    version="0.0.0",
                    url_template="",
                    verify_command=[],
                ),
                status=ToolStatus.NOT_INSTALLED,
                error=f"No recipe for {family.value}",
            )

        # Check if bundled
        if recipe.bundled:
            if self._verify_tool(recipe):
                info = ToolInfo(
                    recipe=recipe,
                    status=ToolStatus.BUNDLED,
                    installed_version=recipe.version,
                )
            else:
                info = ToolInfo(
                    recipe=recipe,
                    status=ToolStatus.NOT_INSTALLED,
                    error="Bundled tool not found in PATH",
                )
            self._cache[family] = info
            return info

        # Check local installation
        tool_path = self.install_dir / recipe.name
        if tool_path.exists() and self._verify_tool(recipe, tool_path):
            info = ToolInfo(
                recipe=recipe,
                status=ToolStatus.INSTALLED,
                installed_version=recipe.version,
                install_path=tool_path,
            )
        else:
            info = ToolInfo(
                recipe=recipe,
                status=ToolStatus.NOT_INSTALLED,
            )

        self._cache[family] = info
        return info

    def _verify_tool(self, recipe: ToolRecipe, tool_path: Path | None = None) -> bool:
        """Verify a tool is working by running its verify command."""
        if not recipe.verify_command:
            return False

        cmd = list(recipe.verify_command)
        if tool_path and cmd:
            # Replace first element with full path
            cmd[0] = str(tool_path)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if recipe.verify_contains:
                return recipe.verify_contains in result.stdout + result.stderr
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False

    def get_shopping_list(self, families: list[LanguageFamily]) -> list[ShoppingListItem]:
        """
        Build a shopping list of missing tools.

        This is used by the Coordinator's User Confirmation Loop.

        Args:
            families: Language families that need tools

        Returns:
            List of tools to install, with URLs and sizes.
        """
        items: list[ShoppingListItem] = []

        for family in families:
            if family.is_internal:
                continue

            info = self.get_tool_info(family)
            if info.status in (ToolStatus.INSTALLED, ToolStatus.BUNDLED):
                continue

            recipe = info.recipe
            url = self._build_download_url(recipe)
            requires_runtime = self._get_required_runtime(family)

            items.append(
                ShoppingListItem(
                    family=family,
                    tool_name=recipe.name,
                    version=recipe.version,
                    download_url=url,
                    download_size=recipe.download_size,
                    requires_runtime=requires_runtime,
                )
            )

        return items

    def _build_download_url(self, recipe: ToolRecipe) -> str:
        """Build the download URL for a tool."""
        if not recipe.url_template:
            return ""

        os_name = {
            OperatingSystem.LINUX: "linux",
            OperatingSystem.MACOS: "darwin",
            OperatingSystem.WINDOWS: "windows",
        }.get(self._os, "linux")

        arch_name = {
            Architecture.X86_64: "amd64",
            Architecture.ARM64: "arm64",
        }.get(self._arch, "amd64")

        return recipe.url_template.format(
            version=recipe.version,
            os=os_name,
            arch=arch_name,
        )

    def _get_required_runtime(self, family: LanguageFamily) -> str | None:
        """Get the required runtime for a language family's tool."""
        runtime_map = {
            LanguageFamily.JAVASCRIPT: "Node.js",
            LanguageFamily.JVM: "Java Runtime (JRE 11+)",
            LanguageFamily.PHP: "PHP and Composer",
            LanguageFamily.SWIFT: "Swift Toolchain",
            LanguageFamily.ELIXIR: "Elixir and Mix",
            LanguageFamily.HASKELL: "GHC (via ghcup)",
        }
        return runtime_map.get(family)

    def install(self, family: LanguageFamily) -> InstallResult:
        """
        Install the SCIP indexer for a language family.

        This performs Just-In-Time installation after user confirmation.

        Args:
            family: Language family to install tool for

        Returns:
            InstallResult indicating success or failure.
        """
        recipe = TOOL_RECIPES.get(family)
        if recipe is None:
            return InstallResult(
                success=False,
                tool_name="unknown",
                version="0.0.0",
                error=f"No recipe for {family.value}",
            )

        if recipe.bundled:
            return self._install_bundled(recipe)

        if not recipe.url_template:
            return InstallResult(
                success=False,
                tool_name=recipe.name,
                version=recipe.version,
                error=f"{recipe.name} requires manual installation",
            )

        return self._install_binary(recipe)

    def _install_bundled(self, recipe: ToolRecipe) -> InstallResult:
        """Install a bundled tool via pip."""
        if recipe.family != LanguageFamily.PYTHON:
            return InstallResult(
                success=False,
                tool_name=recipe.name,
                version=recipe.version,
                error="Only Python tools can be pip-installed",
            )

        try:
            subprocess.run(
                ["pip", "install", "--upgrade", "scip-python"],
                check=True,
                capture_output=True,
            )
            # Clear cache to re-verify
            self._cache.pop(recipe.family, None)
            return InstallResult(
                success=True,
                tool_name=recipe.name,
                version=recipe.version,
            )
        except subprocess.CalledProcessError as e:
            return InstallResult(
                success=False,
                tool_name=recipe.name,
                version=recipe.version,
                error=f"pip install failed: {e.stderr.decode() if e.stderr else str(e)}",
            )

    def _install_binary(self, recipe: ToolRecipe) -> InstallResult:
        """Download and install a binary tool."""
        url = self._build_download_url(recipe)
        if not url:
            return InstallResult(
                success=False,
                tool_name=recipe.name,
                version=recipe.version,
                error="No download URL available",
            )

        # Ensure install directory exists
        self.install_dir.mkdir(parents=True, exist_ok=True)

        try:
            # Download to temp file
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = Path(tmp.name)
                self._download_file(url, tmp_path)

            # Verify checksum if available
            platform_key = f"{self._os.value}_{self._arch.value}"
            expected_checksum = recipe.checksums.get(platform_key)
            if expected_checksum:
                actual_checksum = self._compute_sha256(tmp_path)
                if actual_checksum != expected_checksum:
                    tmp_path.unlink()
                    return InstallResult(
                        success=False,
                        tool_name=recipe.name,
                        version=recipe.version,
                        error="Checksum verification failed",
                    )

            # Extract and install
            tool_path = self._extract_and_install(recipe, tmp_path)
            tmp_path.unlink(missing_ok=True)

            # Clear cache to re-verify
            self._cache.pop(recipe.family, None)

            return InstallResult(
                success=True,
                tool_name=recipe.name,
                version=recipe.version,
                install_path=tool_path,
            )

        except Exception as e:
            return InstallResult(
                success=False,
                tool_name=recipe.name,
                version=recipe.version,
                error=str(e),
            )

    def _download_file(self, url: str, dest: Path) -> None:
        """Download a file from URL."""
        with urlopen(url, timeout=300) as response, open(dest, "wb") as f:
            shutil.copyfileobj(response, f)

    def _compute_sha256(self, path: Path) -> str:
        """Compute SHA256 checksum of a file."""
        sha256 = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _extract_and_install(self, recipe: ToolRecipe, archive_path: Path) -> Path:
        """Extract archive and install binary."""
        tool_path = self.install_dir / recipe.name
        binary_name = recipe.binary_name or recipe.name

        if recipe.archive_type == "binary":
            # Direct binary (possibly gzipped)
            if str(archive_path).endswith(".gz"):
                with gzip.open(archive_path, "rb") as f_in, open(tool_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            else:
                shutil.copy(archive_path, tool_path)

        elif recipe.archive_type == "tar.gz":
            with tarfile.open(archive_path, "r:gz") as tar:
                # Find the binary in the archive
                for member in tar.getmembers():
                    if member.name.endswith(binary_name) or member.name == binary_name:
                        # Extract to install dir
                        member.name = recipe.name
                        tar.extract(member, self.install_dir)
                        break
                else:
                    # Just extract everything and hope for the best
                    tar.extractall(self.install_dir)

        elif recipe.archive_type == "zip":
            with zipfile.ZipFile(archive_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(binary_name) or name == binary_name:
                        with zf.open(name) as src, open(tool_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        break
                else:
                    zf.extractall(self.install_dir)

        # Make executable
        if tool_path.exists():
            tool_path.chmod(tool_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)

        return tool_path

    def uninstall(self, family: LanguageFamily) -> bool:
        """Remove an installed tool."""
        info = self.get_tool_info(family)
        if info.install_path and info.install_path.exists():
            info.install_path.unlink()
            self._cache.pop(family, None)
            return True
        return False

    def clear_cache(self) -> None:
        """Clear the tool info cache."""
        self._cache.clear()


def format_shopping_list(items: list[ShoppingListItem]) -> str:
    """Format a shopping list for display to the user."""
    if not items:
        return "All required tools are already installed."

    lines = ["The following tools are needed for semantic indexing:\n"]

    total_size = 0
    for item in items:
        size_mb = item.download_size / 1_000_000
        total_size += item.download_size

        line = f"  • {item.tool_name} v{item.version} ({size_mb:.1f} MB)"
        if item.requires_runtime:
            line += f" [requires {item.requires_runtime}]"
        lines.append(line)

    lines.append(f"\nTotal download size: {total_size / 1_000_000:.1f} MB")
    lines.append("\nInstall location: ~/.codeplane/bin")

    return "\n".join(lines)
