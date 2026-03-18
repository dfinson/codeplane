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
from typing import Any

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


@dataclass
class AgentCLIStatus:
    """Result of checking whether an agent CLI is usable."""

    sdk_id: str
    name: str
    installed: bool  # Python package importable
    cli_reachable: bool  # CLI binary on PATH (or package acts as entry point)
    ready: bool  # both installed and reachable
    detail: str  # human-readable summary
    hint: str  # actionable suggestion, empty when ready


def check_agent_cli(sdk_id: str) -> AgentCLIStatus:
    """Unified check for an agent CLI.

    Used by preflight, setup wizard, and the /api/sdks endpoint.
    Does NOT verify auth — that is the CLI's responsibility.
    """
    if sdk_id == "copilot":
        try:
            import copilot  # noqa: F401

            installed = True
        except ImportError:
            installed = False
        # Copilot SDK is the entry point (no separate binary)
        cli_reachable = installed
        ready = installed
        if ready:
            ver = getattr(copilot, "__version__", "installed")
            detail = f"github-copilot-sdk {ver}"
            hint = ""
        else:
            detail = "not installed"
            hint = "Install: uv add github-copilot-sdk"
        return AgentCLIStatus("copilot", "GitHub Copilot", installed, cli_reachable, ready, detail, hint)

    if sdk_id == "claude":
        try:
            import claude_code_sdk  # noqa: F401

            installed = True
        except ImportError:
            installed = False
        cli_reachable = shutil.which("claude") is not None
        ready = installed and cli_reachable
        if ready:
            detail = "claude CLI and SDK installed"
            hint = ""
        elif cli_reachable and not installed:
            detail = "claude CLI found, Python SDK missing"
            hint = "Install: uv add claude-code-sdk"
        elif installed and not cli_reachable:
            detail = "Python SDK installed, claude CLI not on PATH"
            hint = "Install CLI: npm install -g @anthropic-ai/claude-code"
        else:
            detail = "not installed"
            hint = "Install CLI: npm install -g @anthropic-ai/claude-code\nInstall SDK: uv add claude-code-sdk"
        return AgentCLIStatus("claude", "Claude Code", installed, cli_reachable, ready, detail, hint)

    return AgentCLIStatus(sdk_id, sdk_id, False, False, False, "unknown agent", "")


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

    # --- Agent CLIs ---
    for sdk_id in ("copilot", "claude"):
        cli = check_agent_cli(sdk_id)
        if cli.ready:
            results.append(CheckResult(cli.name, CheckStatus.passed, cli.detail, category="agent"))
        else:
            results.append(
                CheckResult(
                    cli.name,
                    CheckStatus.warn,
                    cli.detail,
                    hint=cli.hint,
                    category="agent",
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
            ("Agent CLIs", "agent"),
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
# Inline fix helpers (used by preflight)
# ---------------------------------------------------------------------------

# Map (category, label-substring) → shell commands that can fix the issue.
_INLINE_FIX_COMMANDS: dict[str, list[str]] = {
    "claude_cli": ["npm", "install", "-g", "@anthropic-ai/claude-code"],
    "claude_sdk": ["uv", "add", "claude-code-sdk"],
    "copilot_sdk": ["uv", "add", "github-copilot-sdk"],
}


def _prompt_select(choices: list[questionary.Choice]) -> Any:  # noqa: ANN401
    """Present a selection prompt styled to match Rich preflight output.

    Uses a blank qmark, leading-space message, and the ``pointer`` style
    so the choices line up with the Rich check lines (2-space base indent).
    """
    return questionary.select(
        message="",
        qmark="",
        instruction="",
        pointer="  →",
        choices=choices,
    ).ask()


def _offer_inline_fix(warning: CheckResult) -> bool:
    """Offer to fix a single preflight warning in-place.

    Returns True if the issue was resolved.
    """
    # Determine which fix(es) apply
    fixes: list[tuple[str, list[str]]] = []

    if warning.category == "agent":
        cli = check_agent_cli("copilot" if "Copilot" in warning.label else "claude")
        if cli.sdk_id == "claude":
            if not cli.cli_reachable:
                fixes.append(("Install claude CLI", _INLINE_FIX_COMMANDS["claude_cli"]))
            if not cli.installed:
                fixes.append(("Install claude-code-sdk", _INLINE_FIX_COMMANDS["claude_sdk"]))
        elif cli.sdk_id == "copilot" and not cli.installed:
            fixes.append(("Install github-copilot-sdk", _INLINE_FIX_COMMANDS["copilot_sdk"]))

    if not fixes:
        # No automated fix available — just ask continue/abort
        choice = _prompt_select(
            [
                questionary.Choice("Continue anyway", value="continue"),
                questionary.Choice("Abort", value="abort"),
            ]
        )
        if choice == "abort" or choice is None:
            raise SystemExit(1)
        return False

    # Offer to run the fix
    fix_choices = [questionary.Choice(f"Fix now  {' '.join(cmd)}", value=("fix", cmd)) for _label, cmd in fixes]
    fix_choices.append(questionary.Choice("Skip", value=("skip", [])))
    fix_choices.append(questionary.Choice("Abort", value=("abort", [])))

    choice = _prompt_select(fix_choices)

    if choice is None or choice[0] == "abort":
        raise SystemExit(1)
    if choice[0] == "skip":
        return False

    # Attempt the fix
    _, cmd = choice
    _console.print(f"       [dim]Running {' '.join(cmd)} …[/dim]")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return True
        _console.print(f"       [red]Failed (exit {result.returncode})[/red]")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[:3]:
                _console.print(f"       [dim]{line}[/dim]")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        _console.print(f"       [red]Failed: {exc}[/red]")

    # Auto-fix failed — give manual instructions and a recheck option
    _console.print()
    _console.print("       [yellow]Could not install automatically.[/yellow]")
    _console.print("       [dim]Fix it in another terminal:[/dim]")
    for _label, fix_cmd in fixes:
        _console.print(f"       [cyan]{' '.join(fix_cmd)}[/cyan]")
    _console.print()

    retry = _prompt_select(
        [
            questionary.Choice("I've fixed it — recheck", value="recheck"),
            questionary.Choice("Continue anyway", value="continue"),
            questionary.Choice("Abort", value="abort"),
        ]
    )

    if retry == "abort" or retry is None:
        raise SystemExit(1)
    if retry == "recheck":
        if warning.category == "agent":
            rechecked = check_agent_cli("copilot" if "Copilot" in warning.label else "claude")
            if rechecked.ready:
                return True
            _console.print(f"       [yellow]Still not resolved: {rechecked.detail}[/yellow]")
        return False
    # "continue"
    return False


# ---------------------------------------------------------------------------
# cpl up — preflight
# ---------------------------------------------------------------------------


def run_preflight(port: int) -> bool:
    """Interactive preflight for ``cpl up``.

    Returns True if the server can start.
    On warnings, pauses to let the user fix issues or continue.
    """
    results = run_checks(port=port)

    _console.print()
    _console.print("  [bold]Preflight[/bold]")
    _console.print()
    for r in results:
        _render_check_line(r)

    has_fail = any(r.status == CheckStatus.fail for r in results)
    warnings = [r for r in results if r.status == CheckStatus.warn]

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

    if warnings:
        _console.print()
        _console.print(f"  [yellow bold]{len(warnings)} issue{'s' if len(warnings) != 1 else ''} found:[/yellow bold]")

        for w in warnings:
            _console.print()
            _console.print(f"    [yellow]![/yellow]  [bold]{w.label}[/bold]: {w.detail}")
            if w.hint:
                for line in w.hint.split("\n"):
                    _console.print(f"       → {line}")

            resolved = _offer_inline_fix(w)
            if resolved:
                _console.print(f"    [green]✓[/green]  {w.label}: fixed")

        # Re-check for any remaining hard failures after fixes
        results = run_checks(port=port)
        if any(r.status == CheckStatus.fail for r in results):
            _console.print()
            _console.print("  [red bold]Cannot start — fix the errors above.[/red bold]")
            return False

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

    # Step 3: Agent CLIs
    _setup_agent_clis()

    # Step 4: Config
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


_SETUP_TOTAL_STEPS = 4


def _setup_home() -> None:
    """Step 1: Configure CODEPLANE_HOME directory."""
    _step_header(1, _SETUP_TOTAL_STEPS, "Data Directory")

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
    _step_header(2, _SETUP_TOTAL_STEPS, "System Dependencies")

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


def _setup_agent_clis() -> None:
    """Step 3: Agent CLI availability check and default selection."""
    _step_header(3, _SETUP_TOTAL_STEPS, "Agent CLIs")

    copilot = check_agent_cli("copilot")
    claude = check_agent_cli("claude")

    _console.print("  Available agents:")
    for cli in (copilot, claude):
        if cli.ready:
            _console.print(f"    [green]✓[/green]  {cli.name} — {cli.detail}")
        else:
            _console.print(f"    [yellow]![/yellow]  {cli.name} — {cli.detail}")
            if cli.hint:
                for line in cli.hint.split("\n"):
                    _console.print(f"         [dim]→ {line}[/dim]")
    _console.print()

    # Build choices
    choices = [
        questionary.Choice("copilot — GitHub Copilot", value="copilot"),
        questionary.Choice("claude  — Anthropic Claude Code", value="claude"),
    ]

    config = load_config()
    current_default = config.runtime.default_sdk

    sdk_choice = questionary.select(
        "  Which agent should be the default?",
        choices=choices,
        default=current_default,
    ).ask()

    if sdk_choice is None:
        sdk_choice = current_default

    # Show auth hints (not errors — auth is the CLI's job)
    chosen = copilot if sdk_choice == "copilot" else claude
    if not chosen.ready:
        _console.print()
        _console.print(f"  [yellow]{chosen.name} is not fully installed yet.[/yellow]")
        if chosen.hint:
            for line in chosen.hint.split("\n"):
                _console.print(f"    [dim]→ {line}[/dim]")
    elif sdk_choice == "copilot":
        # Hint about gh auth — Copilot SDK needs it at runtime
        gh_ok, _ = _check_gh_auth() if shutil.which("gh") else (False, "")
        if not gh_ok:
            _console.print()
            _console.print("  [dim]Hint: Copilot requires GitHub CLI auth. Run: gh auth login[/dim]")
    elif sdk_choice == "claude":
        _console.print()
        _console.print(
            "  [dim]Hint: Authenticate the Claude CLI if you haven't already "
            "(e.g. claude auth login, or set credentials per your org's method).[/dim]"
        )

    if sdk_choice != current_default:
        config.runtime.default_sdk = sdk_choice
        save_config(config)
        _console.print()
        _console.print(f"  [green]✓[/green]  Default agent set to [bold]{sdk_choice}[/bold]")
    else:
        _console.print()
        _console.print(f"  [green]✓[/green]  Default agent: [bold]{sdk_choice}[/bold] (unchanged)")


def _setup_config() -> None:
    """Step 4: Config initialization."""
    _step_header(4, _SETUP_TOTAL_STEPS, "Configuration")

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
