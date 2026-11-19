"""Check infrastructure, verification engine, and rendering for setup/preflight/doctor.

This module contains the shared verification engine used by ``cpl up``,
``cpl setup``, and ``cpl doctor``.  It defines the check result model,
low-level probes, and Rich rendering helpers.

Dependency descriptors live in ``setup_dependencies.py``.
"""

from __future__ import annotations

import errno
import os
import platform
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass
from enum import StrEnum

from rich.console import Console

from backend import config as backend_config
from backend.services.setup.dependencies import (  # noqa: F401 — re-exported
    DEPENDENCIES,
    HOST_PLATFORM,
    Dependency,
)

__all__ = ["HOST_PLATFORM", "DEPENDENCIES", "Dependency"]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

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


def _check_server_running(host: str, port: int) -> tuple[bool, str]:
    """Probe the /health endpoint, falling back to a real TCP-listener check.

    Returns (running, detail).  The detail string includes version/uptime when
    the health endpoint is reachable, or PID info when only the process is found.

    A process merely *matching* ``cpl up``/``cpl restart`` in its command line
    (see ``find_cpl_processes``) is NOT sufficient evidence on its own — stale
    or orphaned processes can match without actually holding the port, which
    previously caused false "already running" conflicts during ``cpl up``
    preflight. We only trust the process scan once we've confirmed something
    is actually listening on the port.
    """
    import json
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    # 1. Try the health endpoint (definitive when reachable)
    req = Request(f"http://{host}:{port}/health", method="GET")
    try:
        with urlopen(req, timeout=2) as resp:  # noqa: S310
            body = json.loads(resp.read())
            version = body.get("version", "?")
            uptime = int(body.get("uptimeSeconds", 0))
            active = body.get("activeJobs", 0)
            queued = body.get("queuedJobs", 0)
            parts = [f"v{version}", f"uptime {uptime}s"]
            if active or queued:
                parts.append(f"{active} active, {queued} queued")
            return True, ", ".join(parts)
    except (URLError, OSError, ValueError):
        pass

    # 2. Require an actual listening socket before trusting anything else.
    if not _port_is_listening(port):
        return False, "not reachable"

    # 3. Something is listening but /health didn't respond — enrich with
    # process detail when we can attribute it to a cpl process.
    pids = find_cpl_processes()
    if pids:
        pids_str = ", ".join(str(p) for p in pids)
        return True, f"process detected (PID {pids_str}) but /health not reachable"

    return True, "port in use but /health not reachable"


