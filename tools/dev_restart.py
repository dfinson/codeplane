#!/usr/bin/env python3
"""
dev_restart.py — Graceful, native CodePlane self-restart for developers.

Prepares a restart while the current server stays available (frontend build,
backend compile/import preflight, active-launch-profile and secret
re-validation), then hands execution to a detached helper that survives both
this process and the CodePlane server it replaces. See
``_bmad-output/planning-artifacts/architecture/architecture-codeplane-self-restart-2026-08-07/SOLUTION-DESIGN.md``
for the full parent/helper design.

Usage:
    uv run python tools/dev_restart.py [--source PATH] [--adoption-seconds N] ...

The script exits non-zero on any preparation, spawn, or adoption failure. In
every such case the current server keeps running untouched — pause and stop
only ever happen inside the detached helper, after adoption succeeds.
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.services.dev_restart.launch_profile import LaunchProfile, SecretSource
    from backend.services.dev_restart.restart_protocol import RestartRequestPaths, RestartTimeouts

if sys.platform == "win32":
    # Windows consoles default sys.stdout/stderr to the legacy ANSI code page
    # (e.g. CP1252), not UTF-8. This script prints Unicode glyphs like "✓",
    # which raise UnicodeEncodeError under that default. Reconfigure here so
    # the script works regardless of the user's console code page (mirrors
    # the same fix in backend/main.py).
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

# Running ``python tools/dev_restart.py`` sets ``sys.path[0]`` to the tools
# directory, not the repository root. Put the checkout root on sys.path so the
# script and the detached helper can import ``backend.*`` without depending on
# the caller to have launched it as a module.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Marker files a directory must contain to be treated as a native CodePlane
# checkout (AC 1) — cheap, no-import structural check before any subprocess
# or profile validation runs against it.
_CHECKOUT_MARKERS = ("backend/app_factory.py", "pyproject.toml")


class DevRestartError(Exception):
    """Any parent-mode preparation failure. Always leaves the current server running."""


# ---------------------------------------------------------------------------
# Target source resolution and validation (AC 1)
# ---------------------------------------------------------------------------


def resolve_target_source_root(source: str | None) -> Path:
    """Resolve ``--source`` to an absolute native path, defaulting to the
    repository containing this invoked script (Story 1.2 AC 1). Refuses a
    path that does not look like a CodePlane checkout before any build,
    preflight, or profile validation is attempted against it.
    """
    root = Path(source).expanduser().resolve() if source else REPO_ROOT
    missing = [marker for marker in _CHECKOUT_MARKERS if not (root / marker).is_file()]
    if missing:
        raise DevRestartError(f"{root} does not look like a CodePlane checkout (missing: {', '.join(missing)})")
    return root


# ---------------------------------------------------------------------------
# Secret re-resolution (AC 4) — never serializes or logs a secret value
# ---------------------------------------------------------------------------


def _dotenv_value(key: str, source_root: Path) -> str | None:
    """Mirror `cpl up`'s .env-then-environment precedence, resolved against
    *source_root* (the target being restarted into), not this process's own
    environment file.
    """
    import os

    dotenv_path = source_root / ".env"
    if dotenv_path.is_file():
        for line in dotenv_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key_part, _, value_part = stripped.partition("=")
                if key_part.strip() == key:
                    value = value_part.strip()
                    if value:
                        return value
    return os.environ.get(key) or None


def ensure_secret_resolvable(source: SecretSource, target_source_root: Path, *, label: str) -> None:
    """Re-resolve *source* against the target checkout without ever reading,
    serializing, or logging its actual value (Story 1.2 Dev Notes).

    ``unreplayable`` is already refused unconditionally by
    ``validate_launch_profile()`` (AD-5) before this is ever called; the
    branch here exists only so this function fails closed if ever invoked
    on its own. ``resolvable`` sources are checked for *current*
    resolvability -- a stale or since-revoked reference must fail before
    outage, not merely be well-formed.
    """
    if source.kind == "not_required":
        return
    if source.kind == "unreplayable":
        raise DevRestartError(f"{label} is not replayable; restart refused")

    if source.provider == "environment":
        if not source.reference or not _dotenv_value(source.reference, target_source_root):
            raise DevRestartError(
                f"{label} references environment variable {source.reference!r}, which cannot be resolved "
                f"from {target_source_root} (.env or process environment)"
            )
        return
    if source.provider == "provider-login":
        if source.reference == "devtunnel" and shutil.which("devtunnel") is None:
            raise DevRestartError(f"{label} requires the 'devtunnel' CLI, which was not found on PATH")
        return
    raise DevRestartError(f"{label} has an unrecognized provider {source.provider!r}; restart refused")


def get_cached_access_token(profile: LaunchProfile) -> str:
    """Read an existing operator credential, without invoking login or logging output."""
    from backend.services.dev_restart.launch_profile import validate_launch_profile
    from backend.services.dev_restart.restart_protocol import validate_access_origin, validate_access_token

    validate_launch_profile(profile, require_replayable_secrets=True)
    origin = validate_access_origin(profile.tunnel_origin)
    executable = shutil.which("cloudflared")
    if not executable:
        raise DevRestartError("access_token_executable_unavailable")
    try:
        result = subprocess.run(
            [executable, "access", "token", "--app", origin],
            capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        raise DevRestartError("access_token_retrieval_failed") from None
    if result.returncode or not result.stdout.strip():
        raise DevRestartError("access_token_cache_unavailable: operator login required before retry")
    # Strip only the CLI's optional trailing line ending, not embedded whitespace.
    return validate_access_token(result.stdout.rstrip("\r\n"))


# ---------------------------------------------------------------------------
# Backend compile/import preflight (AC 3)
# ---------------------------------------------------------------------------


def run_backend_preflight(executable: str, target_source_root: Path) -> None:
    """Run ``compileall`` over ``backend``/``tools``, then import
    ``backend.app_factory``, using the recorded active executable directly
    against *target_source_root* -- no dependency sync, no runtime mutation
    (Story 1.2 AC 3).
    """
    compile_result = subprocess.run(  # noqa: S603 - fixed argv, recorded native executable
        [executable, "-m", "compileall", "-q", "backend", "tools"],
        cwd=str(target_source_root),
        capture_output=True,
        text=True,
    )
    if compile_result.returncode != 0:
        detail = (compile_result.stdout + compile_result.stderr).strip()
        raise DevRestartError(f"backend preflight failed: compileall over backend/tools reported errors:\n{detail}")

    import_result = subprocess.run(  # noqa: S603 - fixed argv, recorded native executable
        [executable, "-c", "import backend.app_factory"],
        cwd=str(target_source_root),
        capture_output=True,
        text=True,
    )
    if import_result.returncode != 0:
        detail = (import_result.stdout + import_result.stderr).strip()
        raise DevRestartError(f"backend preflight failed: could not import backend.app_factory:\n{detail}")


# ---------------------------------------------------------------------------
# Frontend build (AC 2)
# ---------------------------------------------------------------------------


def _resolve_npm_command() -> str:
    """Return the platform-specific npm executable path."""
    command = "npm.cmd" if platform.system() == "Windows" else "npm"
    resolved = shutil.which(command)
    if resolved is None:
        raise FileNotFoundError(
            f"npm is required to build the frontend but {command!r} was not found on PATH. "
            "Install Node.js and ensure npm is available."
        )
    return resolved


def build_frontend(frontend_dir: Path = FRONTEND_DIR) -> bool:
    """Run `npm run build` in *frontend_dir* (defaults to this checkout's
    frontend). Streams output live. Runs while the current server remains
    available (Story 1.2 AC 2) -- always before helper spawn, pause, or stop.
    """
    npm = _resolve_npm_command()
    if not (frontend_dir / "node_modules").is_dir():
        install = subprocess.run([npm, "ci"], cwd=frontend_dir)
        if install.returncode != 0:
            return False
    result = subprocess.run([npm, "run", "build"], cwd=frontend_dir)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Pending-request write (AC 5)
# ---------------------------------------------------------------------------


def write_pending_request(
    request_id: str,
    target_source_root: Path,
    profile: LaunchProfile,
    timeouts: RestartTimeouts,
) -> RestartRequestPaths:
    """Atomically write the secret-free ``<id>.pending.json`` request (AC 5).

    Contains only the request ID, validated native target source, the
    already secret-free launch profile, and phase timeouts -- no credential
    values, per the wire contract in
    ``backend.services.dev_restart.restart_protocol``.
    """
    from backend.services.dev_restart.restart_protocol import get_request_paths, write_json_atomic

    paths = get_request_paths(request_id)
    payload = {
        "requestId": request_id,
        "targetSourceRoot": str(target_source_root),
        "launchProfile": profile.to_dict(),
        "timeouts": timeouts.to_dict(),
        "createdAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_json_atomic(paths.pending, payload)
    return paths


# ---------------------------------------------------------------------------
# Parent-mode orchestration (Story 1.2 + handoff to the Story 1.3 helper)
# ---------------------------------------------------------------------------


def _resolve_timeouts(args: argparse.Namespace) -> RestartTimeouts:
    from backend.services.dev_restart.restart_protocol import RestartTimeouts

    defaults = RestartTimeouts()
    return RestartTimeouts(
        adoption_seconds=args.adoption_seconds if args.adoption_seconds is not None else defaults.adoption_seconds,
        response_grace_seconds=(
            args.response_grace_seconds if args.response_grace_seconds is not None else defaults.response_grace_seconds
        ),
        pause_wait_seconds=(
            args.pause_wait_seconds if args.pause_wait_seconds is not None else defaults.pause_wait_seconds
        ),
        stop_seconds=args.stop_seconds if args.stop_seconds is not None else defaults.stop_seconds,
        readiness_seconds=(
            args.readiness_seconds if args.readiness_seconds is not None else defaults.readiness_seconds
        ),
        remote_probe_seconds=(
            args.remote_probe_seconds if args.remote_probe_seconds is not None else defaults.remote_probe_seconds
        ),
    )


def prepare_restart_request(args: argparse.Namespace) -> tuple[RestartRequestPaths, str, RestartTimeouts]:
    """Run every preparation step (AC 1-5) in the required order and write
    the pending request. Raises ``DevRestartError``/the underlying
    launch-profile or restart-protocol error on any failure -- callers must
    not send pause or stop after a raised exception (AC 6).
    """
    from backend.services.dev_restart.launch_profile import (
        LaunchProfileError,
        load_active_profile,
        validate_launch_profile,
    )

    timeouts = _resolve_timeouts(args)
    target_source_root = resolve_target_source_root(args.source)

    try:
        profile = load_active_profile()
        validate_launch_profile(profile, require_replayable_secrets=True)
    except LaunchProfileError as exc:
        raise DevRestartError(f"active launch profile is invalid or stale: {exc}") from exc

    ensure_secret_resolvable(profile.password_source, target_source_root, label="password source")
    ensure_secret_resolvable(profile.tunnel_credential_source, target_source_root, label="tunnel credential source")

    print(f"[1/4] Building the frontend at {target_source_root}…")
    if not build_frontend(target_source_root / "frontend"):
        raise DevRestartError("frontend build failed")
    print("  ✓ Frontend build succeeded.")

    print("[2/4] Running backend compile/import preflight…")
    run_backend_preflight(profile.executable, target_source_root)
    print("  ✓ Preflight succeeded.")

    request_id = uuid.uuid4().hex
    print(f"[3/4] Writing restart request {request_id}…")
    paths = write_pending_request(request_id, target_source_root, profile, timeouts)

    return paths, request_id, timeouts


def run_parent(args: argparse.Namespace) -> int:
    """Full parent-mode flow: prepare (AC 1-6), then hand off to the
    detached helper (Story 1.3) and wait for adoption. Returns the process
    exit code.
    """
    from backend.services.dev_restart.launch_profile import LaunchProfile, LaunchProfileError
    from backend.services.dev_restart.restart_helper import HandoffProcess, await_adoption, spawn_detached_helper
    from backend.services.dev_restart.restart_protocol import (
        RestartProtocolError,
        get_restart_log_path,
        read_json_file,
        rotate_restart_log_if_needed,
    )

    try:
        paths, request_id, timeouts = prepare_restart_request(args)
        profile = LaunchProfile.from_dict(read_json_file(paths.pending).get("launchProfile"))
        handoff = None
        if profile.effective_auth_mode == "access_only":
            handoff = {
                "version": 1, "kind": "credential", "requestId": request_id,
                "serverPid": profile.started_pid, "serverProcessTime": profile.started_process_time,
                "origin": profile.tunnel_origin, "jwt": get_cached_access_token(profile),
            }
        log_path = get_restart_log_path()
        rotate_restart_log_if_needed(log_path)
    except (DevRestartError, RestartProtocolError, LaunchProfileError) as exc:
        print(f"\n✗ Restart preparation failed: {exc}\n  The current server has NOT been restarted.\n", file=sys.stderr)
        return 1
    print(f"[4/4] Spawning detached restart helper (log: {log_path})…")
    try:
        with open(log_path, "a", encoding="utf-8") as log_handle:
            if handoff is not None:
                try:
                    helper = spawn_detached_helper(
                        Path(sys.executable), Path(__file__).resolve(), paths.pending, log_handle,
                        handoff=handoff, adoption_seconds=timeouts.adoption_seconds,
                    )
                finally:
                    handoff.clear()
            else:
                helper = spawn_detached_helper(
                    Path(sys.executable), Path(__file__).resolve(), paths.pending, log_handle
                )
    except (OSError, RestartProtocolError):
        print(
            "\nRestart handoff failed before commit. The current server has NOT been restarted.",
            file=sys.stderr,
        )
        return 1

    helper_pid = helper.pid if isinstance(helper, HandoffProcess) else helper
    print(f"  Helper spawned (PID {helper_pid}); waiting up to {timeouts.adoption_seconds:.0f}s for adoption…")
    try:
        adopted = (
            helper.commit(paths) if isinstance(helper, HandoffProcess)
            else await_adoption(paths, request_id, timeouts.adoption_seconds)
        )
    except (OSError, RestartProtocolError):
        adopted = False
    if not adopted:
        print(
            f"\n✗ Helper did not adopt request {request_id} within {timeouts.adoption_seconds:.0f}s.\n"
            f"  The current server has NOT been restarted. Check the log: {log_path}\n",
            file=sys.stderr,
        )
        return 1

    print(f"\n✓ Helper adopted request {request_id}; restart is continuing in the background.")
    print(f"  Follow progress in the log: {log_path}\n")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Native CodePlane self-restart for developers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source",
        default=None,
        metavar="PATH",
        help="Native path to the target CodePlane source (default: the repository containing this script)",
    )
    parser.add_argument("--adoption-seconds", type=float, default=None, help="Override the adoption-wait timeout")
    parser.add_argument(
        "--response-grace-seconds", type=float, default=None, help="Override the helper's response-grace timeout"
    )
    parser.add_argument("--pause-wait-seconds", type=float, default=None, help="Override the pause-wait timeout")
    parser.add_argument("--stop-seconds", type=float, default=None, help="Override the old-process stop timeout")
    parser.add_argument("--readiness-seconds", type=float, default=None, help="Override the readiness-wait timeout")
    parser.add_argument(
        "--remote-probe-seconds", type=float, default=None, help="Override the remote-origin probe timeout"
    )
    # Private mode: not a public command (SPEC AD-9). Only this script's own
    # detached spawn (backend.services.dev_restart.restart_helper.spawn_detached_helper)
    # invokes it with --helper <request-path>; a developer never passes it
    # directly. Kept out of --help via argparse.SUPPRESS.
    parser.add_argument("--helper", metavar="REQUEST_PATH", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.helper is not None:
        from backend.services.dev_restart.restart_helper import run_helper

        sys.exit(run_helper(Path(args.helper)))

    sys.exit(run_parent(args))


if __name__ == "__main__":
    main()
