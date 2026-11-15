"""Interactive setup wizard for CodePlane (``cpl setup``).

Guides users through first-time configuration: data directory, system
dependencies, agent CLIs, and config file creation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import questionary
from rich.panel import Panel

from backend import config as backend_config
from backend.config import init_config, load_config, save_config
from backend.services.setup.checks import (
    DEPENDENCIES,
    HOST_PLATFORM,
    Dependency,
    _check_command,
    _check_gh_auth,
    _console,
    check_agent_cli,
)


def execute_setup_wizard() -> None:
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
    _console.print("    [cyan]cpl up --remote[/cyan]          Start with remote access")
    _console.print("    [cyan]cpl up --dev[/cyan]             Start in dev mode (hot-reload)")
    _console.print("    [cyan]cpl doctor[/cyan]               Check everything without starting")
    _console.print()


_SETUP_TOTAL_STEPS = 4


def _step_header(num: int, total: int, title: str) -> None:
    """Print a step header."""
    _console.print()
    _console.rule(f"[bold cyan]Step {num} of {total} · {title}[/bold cyan]", style="dim")
    _console.print()


def _get_env_persistence_instructions(var_name: str, value: str) -> str:
    """Return OS-specific instructions for persisting an env var."""
    if HOST_PLATFORM == "darwin":
        shell = os.environ.get("SHELL", "/bin/zsh")
        rc = "~/.zshrc" if "zsh" in shell else "~/.bash_profile"
        return f'Add to {rc}:\n  export {var_name}="{value}"\nThen run: source {rc}'
    elif HOST_PLATFORM == "windows":
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


def _try_auto_install(dep: Dependency) -> bool:
    """Attempt auto-installation of a dependency. Returns True on success."""
    if not dep.auto_install_cmd or HOST_PLATFORM not in dep.auto_install_cmd:
        return False

    cmd = dep.auto_install_cmd[HOST_PLATFORM]
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


def _setup_home() -> None:
    """Step 1: Configure CODEPLANE_HOME directory."""
    _step_header(1, _SETUP_TOTAL_STEPS, "Data Directory")

    current = os.environ.get("CODEPLANE_HOME")
    default = str(Path.home() / ".codeplane")

    if current:
        _console.print(f"  CODEPLANE_HOME is set to: [bold]{current}[/bold]")
        keep = questionary.confirm("  Keep this setting?", default=True).ask()
        if keep is not False:
            return

    _console.print(f"  Default location: [bold]{default}[/bold]")
    _console.print("  [dim]CodePlane stores config, database, and logs here.[/dim]")
    _console.print()

    use_default = questionary.confirm("  Use the default location?", default=True).ask()

    if use_default is not False:
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

        if dep.auto_install_cmd and HOST_PLATFORM in dep.auto_install_cmd:
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
    key = HOST_PLATFORM
    instructions = dep.install_instructions.get(key, dep.install_instructions.get("linux", ""))
    _console.print()
    _console.print(f"  [yellow]Manual install for {dep.name}:[/yellow]")
    for line in instructions.split("\n"):
        _console.print(f"    [dim]{line}[/dim]")
    _console.print(f"    [dim]More info: {dep.url}[/dim]")


def _setup_agent_clis() -> None:
    """Step 3: Agent CLI availability check and default selection."""
    _step_header(3, _SETUP_TOTAL_STEPS, "Agent CLIs")

    config = load_config()
    if config.runtime.copilot_only:
        copilot = check_agent_cli("copilot")
        _console.print(f"  GitHub Copilot: {copilot.detail}")
        _console.print("  Copilot-only policy is enabled. Other integrations and stored defaults are preserved.")
        return
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

    config_path = backend_config.get_codeplane_dir() / "config.yaml"
    if config_path.exists():
        _console.print(f"  [green]✓[/green]  Config exists at [bold]{config_path}[/bold]")
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
    _console.print(f"  [dim]Edit: {config_path}[/dim]")