def _windows_ancestor_pids() -> set[int]:
    """Return the current process's PID plus every ancestor's PID (Windows).

    ``find_cpl_processes``'s WMIC query matches on command-line substrings
    ("cpl" + "up"/"restart"), which the *current* invocation of ``cpl up``/
    ``cpl restart`` itself always satisfies (its own command line literally
    contains those words), as do its uv/venv-python ancestors. Without this
    exclusion, ``find_cpl_processes`` would report the running invocation's
    own process tree as "another" cpl process — harmless for the read-only
    preflight detail message, but actively dangerous for ``_stop_server``
    (``cli.py``), which sends real signals to every PID it returns: during
    ``cpl restart`` that would mean killing the restart command's own
    process tree mid-flight. Mirrors the equivalent ancestor-walk already
    done on the POSIX branch below.
    """
    exclude: set[int] = set()
    pid_to_ppid: dict[int, int] = {}
    try:
        result = subprocess.run(
            ["wmic", "process", "get", "ProcessId,ParentProcessId"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.splitlines()
        if lines:
            header = lines[0].split()
            if "ProcessId" in header and "ParentProcessId" in header:
                pid_idx = header.index("ProcessId")
                ppid_idx = header.index("ParentProcessId")
                for line in lines[1:]:
                    parts = line.split()
                    if len(parts) <= max(pid_idx, ppid_idx):
                        continue
                    if parts[pid_idx].isdigit() and parts[ppid_idx].isdigit():
                        pid_to_ppid[int(parts[pid_idx])] = int(parts[ppid_idx])
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # Fall through — exclude will still contain at least our own PID.

    ancestor = os.getpid()
    while ancestor and ancestor not in exclude:
        exclude.add(ancestor)
        ancestor = pid_to_ppid.get(ancestor, 0)
    return exclude


def find_cpl_processes() -> list[int]:
    """Return PIDs of running ``cpl up`` / ``cpl restart`` processes (cross-platform)."""
    pids: list[int] = []
    _system = platform.system()

    if _system == "Windows":
        # WMIC is available on all supported Windows versions
        try:
            result = subprocess.run(
                [
                    "wmic",
                    "process",
                    "where",
                    "CommandLine like '%cpl%up%' or CommandLine like '%cpl%restart%'",
                    "get",
                    "ProcessId",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            candidate_pids: list[int] = []
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    candidate_pids.append(int(line))
            if candidate_pids:
                ancestor_pids = _windows_ancestor_pids()
                pids = [p for p in candidate_pids if p not in ancestor_pids]
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
    else:
        # POSIX (Linux, macOS, BSD)
        try:
            result = subprocess.run(
                ["ps", "axo", "pid,ppid,args"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Build exclude set: walk the full ancestor chain so we never
            # detect our own process tree (make → sh → uv → python cpl up).
            pid_to_ppid: dict[int, int] = {}
            for line in result.stdout.splitlines():
                parts = line.split(None, 2)
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    pid_to_ppid[int(parts[0])] = int(parts[1])

            exclude: set[int] = set()
            ancestor = os.getpid()
            while ancestor and ancestor not in exclude:
                exclude.add(ancestor)
                ancestor = pid_to_ppid.get(ancestor, 0)

            for line in result.stdout.splitlines():
                parts = line.split(None, 2)
                if len(parts) < 3 or not parts[0].isdigit():
                    continue
                pid = int(parts[0])
                args_lower = parts[2].lower()
                if (
                    ("cpl up" in args_lower or "cpl restart" in args_lower)
                    and "doctor" not in args_lower
                    and pid not in exclude
                ):
                    pids.append(pid)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    return pids


def _port_is_listening(port: int) -> bool:
    """True if something is actively accepting TCP connections on this port (loopback).

    This is the only reliable cross-platform signal that a real listener owns
    the port — unlike command-line/process-name matching (``find_cpl_processes``),
    it can't produce false positives from stale or unrelated processes.
    """
    probe_targets: list[tuple[int, str]] = [(socket.AF_INET, "127.0.0.1")]
    if socket.has_ipv6:
        probe_targets.append((socket.AF_INET6, "::1"))

    for family, host in probe_targets:
        try:
            with socket.socket(family, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                if probe.connect_ex((host, port)) == 0:
                    return True
        except OSError:
            continue
    return False


def _check_port(port: int) -> tuple[bool, str]:
    """Check if a port is available. Returns (available, detail)."""
    if _port_is_listening(port):
        return False, "in use"

    refused_errnos = {
        0,
        errno.ECONNREFUSED,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EADDRNOTAVAIL,
    }

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
            return True, "available"
    except OSError as exc:
        if exc.errno in refused_errnos:
            return True, "available"
        return False, "unavailable"


@dataclass
class AgentAuthStatus:
    sdk_id: str
    authenticated: bool | None
    detail: str
    hint: str = ""


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


def check_agent_auth(sdk_id: str) -> AgentAuthStatus:
    """Best-effort auth status for agent CLIs.

    This is advisory only. Unknown status should not be treated as a failure.
    """
    if sdk_id == "copilot":
        if shutil.which("gh") is None:
            return AgentAuthStatus(sdk_id, None, "GitHub CLI not available")
        ok, detail = _check_gh_auth()
        if ok:
            return AgentAuthStatus(sdk_id, True, detail)
        return AgentAuthStatus(sdk_id, False, detail, "Run: gh auth login")

    if sdk_id == "claude":
        if shutil.which("claude") is None:
            return AgentAuthStatus(sdk_id, None, "claude CLI not available")
        try:
            # --text: human-readable output (JSON is the default since CLI ~2.x).
            # Sample authenticated output:
            #   Login method: Claude Pro Account
            #   Organization: user@example.com's Organization
            #   Email: user@example.com
            # Sample unauthenticated: exit code 1, "Not logged in" on stderr.
            result = subprocess.run(
                ["claude", "auth", "status", "--text"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return AgentAuthStatus(sdk_id, None, "Unable to determine auth status")

        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        lowered = output.lower()

        if result.returncode == 0:
            # Extract email from "Email: user@example.com" line.
            email = ""
            for line in lines:
                if line.lower().startswith("email:"):
                    email = line.split(":", 1)[1].strip()
                    break
            detail = f"Logged in as {email}" if email else "authenticated"
            return AgentAuthStatus(sdk_id, True, detail)

        if "not logged in" in lowered or "login required" in lowered:
            detail = lines[0] if lines else "not authenticated"
            return AgentAuthStatus(sdk_id, False, detail, "Run: claude auth login")
        return AgentAuthStatus(sdk_id, None, lines[0] if lines else "Unable to determine auth status")

    return AgentAuthStatus(sdk_id, None, "Unknown agent")


def _build_agent_check_result(sdk_id: str) -> CheckResult:
    cli = check_agent_cli(sdk_id)
    if not cli.ready:
        return CheckResult(
            cli.name,
            CheckStatus.warn,
            cli.detail,
            hint=cli.hint,
            category="agent",
        )

    auth = check_agent_auth(sdk_id)
    if auth.authenticated is False:
        return CheckResult(
            cli.name,
            CheckStatus.warn,
            f"{cli.detail} (not authenticated)",
            hint=auth.hint,
            category="agent",
        )
    if auth.authenticated is None:
        return CheckResult(
            cli.name,
            CheckStatus.warn,
            f"{cli.detail} (auth unknown)",
            hint=auth.hint or "Unable to verify authentication",
            category="agent",
        )

    return CheckResult(cli.name, CheckStatus.passed, auth.detail, category="agent")


def check_agent_cli(sdk_id: str) -> AgentCLIStatus:
    """Unified check for an agent CLI.

    Used by preflight, setup wizard, and the /api/sdks endpoint.
    SDKs are pre-packaged with CodePlane so import checks are skipped;
    only CLI reachability is verified.  Auth is checked separately.
    """
    if sdk_id == "copilot":
        # Copilot uses the gh CLI as its entry point.
        cli_reachable = shutil.which("gh") is not None
        ready = cli_reachable
        if ready:
            detail = "gh CLI installed"
            hint = ""
        else:
            detail = "gh CLI not found"
            hint = "Install: https://cli.github.com/"
        return AgentCLIStatus("copilot", "GitHub Copilot", True, cli_reachable, ready, detail, hint)

    if sdk_id == "claude":
        cli_reachable = shutil.which("claude") is not None
        ready = cli_reachable
        if ready:
            detail = "claude CLI installed"
            hint = ""
        else:
            detail = "claude CLI not on PATH"
            hint = "Install CLI: npm install -g @anthropic-ai/claude-code"
        return AgentCLIStatus("claude", "Claude Code", True, cli_reachable, ready, detail, hint)

    return AgentCLIStatus(sdk_id, sdk_id, False, False, False, "unknown agent", "")


# ---------------------------------------------------------------------------
# Shared verification engine
# ---------------------------------------------------------------------------


def verify_requirements(
    *,
    port: int | None = None,
    include_optional_dependencies: bool = True,
    preflight: bool = False,
) -> list[CheckResult]:
    """Run all preflight checks and return structured results.

    Parameters
    ----------
    port:
        If given, also checks whether the port is available.
    include_optional_dependencies:
        Whether to include optional tools like the Dev Tunnels CLI in the dependency list.
    preflight:
        When True (called before ``cpl up``), only check for conflicting
        processes and port availability instead of reporting server health.
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
        if not include_optional_dependencies and not dep.required:
            continue
        found, version = _check_command(dep.command)
        if found:
            results.append(CheckResult(dep.name, CheckStatus.passed, version or "installed", category="deps"))
        elif dep.required:
            hint = dep.install_instructions.get(HOST_PLATFORM, dep.install_instructions.get("linux", ""))
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
    agent_results: list[CheckResult] = []
    for sdk_id in ("copilot", "claude"):
        from backend.config import load_config
        from backend.services.adapters.integration_policy import IntegrationPolicy

        if not IntegrationPolicy(load_config()).enabled(sdk_id):
            continue
        agent_results.append(_build_agent_check_result(sdk_id))
    results.extend(agent_results)

    # At least one agent CLI must be authenticated.
    any_agent_ready = any(r.status == CheckStatus.passed for r in agent_results)
    if not any_agent_ready:
        results.append(
            CheckResult(
                "Agent Auth",
                CheckStatus.fail,
                "no authenticated agent CLI",
                hint="At least one agent CLI must be authenticated.\n"
                "Run: gh auth login (GitHub Copilot) or claude auth login (Claude Code)",
                category="agent",
            )
        )

    # --- Environment ---
    config_path = backend_config.get_codeplane_dir() / "config.yaml"
    if config_path.exists():
        results.append(CheckResult("Config", CheckStatus.passed, str(config_path), category="env"))
    else:
        results.append(
            CheckResult(
                "Config",
                CheckStatus.warn,
                "not found",
                hint=f"Will be created at {config_path}",
                category="env",
            )
        )

    if port is not None:
        running, run_detail = _check_server_running("127.0.0.1", port)

        if preflight:
            # Preflight: we're about to start — only care about conflicts.
            if running:
                results.append(
                    CheckResult(
                        f"Server (:{port})",
                        CheckStatus.warn,
                        f"already running — {run_detail}",
                        hint="Another instance may conflict. Stop it first: cpl down",
                        category="env",
                    )
                )
            else:
                # Not running — expected; just check port is free.
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
        else:
            # Doctor / status: report full server health.
            if running:
                results.append(
                    CheckResult(
                        f"Server (:{port})",
                        CheckStatus.passed,
                        f"running — {run_detail}",
                        category="env",
                    )
                )
            else:
                results.append(
                    CheckResult(
                        f"Server (:{port})",
                        CheckStatus.warn,
                        "not running",
                        hint="Start with: cpl up",
                        category="env",
                    )
                )

                # Only check port availability when CodePlane isn't running —
                # otherwise we'd falsely report the port as "in use".
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
