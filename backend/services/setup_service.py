"""Setup, preflight, and doctor for CodePlane.

Provides:
- A shared verification engine used by ``cpl up``, ``cpl setup``, and ``cpl doctor``.
- An interactive setup wizard (``run_setup``) using questionary + Rich.
- A non-interactive diagnostic (``run_doctor``).
- A quick preflight (``run_preflight``) called before server start.
"""

from __future__ import annotations

import json as _json
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import questionary
from rich.console import Console
from rich.panel import Panel

from backend.config import DEFAULT_CONFIG_PATH, init_config, load_config, save_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_IS_WSL = bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))
_SYSTEM = platform.system().lower()  # "linux", "darwin", "windows"

_console = Console()


# ---------------------------------------------------------------------------
# Check result model
# ---------------------------------------------------------------------------


class CheckStatus(StrEnum):
    passed = "pass"
    warn = "warn"
    fail = "fail"
    skipped = "skip"


@dataclass
class CheckResult:
    label: str
    status: CheckStatus
    detail: str = ""
    hint: str = ""
    category: str = "general"


# ---------------------------------------------------------------------------
# Dependency descriptors (for setup auto-install)
# ---------------------------------------------------------------------------


@dataclass
class Dependency:
    name: str
    command: str
    install_instructions: dict[str, str]
    url: str
    required: bool = True
    auto_install_cmd: dict[str, list[str]] = field(default_factory=dict)


DEPENDENCIES: list[Dependency] = [
    Dependency(
        name="Git",
        command="git",
        url="https://git-scm.com/downloads",
        required=True,
        install_instructions={
            "linux": "sudo apt-get install -y git",
            "darwin": "brew install git",
            "windows": "Download from https://git-scm.com/downloads",
        },
        auto_install_cmd={
            "linux": ["sudo", "apt-get", "install", "-y", "git"],
            "darwin": ["brew", "install", "git"],
        },
    ),
    Dependency(
        name="Node.js",
        command="node",
        url="https://nodejs.org/",
        required=True,
        install_instructions={
            "linux": (
                "curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs"
            ),
            "darwin": "brew install node",
            "windows": "Download installer from https://nodejs.org/",
        },
        auto_install_cmd={
            "linux": ["sudo", "apt-get", "install", "-y", "nodejs"],
            "darwin": ["brew", "install", "node"],
        },
    ),
    Dependency(
        name="npm",
        command="npm",
        url="https://nodejs.org/",
        required=True,
        install_instructions={
            "linux": "Included with Node.js — reinstall Node if missing",
            "darwin": "Included with Node.js — reinstall Node if missing",
            "windows": "Included with Node.js — reinstall Node if missing",
        },
    ),
    Dependency(
        name="GitHub CLI",
        command="gh",
        url="https://cli.github.com/",
        required=True,
        install_instructions={
            "linux": (
                "sudo apt-key adv --keyserver keyserver.ubuntu.com --recv-key 23F3D4EA75716059\n"
                "sudo apt-add-repository https://cli.github.com/packages\n"
                "sudo apt-get update && sudo apt-get install gh"
            ),
            "darwin": "brew install gh",
            "windows": "winget install --id GitHub.cli",
        },
        auto_install_cmd={
            "linux": ["sudo", "apt-get", "install", "-y", "gh"],
            "darwin": ["brew", "install", "gh"],
        },
    ),
    Dependency(
        name="Dev Tunnel CLI",
        command="devtunnel",
        url="https://aka.ms/devtunnels/cli",
        required=False,
        install_instructions={
            "linux": "curl -sL https://aka.ms/DevTunnelCliInstall | bash",
            "darwin": "brew install --cask devtunnel",
            "windows": "winget install Microsoft.devtunnel",
        },
        auto_install_cmd={
            "linux": ["bash", "-c", "curl -sL https://aka.ms/DevTunnelCliInstall | bash"],
            "darwin": ["brew", "install", "--cask", "devtunnel"],
        },
    ),
]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _check_command(cmd: str) -> tuple[bool, str | None]:
    """Check if a command is available, return (found, version_string)."""
    path = shutil.which(cmd)
    if not path:
        return False, None
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        version = result.stdout.strip().split("\n")[0] if result.stdout else "installed"
        return True, version
    except (subprocess.TimeoutExpired, OSError):
        return True, "installed (version unknown)"


