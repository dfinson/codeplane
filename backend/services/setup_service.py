"""Interactive setup and dependency checker for CodePlane.

Provides a questionnaire-based onboarding flow that:
- Checks system dependencies (node, gh CLI, devtunnel)
- Offers to install missing deps (auto + manual fallback)
- Configures CODEPLANE_HOME (with OS-specific persistence instructions)
- Handles gh CLI auth and devtunnel login (with WSL-aware headless flow)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import click

from backend.config import init_config

# ---------------------------------------------------------------------------
# Dependency descriptors
# ---------------------------------------------------------------------------

_IS_WSL = bool(os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"))
_SYSTEM = platform.system().lower()  # "linux", "darwin", "windows"


@dataclass
class Dependency:
    name: str
    command: str
    install_instructions: dict[str, str]
    url: str
    auto_install_cmd: dict[str, list[str]] | None = None


DEPENDENCIES: list[Dependency] = [
    Dependency(
        name="Node.js",
        command="node",
        url="https://nodejs.org/",
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
        name="GitHub CLI",
        command="gh",
        url="https://cli.github.com/",
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
# Helpers
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


def _check_gh_auth() -> bool:
    """Check if gh CLI is authenticated."""
    try:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _check_devtunnel_login() -> bool:
    """Check if devtunnel is logged in."""
    try:
        result = subprocess.run(
            ["devtunnel", "user", "show"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _try_auto_install(dep: Dependency) -> bool:
    """Attempt auto-installation of a dependency. Returns True on success."""
    if not dep.auto_install_cmd or _SYSTEM not in dep.auto_install_cmd:
        return False

    cmd = dep.auto_install_cmd[_SYSTEM]
    click.echo(f"  Attempting: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode == 0:
            return True
        click.secho(f"  Auto-install failed (exit {result.returncode})", fg="red")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[:5]:
                click.echo(f"    {line}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        click.secho(f"  Auto-install failed: {exc}", fg="red")
    return False


def _show_manual_instructions(dep: Dependency) -> None:
    """Show OS-specific manual installation instructions."""
    key = _SYSTEM
    instructions = dep.install_instructions.get(key, dep.install_instructions.get("linux", ""))
    click.echo()
    click.secho(f"  Manual installation for {dep.name}:", fg="yellow")
    click.echo()
    for line in instructions.split("\n"):
        click.echo(f"    {line}")
    click.echo(f"\n    More info: {dep.url}")
    click.echo()


def _get_env_persistence_instructions(var_name: str, value: str) -> str:
    """Return OS-specific instructions for persisting an env var across sessions."""
    if _SYSTEM == "darwin":
        shell = os.environ.get("SHELL", "/bin/zsh")
        rc = "~/.zshrc" if "zsh" in shell else "~/.bash_profile"
        return f'Add this line to {rc}:\n  export {var_name}="{value}"\n\nThen run: source {rc}'
    elif _SYSTEM == "windows":
        return (
            f"Run in PowerShell (Admin):\n"
            f'  [System.Environment]::SetEnvironmentVariable("{var_name}", "{value}", "User")\n\n'
            f"Or via Settings > System > Advanced > Environment Variables"
        )
    else:  # Linux / WSL
        shell = os.environ.get("SHELL", "/bin/bash")
        if "zsh" in shell:
            rc = "~/.zshrc"
        elif "fish" in shell:
            return f'Run:\n  set -Ux {var_name} "{value}"\n\nThis persists across sessions automatically.'
        else:
            rc = "~/.bashrc"
        return f'Add this line to {rc}:\n  export {var_name}="{value}"\n\nThen run: source {rc}'


# ---------------------------------------------------------------------------
# Main setup flow
# ---------------------------------------------------------------------------


def run_setup() -> None:
    """Run the interactive setup questionnaire."""
    click.echo()
    click.secho("╔══════════════════════════════════════╗", fg="cyan")
    click.secho("║       CodePlane — Initial Setup          ║", fg="cyan")
    click.secho("╚══════════════════════════════════════╝", fg="cyan")
    click.echo()

    # --- Step 1: CODEPLANE_HOME ---
    _setup_tower_home()

    # --- Step 2: System dependencies ---
    _check_dependencies()

    # --- Step 3: gh CLI auth ---
    _setup_gh_auth()

    # --- Step 4: devtunnel login ---
    _setup_devtunnel_login()

    # --- Step 5: Config init ---
    _setup_config()

    click.echo()
    click.secho("✓ Setup complete! Run 'cpl up' to start the server.", fg="green", bold=True)
    click.echo()


def _setup_tower_home() -> None:
    """Configure CODEPLANE_HOME directory."""
    click.secho("1. Data Directory", fg="cyan", bold=True)
    click.echo()

    current = os.environ.get("CODEPLANE_HOME")
    default = str(Path.home() / ".codeplane")

    if current:
        click.echo(f"  CODEPLANE_HOME is set to: {current}")
        if click.confirm("  Keep this setting?", default=True):
            return

    click.echo(f"  Default location: {default}")
    click.echo("  CodePlane stores config, database, and logs here.")
    click.echo()

    use_default = click.confirm("  Use the default location?", default=True)

    if use_default:
        tower_dir = default
    else:
        tower_dir = click.prompt("  Enter custom path", type=str).strip()
        tower_dir = str(Path(tower_dir).expanduser().resolve())

    # Create the directory
    Path(tower_dir).mkdir(parents=True, exist_ok=True)

    if tower_dir != default:
        click.echo()
        click.secho("  To persist this across sessions:", fg="yellow")
        instructions = _get_env_persistence_instructions("CODEPLANE_HOME", tower_dir)
        for line in instructions.split("\n"):
            click.echo(f"    {line}")
        click.echo()

        # Set for the current process
        os.environ["CODEPLANE_HOME"] = tower_dir
    else:
        Path(tower_dir).mkdir(parents=True, exist_ok=True)
        click.echo(f"  Using: {tower_dir}")

    click.echo()


def _check_dependencies() -> None:
    """Check and optionally install system dependencies."""
    click.secho("2. System Dependencies", fg="cyan", bold=True)
    click.echo()

    all_ok = True
    for dep in DEPENDENCIES:
        found, version = _check_command(dep.command)
        if found:
            click.secho(f"  ✓ {dep.name}: {version}", fg="green")
        else:
            click.secho(f"  ✗ {dep.name}: not found", fg="red")
            all_ok = False

            optional = dep.name == "Dev Tunnel CLI"
            severity = "(optional)" if optional else "(required)"
            click.echo(f"    {dep.name} is {severity} for CodePlane.")

            if click.confirm("    Attempt automatic installation?", default=not optional):
                success = _try_auto_install(dep)
                if success:
                    found2, version2 = _check_command(dep.command)
                    if found2:
                        click.secho(f"  ✓ {dep.name}: {version2}", fg="green")
                        continue
                _show_manual_instructions(dep)
                if not optional:
                    click.secho("    Please install manually and re-run 'cpl setup'.", fg="yellow")
            else:
                _show_manual_instructions(dep)

    if all_ok:
        click.secho("  All dependencies found!", fg="green")
    click.echo()


def _setup_gh_auth() -> None:
    """Set up GitHub CLI authentication."""
    click.secho("3. GitHub CLI Authentication", fg="cyan", bold=True)
    click.echo()

    if not shutil.which("gh"):
        click.secho("  ⊘ GitHub CLI not installed — skipping auth setup.", fg="yellow")
        click.echo()
        return

    if _check_gh_auth():
        click.secho("  ✓ GitHub CLI is authenticated.", fg="green")
        click.echo()
        return

    click.secho("  ✗ GitHub CLI is not authenticated.", fg="red")
    click.echo()

    if _IS_WSL:
        click.echo("  WSL detected — browser-based auth may not work directly.")
        click.echo("  Options:")
        click.echo()
        click.secho("    Option A (recommended): Use a personal access token:", fg="yellow")
        click.echo("      1. Go to https://github.com/settings/tokens")
        click.echo("      2. Create a token with 'repo' and 'read:org' scopes")
        click.echo("      3. Run: echo '<YOUR_TOKEN>' | gh auth login --with-token")
        click.echo()
        click.secho("    Option B: Browser auth via Windows host:", fg="yellow")
        click.echo("      Run: gh auth login -w")
        click.echo("      Then open the URL shown in your Windows browser.")
        click.echo()
    else:
        click.echo("  Options:")
        click.echo()
        click.secho("    Interactive login:", fg="yellow")
        click.echo("      Run: gh auth login")
        click.echo()

    if click.confirm("  Attempt 'gh auth login' now?", default=not _IS_WSL):
        cmd = ["gh", "auth", "login"]
        if _IS_WSL:
            cmd.extend(["-w"])  # web-based, gives a code to enter in browser
        try:
            subprocess.run(cmd, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            click.secho(f"  Auth attempt failed: {exc}", fg="red")

        if _check_gh_auth():
            click.secho("  ✓ GitHub CLI is now authenticated!", fg="green")
        else:
            click.secho("  ✗ Auth not completed. You can retry with 'gh auth login'.", fg="yellow")

    click.echo()


def _setup_devtunnel_login() -> None:
    """Set up Dev Tunnel authentication."""
    click.secho("4. Dev Tunnel Authentication", fg="cyan", bold=True)
    click.echo()

    if not shutil.which("devtunnel"):
        click.secho("  ⊘ Dev Tunnel CLI not installed — skipping.", fg="yellow")
        click.echo("    (Not required unless you want remote access via 'cpl up --tunnel')")
        click.echo()
        return

    if _check_devtunnel_login():
        click.secho("  ✓ Dev Tunnel is logged in.", fg="green")
        click.echo()
        return

    click.secho("  ✗ Dev Tunnel is not logged in.", fg="red")
    click.echo()

    if _IS_WSL:
        click.echo("  WSL detected — browser-based login may not work directly.")
        click.echo()
        click.secho("  Option A (recommended): Device code flow:", fg="yellow")
        click.echo("    Run: devtunnel user login -d")
        click.echo("    Then open the URL in your Windows browser and enter the code.")
        click.echo()
        click.secho("  Option B: GitHub token:", fg="yellow")
        click.echo("    Run: devtunnel user login -g")
        click.echo("    (Uses GitHub auth if already configured)")
        click.echo()
    else:
        click.echo("  Run: devtunnel user login")
        click.echo()

    if click.confirm("  Attempt login now?", default=not _IS_WSL):
        cmd = ["devtunnel", "user", "login"]
        if _IS_WSL:
            cmd.append("-d")  # device code flow for headless environments
        try:
            subprocess.run(cmd, timeout=120)
        except (subprocess.TimeoutExpired, OSError) as exc:
            click.secho(f"  Login attempt failed: {exc}", fg="red")

        if _check_devtunnel_login():
            click.secho("  ✓ Dev Tunnel is now logged in!", fg="green")
        else:
            click.secho("  ✗ Login not completed. You can retry with 'devtunnel user login'.", fg="yellow")

    click.echo()


def _setup_config() -> None:
    """Initialize CodePlane config if not present."""
    click.secho("5. Configuration", fg="cyan", bold=True)
    click.echo()

    from backend.config import DEFAULT_CONFIG_PATH

    if DEFAULT_CONFIG_PATH.exists():
        click.secho(f"  ✓ Config exists at {DEFAULT_CONFIG_PATH}", fg="green")
    else:
        click.echo(f"  No config found. Creating default at {DEFAULT_CONFIG_PATH}")
        path = init_config()
        click.secho(f"  ✓ Created {path}", fg="green")

    click.echo()


# ---------------------------------------------------------------------------
# Non-interactive dependency check (for `cpl up` pre-flight)
# ---------------------------------------------------------------------------


def preflight_check(*, verbose: bool = True) -> bool:
    """Quick non-interactive check of required dependencies.

    Returns True if all required deps are present.
    """
    ok = True
    for dep in DEPENDENCIES:
        found, version = _check_command(dep.command)
        is_optional = dep.name == "Dev Tunnel CLI"
        if found:
            if verbose:
                click.secho(f"  ✓ {dep.name}: {version}", fg="green")
        elif is_optional:
            if verbose:
                click.secho(f"  ⊘ {dep.name}: not found (optional)", fg="yellow")
        else:
            if verbose:
                click.secho(f"  ✗ {dep.name}: not found", fg="red")
            ok = False
    return ok