def _check_gh_auth() -> tuple[bool, str]:
    """Check if gh CLI is authenticated. Returns (ok, detail)."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            # Try to extract username from output
            for line in (result.stdout + result.stderr).splitlines():
                if "Logged in to" in line and "account" in line.lower():
                    return True, line.strip()
                if "Logged in to" in line:
                    return True, line.strip()
            return True, "authenticated"
        return False, "not authenticated"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, "gh not available"


def _check_devtunnel_login() -> tuple[bool, str]:
    """Check if devtunnel is logged in. Returns (ok, detail)."""
    try:
        result = subprocess.run(
            ["devtunnel", "user", "show"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True, "logged in"
        return False, "not logged in"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False, "devtunnel not available"


def _check_port(port: int) -> tuple[bool, str]:
    """Check if a port is available. Returns (available, detail)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.bind(("127.0.0.1", port))
            return True, "available"
    except OSError:
        return False, "in use"


def _check_sdk_copilot() -> tuple[bool, str]:
    """Check if the Copilot SDK is importable. Returns (ok, detail)."""
    try:
        import copilot  # noqa: F401

        ver = getattr(copilot, "__version__", "installed")
        return True, f"github-copilot-sdk {ver}"
    except ImportError:
        return False, "github-copilot-sdk not installed"


def _check_sdk_claude() -> tuple[bool, str]:
    """Check if Claude SDK is importable and API key is set. Returns (ok, detail)."""
    try:
        import claude_code_sdk  # noqa: F401

        if os.environ.get("ANTHROPIC_API_KEY"):
            return True, "claude-code-sdk installed, API key set"
        return False, "claude-code-sdk installed, ANTHROPIC_API_KEY not set"
    except ImportError:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return False, "claude-code-sdk not installed (API key is set)"
        return False, "claude-code-sdk not installed, ANTHROPIC_API_KEY not set"


def _try_auto_install(dep: Dependency) -> bool:
    """Attempt auto-installation of a dependency. Returns True on success."""
    if not dep.auto_install_cmd or _SYSTEM not in dep.auto_install_cmd:
        return False

    cmd = dep.auto_install_cmd[_SYSTEM]
    _console.print(f"  Attempting: [dim]{' '.join(cmd)}[/dim]")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return True
        _console.print(f"  [red]Auto-install failed (exit {result.returncode})[/red]")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[:3]:
                _console.print(f"    [dim]{line}[/dim]")
    except (subprocess.TimeoutExpired, OSError) as exc:
        _console.print(f"  [red]Auto-install failed: {exc}[/red]")
    return False


def _get_env_persistence_instructions(var_name: str, value: str) -> str:
    """Return OS-specific instructions for persisting an env var."""
    if _SYSTEM == "darwin":
        shell = os.environ.get("SHELL", "/bin/zsh")
        rc = "~/.zshrc" if "zsh" in shell else "~/.bash_profile"
        return f'Add to {rc}:\n  export {var_name}="{value}"\nThen run: source {rc}'
    elif _SYSTEM == "windows":
        return (
            f"Run in PowerShell (Admin):\n"
            f'  [System.Environment]::SetEnvironmentVariable("{var_name}", "{value}", "User")\n'
            f"Or: Settings > System > Advanced > Environment Variables"
        )
    else:  # Linux / WSL
        shell = os.environ.get("SHELL", "/bin/bash")
        if "fish" in shell:
            return f'Run:\n  set -Ux {var_name} "{value}"'
        rc = "~/.zshrc" if "zsh" in shell else "~/.bashrc"
        return f'Add to {rc}:\n  export {var_name}="{value}"\nThen run: source {rc}'


# ---------------------------------------------------------------------------
# Shared verification engine
# ---------------------------------------------------------------------------


def run_checks(*, port: int | None = None) -> list[CheckResult]:
    """Run all preflight checks and return structured results.

    Parameters
    ----------
    port:
        If given, also checks whether the port is available.
    """
    results: list[CheckResult] = []

    # --- Python version ---
    v = sys.version_info
    py_ver = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        results.append(CheckResult("Python", CheckStatus.passed, py_ver, category="deps"))
    else:
        results.append(
            CheckResult(
                "Python",
                CheckStatus.fail,
                py_ver,
                hint="Python 3.11+ is required",
                category="deps",
            )
        )

    # --- System dependencies ---
    for dep in DEPENDENCIES:
        found, version = _check_command(dep.command)
        if found:
            results.append(CheckResult(dep.name, CheckStatus.passed, version or "installed", category="deps"))
        elif dep.required:
            hint = dep.install_instructions.get(_SYSTEM, dep.install_instructions.get("linux", ""))
            results.append(CheckResult(dep.name, CheckStatus.fail, "not found", hint=hint, category="deps"))
        else:
            results.append(
                CheckResult(
                    dep.name,
                    CheckStatus.skipped,
                    "not found (optional)",
                    category="deps",
                )
            )

    # --- Authentication ---
    if shutil.which("gh"):
        ok, detail = _check_gh_auth()
        if ok:
            results.append(CheckResult("GitHub CLI auth", CheckStatus.passed, detail, category="auth"))
        else:
            results.append(
                CheckResult(
                    "GitHub CLI auth",
                    CheckStatus.warn,
                    detail,
                    hint="Run: gh auth login",
                    category="auth",
                )
            )
    else:
        results.append(CheckResult("GitHub CLI auth", CheckStatus.skipped, "gh not installed", category="auth"))

    if shutil.which("devtunnel"):
        ok, detail = _check_devtunnel_login()
        if ok:
            results.append(CheckResult("Dev Tunnel auth", CheckStatus.passed, detail, category="auth"))
        else:
            results.append(
                CheckResult(
                    "Dev Tunnel auth",
                    CheckStatus.warn,
                    detail,
                    hint="Run: devtunnel user login",
                    category="auth",
                )
            )
    else:
        results.append(CheckResult("Dev Tunnel auth", CheckStatus.skipped, "not installed", category="auth"))

    # --- Agent SDK ---
    ok, detail = _check_sdk_copilot()
    if ok:
        results.append(CheckResult("Copilot SDK", CheckStatus.passed, detail, category="sdk"))
    else:
        results.append(
            CheckResult(
                "Copilot SDK",
                CheckStatus.warn,
                detail,
                hint="Install: uv add github-copilot-sdk",
                category="sdk",
            )
        )

    ok, detail = _check_sdk_claude()
    if ok:
        results.append(CheckResult("Claude SDK", CheckStatus.passed, detail, category="sdk"))
    else:
        results.append(
            CheckResult(
                "Claude SDK",
                CheckStatus.warn,
                detail,
                hint="Set ANTHROPIC_API_KEY or add to ~/.codeplane/.env",
                category="sdk",
            )
        )

    # --- Environment ---
    if DEFAULT_CONFIG_PATH.exists():
        results.append(CheckResult("Config", CheckStatus.passed, str(DEFAULT_CONFIG_PATH), category="env"))
    else:
        results.append(
            CheckResult(
                "Config",
                CheckStatus.warn,
                "not found",
                hint=f"Will be created at {DEFAULT_CONFIG_PATH}",
                category="env",
            )
        )

    if port is not None:
        ok, detail = _check_port(port)
        if ok:
            results.append(CheckResult(f"Port {port}", CheckStatus.passed, detail, category="env"))
        else:
            results.append(
                CheckResult(
                    f"Port {port}",
                    CheckStatus.fail,
                    detail,
                    hint=f"Try: cpl up --port {port + 1}\n  Or: lsof -i :{port} | grep LISTEN",
                    category="env",
                )
            )

    # --- Disk space ---
    try:
        disk_path = DEFAULT_CONFIG_PATH.parent if DEFAULT_CONFIG_PATH.parent.exists() else Path.home()
        usage = shutil.disk_usage(str(disk_path))
        free_gb = usage.free / (1024**3)
        if free_gb > 1:
            results.append(CheckResult("Disk space", CheckStatus.passed, f"{free_gb:.0f} GB free", category="env"))
        else:
            results.append(
                CheckResult(
                    "Disk space",
                    CheckStatus.warn,
                    f"{free_gb:.1f} GB free",
                    hint="Less than 1 GB free — may cause issues",
                    category="env",
                )
            )
    except OSError:
        pass

    return results


# ---------------------------------------------------------------------------
# Rich rendering
# ---------------------------------------------------------------------------

_STATUS_ICONS: dict[CheckStatus, str] = {
    CheckStatus.passed: "[green]✓[/green]",
    CheckStatus.warn: "[yellow]![/yellow]",
    CheckStatus.fail: "[red]✗[/red]",
    CheckStatus.skipped: "[dim]⊘[/dim]",
}


def render_checks(results: list[CheckResult], *, grouped: bool = False) -> None:
    """Render check results to the console using Rich."""
    if grouped:
        categories = [
            ("Dependencies", "deps"),
            ("Authentication", "auth"),
            ("Agent SDK", "sdk"),
            ("Environment", "env"),
        ]
        for cat_label, cat_key in categories:
            cat_results = [r for r in results if r.category == cat_key]
            if not cat_results:
                continue
            _console.print()
            _console.print(f"  [bold]{cat_label}[/bold]")
            for r in cat_results:
                _render_check_line(r)
    else:
        for r in results:
            _render_check_line(r)


def _render_check_line(r: CheckResult) -> None:
    """Render a single check result line with optional hint."""
    icon = _STATUS_ICONS[r.status]
    if r.status == CheckStatus.skipped:
        _console.print(f"  {icon}  {r.label:<20s} [dim]{r.detail}[/dim]")
    else:
        _console.print(f"  {icon}  {r.label:<20s} {r.detail}")
    if r.hint and r.status in (CheckStatus.warn, CheckStatus.fail):
        for line in r.hint.split("\n"):
            _console.print(f"       [dim]→ {line}[/dim]")


def render_summary(results: list[CheckResult]) -> None:
    """Render a summary line."""
    passed = sum(1 for r in results if r.status == CheckStatus.passed)
    warns = sum(1 for r in results if r.status == CheckStatus.warn)
    fails = sum(1 for r in results if r.status == CheckStatus.fail)

    parts = [f"[green]{passed} passed[/green]"]
    if warns:
        parts.append(f"[yellow]{warns} warning{'s' if warns != 1 else ''}[/yellow]")
    if fails:
        parts.append(f"[red]{fails} failed[/red]")

    _console.print()
    _console.print(f"  Summary: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# cpl up — preflight
# ---------------------------------------------------------------------------


def run_preflight(port: int) -> bool:
    """Quick non-interactive preflight for ``cpl up``.

    Returns True if the server can start (no hard failures).
    Prints a compact check table.
    """
    results = run_checks(port=port)

    _console.print()
    _console.print("  [bold]Preflight[/bold]")
    _console.print()
    for r in results:
        _render_check_line(r)

    has_fail = any(r.status == CheckStatus.fail for r in results)
    has_warn = any(r.status == CheckStatus.warn for r in results)

    # Auto-create config on first run
    if not DEFAULT_CONFIG_PATH.exists():
        init_config()
        _console.print()
        _console.print("  [dim]Created default config at[/dim]", str(DEFAULT_CONFIG_PATH))

    if has_fail:
        _console.print()
        _console.print("  [red bold]Cannot start — fix the errors above.[/red bold]")
        _console.print("  [dim]Run 'cpl setup' for guided installation, or 'cpl doctor' for details.[/dim]")
        return False

    if has_warn:
        _console.print()
        _console.print("  [yellow]Warnings above may affect some features.[/yellow]")
        # First-run tip
        if not DEFAULT_CONFIG_PATH.exists():
            _console.print("  [dim]Tip: Run 'cpl setup' for guided first-time configuration.[/dim]")

    _console.print()
    return True


# ---------------------------------------------------------------------------
# cpl doctor — non-interactive diagnostic
# ---------------------------------------------------------------------------


def run_doctor(*, as_json: bool = False) -> bool:
    """Full non-interactive diagnostic.

    Returns True if no hard failures.
    """
    results = run_checks(port=load_config().server.port)

    if as_json:
        data = {
            "checks": [
                {
                    "label": r.label,
                    "status": r.status.value,
                    "detail": r.detail,
                    "hint": r.hint,
                    "category": r.category,
                }
                for r in results
            ],
            "passed": sum(1 for r in results if r.status == CheckStatus.passed),
            "warnings": sum(1 for r in results if r.status == CheckStatus.warn),
            "failed": sum(1 for r in results if r.status == CheckStatus.fail),
        }
        print(_json.dumps(data, indent=2))  # noqa: T201
        return not any(r.status == CheckStatus.fail for r in results)

    _console.print()
    _console.print(Panel("[bold]CodePlane Doctor[/bold]", border_style="cyan", expand=False))

    render_checks(results, grouped=True)
    render_summary(results)

    has_fail = any(r.status == CheckStatus.fail for r in results)
    if has_fail:
        _console.print()
        _console.print("  [red]Fix required — run 'cpl setup' to resolve.[/red]")
    else:
        _console.print()
        _console.print("  [green]All clear — run 'cpl up' to start.[/green]")
    _console.print()

    return not has_fail


# ---------------------------------------------------------------------------
# cpl setup — interactive wizard
# ---------------------------------------------------------------------------


def run_setup() -> None:
    """Run the interactive setup wizard."""
    _console.print()
    _console.print(
        Panel(
            "[bold]CodePlane — Initial Setup[/bold]",
            border_style="cyan",
            expand=False,
        )
    )
    _console.print()

    # Step 1: CODEPLANE_HOME
    _setup_home()

    # Step 2: System dependencies
    _setup_dependencies()

    # Step 3: GitHub CLI auth
    _setup_gh_auth()

    # Step 4: Dev Tunnel auth
    _setup_devtunnel_auth()

    # Step 5: Agent SDK
    _setup_sdk()

    # Step 6: Config
    _setup_config()

    # Done
    _console.print()
    _console.rule(style="green")
    _console.print()
    _console.print("  [bold green]✓ Setup complete![/bold green]")
    _console.print()
    _console.print("  Quick start:")
    _console.print("    [cyan]cpl up[/cyan]                   Start the server")
    _console.print("    [cyan]cpl up --tunnel[/cyan]          Start with remote access")
    _console.print("    [cyan]cpl up --dev[/cyan]             Start in dev mode (hot-reload)")
    _console.print("    [cyan]cpl doctor[/cyan]               Check everything without starting")
    _console.print()


def _step_header(num: int, total: int, title: str) -> None:
    """Print a step header."""
    _console.print()
    _console.rule(f"[bold cyan]Step {num} of {total} · {title}[/bold cyan]", style="dim")
    _console.print()


def _setup_home() -> None:
    """Step 1: Configure CODEPLANE_HOME directory."""
    _step_header(1, 6, "Data Directory")

    current = os.environ.get("CODEPLANE_HOME")
    default = str(Path.home() / ".codeplane")

    if current:
        _console.print(f"  CODEPLANE_HOME is set to: [bold]{current}[/bold]")
        keep = questionary.confirm("  Keep this setting?", default=True).ask()
        if keep or keep is None:
            return

    _console.print(f"  Default location: [bold]{default}[/bold]")
    _console.print("  [dim]CodePlane stores config, database, and logs here.[/dim]")
    _console.print()

    use_default = questionary.confirm("  Use the default location?", default=True).ask()

    if use_default or use_default is None:
        tower_dir = default
    else:
        tower_dir = questionary.path(
            "  Enter custom path:",
            default=default,
            only_directories=True,
        ).ask()
        if not tower_dir:
            tower_dir = default
        tower_dir = str(Path(tower_dir).expanduser().resolve())

    Path(tower_dir).mkdir(parents=True, exist_ok=True)

    if tower_dir != default:
        _console.print()
        _console.print("  [yellow]To persist this across sessions:[/yellow]")
        instructions = _get_env_persistence_instructions("CODEPLANE_HOME", tower_dir)
        for line in instructions.split("\n"):
            _console.print(f"    [dim]{line}[/dim]")

        os.environ["CODEPLANE_HOME"] = tower_dir
    else:
        _console.print(f"  Using: [bold]{tower_dir}[/bold]")


def _setup_dependencies() -> None:
    """Step 2: Check and optionally install system deps."""
    _step_header(2, 6, "System Dependencies")

    all_ok = True
    for dep in DEPENDENCIES:
        found, version = _check_command(dep.command)
        if found:
            _console.print(f"  [green]✓[/green]  {dep.name}: {version}")
            continue

        all_ok = False
        if dep.required:
            _console.print(f"  [red]✗[/red]  {dep.name}: not found [red](required)[/red]")
        else:
            _console.print(f"  [yellow]![/yellow]  {dep.name}: not found [dim](optional)[/dim]")

        if dep.auto_install_cmd and _SYSTEM in dep.auto_install_cmd:
            should_install = questionary.confirm(
                f"    Attempt automatic installation of {dep.name}?",
                default=dep.required,
            ).ask()
            if should_install:
                success = _try_auto_install(dep)
                if success:
                    found2, version2 = _check_command(dep.command)
                    if found2:
                        _console.print(f"  [green]✓[/green]  {dep.name}: {version2}")
                        continue
                # Show manual fallback
                _show_manual_instructions(dep)
            else:
                _show_manual_instructions(dep)
        else:
            _show_manual_instructions(dep)

    if all_ok:
        _console.print("  [green]All dependencies found![/green]")


def _show_manual_instructions(dep: Dependency) -> None:
    """Show OS-specific manual installation instructions."""
    key = _SYSTEM
    instructions = dep.install_instructions.get(key, dep.install_instructions.get("linux", ""))
    _console.print()
    _console.print(f"  [yellow]Manual install for {dep.name}:[/yellow]")
    for line in instructions.split("\n"):
        _console.print(f"    [dim]{line}[/dim]")
    _console.print(f"    [dim]More info: {dep.url}[/dim]")


def _setup_gh_auth() -> None:
    """Step 3: GitHub CLI authentication."""
    _step_header(3, 6, "GitHub Authentication")

    if not shutil.which("gh"):
        _console.print("  [dim]⊘ GitHub CLI not installed — skipping auth setup.[/dim]")
        return

    ok, detail = _check_gh_auth()
    if ok:
        _console.print(f"  [green]✓[/green]  {detail}")
        return

    _console.print("  [red]✗[/red]  GitHub CLI is not authenticated.")
    _console.print()

    if _IS_WSL:
        _console.print("  [dim]WSL detected — browser-based auth may not work directly.[/dim]")
        _console.print()
        method = questionary.select(
            "  How would you like to authenticate?",
            choices=[
                questionary.Choice("Personal access token (recommended for WSL)", value="token"),
                questionary.Choice("Browser auth via Windows host", value="browser"),
                questionary.Choice("Skip for now", value="skip"),
            ],
        ).ask()

        if method == "token":
            _console.print()
            _console.print("  [cyan]Steps:[/cyan]")
            _console.print("    1. Go to https://github.com/settings/tokens")
            _console.print("    2. Create a token with [bold]repo[/bold] and [bold]read:org[/bold] scopes")
            _console.print("    3. Run: [cyan]echo '<TOKEN>' | gh auth login --with-token[/cyan]")
            _console.print()
            return
        elif method == "browser":
            cmd = ["gh", "auth", "login", "-w"]
        elif method == "skip" or method is None:
            _console.print("  [dim]Skipped — run 'gh auth login' later.[/dim]")
            return
        else:
            return
    else:
        should_login = questionary.confirm("  Attempt 'gh auth login' now?", default=True).ask()
        if not should_login:
            _console.print("  [dim]Skipped — run 'gh auth login' later.[/dim]")
            return
        cmd = ["gh", "auth", "login"]

    try:
        subprocess.run(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _console.print(f"  [red]Auth attempt failed: {exc}[/red]")

    ok2, detail2 = _check_gh_auth()
    if ok2:
        _console.print(f"  [green]✓[/green]  {detail2}")
    else:
        _console.print("  [yellow]![/yellow]  Auth not completed. Run [cyan]gh auth login[/cyan] later.")


def _setup_devtunnel_auth() -> None:
    """Step 4: Dev Tunnel authentication."""
    _step_header(4, 6, "Dev Tunnel Authentication")

    if not shutil.which("devtunnel"):
        _console.print("  [dim]⊘ Dev Tunnel CLI not installed — skipping.[/dim]")
        _console.print("  [dim]  (Not required unless you use 'cpl up --tunnel')[/dim]")
        return

    ok, _detail = _check_devtunnel_login()
    if ok:
        _console.print("  [green]✓[/green]  Dev Tunnel is logged in.")
        return

    _console.print("  [red]✗[/red]  Dev Tunnel is not logged in.")
    _console.print()

    if _IS_WSL:
        _console.print("  [dim]WSL detected — browser-based login may not work directly.[/dim]")
        _console.print()
        method = questionary.select(
            "  How would you like to log in?",
            choices=[
                questionary.Choice("Device code flow (recommended for WSL)", value="device"),
                questionary.Choice("GitHub token", value="github"),
                questionary.Choice("Skip for now", value="skip"),
            ],
        ).ask()

        if method == "device":
            cmd = ["devtunnel", "user", "login", "-d"]
        elif method == "github":
            cmd = ["devtunnel", "user", "login", "-g"]
        elif method == "skip" or method is None:
            _console.print("  [dim]Skipped — run 'devtunnel user login' later.[/dim]")
            return
        else:
            return
    else:
        should_login = questionary.confirm("  Attempt login now?", default=True).ask()
        if not should_login:
            _console.print("  [dim]Skipped — run 'devtunnel user login' later.[/dim]")
            return
        cmd = ["devtunnel", "user", "login"]

    try:
        subprocess.run(cmd, timeout=120)
    except (subprocess.TimeoutExpired, OSError) as exc:
        _console.print(f"  [red]Login attempt failed: {exc}[/red]")

    ok2, _ = _check_devtunnel_login()
    if ok2:
        _console.print("  [green]✓[/green]  Dev Tunnel is now logged in!")
    else:
        _console.print("  [yellow]![/yellow]  Login not completed. Run [cyan]devtunnel user login[/cyan] later.")


def _setup_sdk() -> None:
    """Step 5: Agent SDK selection and credential check."""
    _step_header(5, 6, "Agent SDK")

    # Check what's available
    copilot_ok, copilot_detail = _check_sdk_copilot()
    claude_ok, claude_detail = _check_sdk_claude()

    _console.print("  Available SDKs:")
    if copilot_ok:
        _console.print(f"    [green]✓[/green]  Copilot — {copilot_detail}")
    else:
        _console.print(f"    [yellow]![/yellow]  Copilot — {copilot_detail}")

    if claude_ok:
        _console.print(f"    [green]✓[/green]  Claude  — {claude_detail}")
    else:
        _console.print(f"    [yellow]![/yellow]  Claude  — {claude_detail}")
    _console.print()

    # Build choices
    choices = [
        questionary.Choice("copilot — GitHub Copilot", value="copilot"),
        questionary.Choice("claude  — Anthropic Claude Code", value="claude"),
    ]

    # Read current default
    config = load_config()
    current_default = config.runtime.default_sdk

    sdk_choice = questionary.select(
        "  Which SDK should be the default?",
        choices=choices,
        default=f"{'copilot — GitHub Copilot' if current_default == 'copilot' else 'claude  — Anthropic Claude Code'}",
    ).ask()

    if sdk_choice is None:
        sdk_choice = current_default

    # SDK-specific guidance
    if sdk_choice == "copilot" and not copilot_ok:
        _console.print()
        _console.print("  [yellow]Copilot SDK needs authentication.[/yellow]")
        _console.print("  [dim]Ensure GitHub CLI is authenticated: gh auth login[/dim]")
    elif sdk_choice == "claude" and not claude_ok:
        _console.print()
        _console.print("  [yellow]Claude SDK needs an API key.[/yellow]")
        _console.print("  [dim]Set ANTHROPIC_API_KEY in your environment or ~/.codeplane/.env[/dim]")

    # Save choice to config
    if sdk_choice != current_default:
        config.runtime.default_sdk = sdk_choice
        save_config(config)
        _console.print()
        _console.print(f"  [green]✓[/green]  Default SDK set to [bold]{sdk_choice}[/bold]")
    else:
        _console.print()
        _console.print(f"  [green]✓[/green]  Default SDK: [bold]{sdk_choice}[/bold] (unchanged)")


def _setup_config() -> None:
    """Step 6: Config initialization."""
    _step_header(6, 6, "Configuration")

    if DEFAULT_CONFIG_PATH.exists():
        _console.print(f"  [green]✓[/green]  Config exists at [bold]{DEFAULT_CONFIG_PATH}[/bold]")
    else:
        path = init_config()
        _console.print(f"  [green]✓[/green]  Created [bold]{path}[/bold]")

    config = load_config()
    _console.print()
    _console.print("  Key settings:")
    _console.print(f"    server.port:             [bold]{config.server.port}[/bold]")
    _console.print(f"    runtime.default_sdk:     [bold]{config.runtime.default_sdk}[/bold]")
    _console.print(f"    runtime.max_concurrent:  [bold]{config.runtime.max_concurrent_jobs}[/bold]")
    _console.print(f"    completion.strategy:     [bold]{config.completion.strategy}[/bold]")
    _console.print()
    _console.print(f"  [dim]Edit: {DEFAULT_CONFIG_PATH}[/dim]")


# ---------------------------------------------------------------------------
# Legacy compat — keep preflight_check for any external callers
# ---------------------------------------------------------------------------


def preflight_check(*, verbose: bool = True) -> bool:
    """Quick non-interactive check of required dependencies.

    Returns True if all required deps are present.
    Deprecated: use run_preflight() or run_doctor() instead.
    """
    results = run_checks()
    if verbose:
        render_checks(results)
    return not any(r.status == CheckStatus.fail for r in results)
