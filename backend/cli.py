"""CLI entry point for CodePlane (``cpl`` command group).

Contains the Click command group and all sub-commands (up, version, setup,
doctor, down, restart) along with tunnel management and startup helpers.
"""

from __future__ import annotations

import atexit
import contextlib
import multiprocessing
import signal
import socket
import warnings
from pathlib import Path
from typing import Any

# Prevent libraries from duplicating the server process.
# The default "fork" start method duplicates the entire parent address space
# for each worker — on a 20-core host this creates 16× copies of a ~1 GB
# process.  "spawn" starts fresh interpreters that only load what they need.
multiprocessing.set_start_method("spawn", force=True)

# Suppress the benign "leaked semaphore objects" warning that fires when the
# server is killed (SIGKILL) rather than gracefully shut down.
warnings.filterwarnings("ignore", message="resource_tracker:.*leaked semaphore")

import click  # noqa: E402
import structlog  # noqa: E402

from backend.config import load_config  # noqa: E402


@click.group()
def cli() -> None:
    """CodePlane — control plane for coding agents."""


def _bind_ephemeral_socket(host: str) -> socket.socket:
    """Bind an OS-assigned port on ``host`` and return the still-open socket.

    Used to resolve ``--port 0`` to a concrete port *before* anything
    downstream needs it. Binding and then closing the socket to "peek" at the
    port would race another process for it, so the bound socket is kept and
    handed to uvicorn via ``server.run(sockets=...)``.

    Mirrors ``uvicorn.Config.bind_socket`` so the socket uvicorn receives is
    configured the way it would have configured one itself.
    """
    family = socket.AF_INET
    with contextlib.suppress(OSError):
        socket.inet_pton(socket.AF_INET6, host)
        family = socket.AF_INET6
    sock = socket.socket(family=family)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, 0))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


# ---------------------------------------------------------------------------
# Frontend build helper
# ---------------------------------------------------------------------------


def _build_frontend() -> bool:
    """Build the frontend if sources are newer than dist/."""
    import shutil
    import subprocess

    # On Windows, npm is actually a "npm.cmd" shim. subprocess.run(["npm", ...])
    # without shell=True calls CreateProcess directly, which does NOT resolve
    # .cmd/.bat shims the way cmd.exe's PATH search does — it fails with
    # "[WinError 2] The system cannot find the file specified" even though
    # `npm` works fine from an interactive shell. shutil.which() resolves the
    # correct executable (npm.cmd on Windows, npm on POSIX) up front.
    npm = shutil.which("npm")
    if npm is None:
        click.secho("Frontend build failed: npm not found on PATH", fg="yellow")
        click.echo("The API will still work, but there will be no web UI.")
        return False

    frontend_root = Path(__file__).resolve().parent.parent / "frontend"
    dist = Path(__file__).resolve().parent / "web" / "index.html"
    package_json = frontend_root / "package.json"
    if not package_json.exists():
        return dist.exists()

    src_roots = [
        frontend_root / "src",
        frontend_root / "public",
    ]
    source_files = [
        package_json,
        frontend_root / "package-lock.json",
        frontend_root / "index.html",
        frontend_root / "vite.config.ts",
    ]
    for root in src_roots:
        if root.exists():
            source_files.extend(path for path in root.rglob("*") if path.is_file())

    # Skip build if dist is up-to-date
    if dist.exists() and source_files:
        dist_mtime = dist.stat().st_mtime
        src_mtime = max(path.stat().st_mtime for path in source_files if path.exists())
        if dist_mtime > src_mtime:
            return True

    click.echo("Building frontend...")
    try:
        # Ensure deps are installed
        if not (frontend_root / "node_modules").is_dir():
            subprocess.run([npm, "ci"], cwd=str(frontend_root), check=True, capture_output=True, timeout=300)
        subprocess.run([npm, "run", "build"], cwd=str(frontend_root), check=True, capture_output=True, timeout=300)
        click.secho("Frontend built.", fg="green")
        return dist.exists()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        click.secho(f"Frontend build failed: {exc}", fg="yellow")
        click.echo("The API will still work, but there will be no web UI.")
        return False


#: Location of the repository ``.env``. Module-level so tests can redirect it
#: to a scratch path instead of silently picking up the developer's real
#: credentials — which changes provider auto-detection and, since the values
#: are exported, leaks into ``os.environ`` for the rest of the session.
DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"


#: ``.env`` keys that are exported into ``os.environ``. Deliberately an
#: allowlist: consumers outside this module build subprocess environments from
#: ``os.environ`` (pty terminals in ``terminal_service``, the restart helper,
#: every agent CLI), so exporting the whole file would push unrelated developer
#: credentials into every child process CodePlane spawns.
_EXPORTED_DOTENV_KEYS = frozenset(
    {
        "CPL_CLOUDFLARE_TUNNEL_TOKEN",
        "CPL_CLOUDFLARE_HOSTNAME",
        "CPL_CF_ACCESS_TEAM",
        "CPL_CF_ACCESS_AUD",
        "CPL_DEVTUNNEL_NAME",
        "CPL_PASSWORD",
    }
)


def _load_and_export_dotenv(dotenv_path: Path) -> dict[str, str]:
    """Parse ``.env`` and export its remote-access entries into ``os.environ``.

    Returns the parsed mapping so callers can resolve values themselves.

    The export matters because several consumers read ``os.environ``
    directly and cannot see a local mapping: ``backend.lifespan``'s
    Cloudflare Access check derives the account ID from
    ``CPL_CLOUDFLARE_TUNNEL_TOKEN`` in ``os.environ``, so a ``.env``-only
    setup silently lost the Zero Trust API verification strategy, and the
    restart helper replays ``cpl up`` with ``env=dict(os.environ)``, which
    dropped the tunnel credentials entirely when the replacement process ran
    outside the repository directory.

    Only :data:`_EXPORTED_DOTENV_KEYS` are exported, and ``setdefault`` keeps
    an explicit shell export authoritative — which is also the precedence
    ``_env`` applies, so the CLI, the lifespan hooks and the restart helper all
    resolve the same value when the two sources disagree.
    """
    import os

    dotenv_vars: dict[str, str] = {}
    if not dotenv_path.is_file():
        return dotenv_vars

    for raw_line in dotenv_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        dotenv_vars[key.strip()] = value.strip()

    for key, value in dotenv_vars.items():
        if key in _EXPORTED_DOTENV_KEYS:
            os.environ.setdefault(key, value)

    return dotenv_vars


def _provider_explicit() -> bool:
    """Whether ``--provider`` was actually supplied on the command line.

    ``up``'s ``--provider`` has a default of ``devtunnel``, so the parameter
    value cannot distinguish "the user asked for devtunnel" from "nobody said
    anything". Click records where each value came from, which is the only
    reliable signal.
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    from click.core import ParameterSource

    return ctx.get_parameter_source("provider") is ParameterSource.COMMANDLINE


def _neutralize_fatal_termination_signals() -> None:
    """Make a re-raised termination signal survivable so cleanup can run.

    ``uvicorn.Server.run`` captures the termination signals for the duration of
    ``serve()``, then restores whatever handler was installed beforehand and
    re-raises the signal it caught. When that pre-existing handler is the
    default one, the re-raise kills the process *instantly*: ``server.run()``
    never returns, so the ``finally`` block that stops the tunnel connector is
    skipped and the connector is orphaned. ``SIGINT`` is harmless because
    Python's default handler merely raises ``KeyboardInterrupt``, which is why
    Ctrl-C tore down correctly while ``cpl down`` (``SIGTERM``) did not.

    Installing a handler *before* ``server.run()`` means uvicorn restores that
    one instead, and the re-raise becomes survivable. The handler restores the
    default as it fires, so the signal is absorbed exactly once: a second
    ``cpl down`` (or a shutdown-time ``SIGTERM``) still terminates the process
    rather than being ignored forever.
    """

    def _absorb_once(signum: int, _frame: Any) -> None:
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            signal.signal(signum, signal.SIG_DFL)

    # ``backend.lifespan._request_graceful_shutdown`` treats a callable SIGTERM
    # handler as proof that someone will act on the signal. This one does the
    # opposite, so it advertises itself and lets that caller take a hard stop.
    _absorb_once.codeplane_absorbs_signal = True  # type: ignore[attr-defined]

    for name in ("SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            if signal.getsignal(sig) in (signal.SIG_DFL, None):
                signal.signal(sig, _absorb_once)


# ---------------------------------------------------------------------------
# ``cpl up`` — start the server
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Dev mode: skip frontend build")
@click.option("--remote", is_flag=True, help="Enable remote access via a tunnel provider")
@click.option(
    "--provider",
    default="devtunnel",
    type=click.Choice(["devtunnel", "cloudflare"], case_sensitive=False),
    show_default=True,
    help="Remote access provider (requires --remote)",
)
@click.option("--password", default=None, help="Set auth password (auto-generated with --remote)")
@click.option("--access-only", is_flag=True, help="Require Cloudflare Access JWT instead of a password")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --remote)")
@click.option("--tunnel-name", default=None, help="Dev Tunnel name (default: random, reused across restarts)")
@click.option(
    "--tunnel-ownership",
    default=None,
    type=click.Choice(["managed", "external"], case_sensitive=False),
    help=(
        "Explicit remote-connector ownership (managed: CodePlane starts and owns the "
        "connector; external: never scan or spawn, only resolve the recorded origin). "
        "Replayed automatically by restart; not normally set by hand."
    ),
)
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
@click.option("--phone", is_flag=True, help="Shortcut for --remote: enable tunnel + QR code for mobile access")
def up(
    host: str | None,
    port: int | None,
    dev: bool,
    remote: bool,
    provider: str,
    password: str | None,
    access_only: bool,
    no_password: bool,
    tunnel_name: str | None,
    tunnel_ownership: str | None,
    skip_preflight: bool,
    phone: bool,
) -> None:
    """Start the CodePlane server."""
    import structlog
    import uvicorn

    from backend.app_factory import create_app
    from backend.logging_config import setup_logging
    from backend.persistence.database import run_migrations
    from backend.services.sharing.tunnel_service import (
        RemoteProvider,
        TunnelHandle,
        TunnelOwnership,
        TunnelStartError,
        start_remote_access,
        validate_remote_provider,
    )

    log = structlog.get_logger()

    # --phone implies --remote
    if phone:
        remote = True

    config = load_config()
    host = host if host is not None else config.server.host
    # ``port or config.server.port`` would silently discard an explicit
    # ``--port 0``, which is a valid request for an OS-assigned ephemeral port.
    port = port if port is not None else config.server.port

    # Run preflight checks before starting
    if not skip_preflight:
        from backend.services.setup.service import validate_preflight

        if not validate_preflight(port):
            raise SystemExit(1)

    # ``--port 0`` asks the OS to assign an ephemeral port. Resolve it now, by
    # binding the listening socket ourselves and reading back the assigned
    # port, so that every downstream consumer agrees on the port the server
    # actually serves on: the tunnel origin, the banner, the published
    # ``run.json`` (which ``cpl down``/``cpl restart`` read), and the
    # listener-ownership check. Done after preflight, which would otherwise
    # find the port already taken -- by us.
    ephemeral_socket: socket.socket | None = None
    if port == 0:
        try:
            ephemeral_socket = _bind_ephemeral_socket(host)
        except OSError as exc:
            click.secho(f"ERROR: could not bind an ephemeral port on {host}: {exc}", fg="red", err=True)
            raise SystemExit(1) from exc
        port = ephemeral_socket.getsockname()[1]

    if not remote and _provider_explicit():
        click.secho(
            f"ERROR: --provider requires --remote (got --provider {provider} without --remote).",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    # Credentials come from the OS environment first, then ``.env``. The order
    # must match ``_load_and_export_dotenv``'s ``setdefault`` export, otherwise
    # this process and the consumers that read ``os.environ`` (lifespan, the
    # restart helper) would resolve different values when the two disagree.
    import os

    dotenv_vars = _load_and_export_dotenv(DOTENV_PATH)

    def _env(key: str) -> str | None:
        return os.environ.get(key) or dotenv_vars.get(key) or None

    cloudflare_token = _env("CPL_CLOUDFLARE_TUNNEL_TOKEN")
    cloudflare_hostname = _env("CPL_CLOUDFLARE_HOSTNAME")
    tunnel_name = tunnel_name or _env("CPL_DEVTUNNEL_NAME")

    cf_access_team = _env("CPL_CF_ACCESS_TEAM")
    cf_access_aud = _env("CPL_CF_ACCESS_AUD")

    # Auto-detect Cloudflare when --provider wasn't explicitly set but credentials exist.
    # ``provider`` defaults to "devtunnel", so its value alone cannot tell an
    # explicit ``--provider devtunnel`` apart from the default; click's
    # parameter source can. Without this distinction the auto-detect silently
    # overrode a deliberate devtunnel request on any machine that happens to
    # have Cloudflare credentials, and the restart helper — which always
    # replays ``--provider`` explicitly — could not reproduce a recorded
    # devtunnel session.
    if remote and provider == "devtunnel" and cloudflare_token and cloudflare_hostname and not _provider_explicit():
        provider = "cloudflare"

    remote_provider = RemoteProvider(provider) if remote else RemoteProvider.local

    if access_only and (
        not remote
        or remote_provider is not RemoteProvider.cloudflare
        or not cf_access_team
        or not cf_access_aud
        or password is not None
        or _env("CPL_PASSWORD")
    ):
        click.secho(
            "ERROR: --access-only requires --remote --provider cloudflare, CPL_CF_ACCESS_TEAM and "
            "CPL_CF_ACCESS_AUD, and conflicts with an explicit password (including CPL_PASSWORD).",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    from backend.services.auth import cf_access

    cf_access.reset()
    if cf_access_team and cf_access_aud:
        try:
            cf_access.configure(team=cf_access_team, aud=cf_access_aud, eager=False)
        except cf_access.CfAccessConfigError as exc:
            raise click.ClickException(str(exc)) from exc
    elif cf_access_team or cf_access_aud:
        click.secho(
            "WARNING: Both CPL_CF_ACCESS_TEAM and CPL_CF_ACCESS_AUD are required for Access JWT verification.",
            fg="yellow",
            err=True,
        )

    # Password logic: block unsafe combos before checking provider availability
    if remote and no_password:
        click.secho(
            "ERROR: --remote with --no-password is not allowed. Remote access requires authentication.", fg="red"
        )
        raise SystemExit(1)

    if remote:
        error = validate_remote_provider(
            remote_provider,
            cloudflare_token=cloudflare_token,
            cloudflare_hostname=cloudflare_hostname,
        )
        if error:
            click.secho(error, fg="red", err=True)
            raise SystemExit(1)

    # Password priority: --password flag > CPL_PASSWORD env/dotenv > auto-generate for remote
    #
    # ``password_source`` classifies *how* the effective password was
    # resolved -- persisted in the active launch profile (Story 1.1) so
    # restart can refuse to replay a source it cannot resolve again. Never
    # infer replayability from the literal value; classify the branch
    # actually taken by CLI precedence.
    from backend.services.dev_restart.launch_profile import SecretSource

    effective_password: str | None = password
    password_source: SecretSource = SecretSource.not_required()

    if password:
        # Literal --password value has no durable reference to replay.
        password_source = SecretSource.unreplayable()

    if not effective_password and not no_password and not access_only:
        env_pw = _env("CPL_PASSWORD")
        if env_pw:
            effective_password = env_pw
            password_source = SecretSource.resolvable("environment", "CPL_PASSWORD")

    if not effective_password and not no_password and remote and not access_only:
        from backend.services.auth.middleware import generate_password

        effective_password = generate_password()
        password_source = SecretSource.unreplayable()

    # Block unauthenticated binding on all interfaces — validate before migrations
    if host == "0.0.0.0" and no_password:  # noqa: S104
        click.secho(
            "ERROR: --host 0.0.0.0 with --no-password is not allowed. "
            "Binding to all interfaces requires authentication.",
            fg="red",
            err=True,
        )
        raise SystemExit(1)

    # Build frontend (unless --dev, which uses Vite's hot-reload server separately)
    if not dev:
        _build_frontend()

    # Configure logging before everything else so all startup messages are captured.
    # Create the console log now (TTY check) so the log handler can be wired in
    # setup_logging; the banner prints when start() is called later.
    from backend.console_dashboard import ConsoleLog

    dashboard = ConsoleLog.create_if_tty(log_file_path=config.logging.file)
    setup_logging(
        config.logging.file,
        console_level=config.logging.level,
        max_file_size_mb=config.logging.max_file_size_mb,
        backup_count=config.logging.backup_count,
        dashboard=dashboard,
    )

    # Run Alembic migrations before starting the server
    run_migrations()

    # Auto-generate password when binding to all interfaces without one set
    if host == "0.0.0.0" and not effective_password and not access_only:  # noqa: S104
        from backend.services.auth.middleware import generate_password as _gen_pw

        effective_password = _gen_pw()
        password_source = SecretSource.unreplayable()
        click.secho(
            "WARNING: Binding to 0.0.0.0 — password auth auto-enabled.",
            fg="yellow",
            err=True,
        )

    tunnel_origin: str | None = None
    tunnel_handle: TunnelHandle | None = None
    # Closed secret-source classification for the tunnel credential (Story
    # 1.1) -- not_required unless a managed remote connector actually
    # consumed one.
    tunnel_credential_source: SecretSource = SecretSource.not_required()

    if remote:
        try:
            explicit_ownership = TunnelOwnership(tunnel_ownership) if tunnel_ownership else None
            tunnel_handle = start_remote_access(
                remote_provider,
                port=port,
                cloudflare_token=cloudflare_token,
                cloudflare_hostname=cloudflare_hostname,
                tunnel_name=tunnel_name,
                ownership=explicit_ownership,
            )
        except TunnelStartError as exc:
            click.secho(f"ERROR: {exc}", fg="red", err=True)
            raise SystemExit(1) from exc
        # Ownership of the connector begins here, but the ``finally`` that
        # releases it only starts once ``server.run()`` is reached. Anything
        # that aborts in between (a failed migration, a bad DI wiring) would
        # otherwise leave a public tunnel pointing at a server that never came
        # up. ``close()`` is idempotent, so the later explicit calls still win.
        atexit.register(tunnel_handle.close)
        tunnel_origin = tunnel_handle.origin

        if tunnel_handle.externally_managed:
            # CodePlane did not start this connector -- no credential of ours was used.
            tunnel_credential_source = SecretSource.not_required()
        elif remote_provider is RemoteProvider.devtunnel:
            tunnel_credential_source = SecretSource.resolvable("provider-login", "devtunnel")
        elif remote_provider is RemoteProvider.cloudflare:
            tunnel_credential_source = SecretSource.resolvable("environment", "CPL_CLOUDFLARE_TUNNEL_TOKEN")

    auth_mode = "access_only" if access_only else ("password" if effective_password else "local")
    app = create_app(
        dev=dev, tunnel_origin=tunnel_origin, password=effective_password, remote=remote, access_only=access_only
    )

    # Stash banner info so lifespan can print it after services are ready.
    # Also stash the dashboard so lifespan can subscribe it to the EventBus
    # and start the Live display after the banner.
    app.state.banner_args = {
        "host": host,
        "port": port,
        "dev": dev,
        "tunnel_url": tunnel_origin,
        "password": effective_password,
    }
    app.state.dashboard = dashboard
    app.state.tunnel_handle = tunnel_handle

    # Use uvicorn.Server directly so we can patch handle_exit to stop the
    # Rich Live display the instant a signal is received.  uvicorn installs
    # signal handlers via loop.add_signal_handler() which overrides any
    # signal.signal() handlers we set beforehand, so the only reliable way
    # to hook into the signal path is to wrap the Server's own callback.
    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning" if dashboard else "info",
        timeout_graceful_shutdown=45,
    )

    def _publish_active_launch_profile() -> None:
        """Publish the active launch profile once the listener has bound (Story 1.1).

        Called only from ``_LaunchProfileServer.startup()`` after
        ``await super().startup()`` has returned successfully -- i.e. after
        Uvicorn has created the listening socket and completed lifespan
        startup. A write issued any earlier (e.g. immediately before
        ``server.run()``) could publish a profile for a server that then
        fails to bind or fails lifespan startup.
        """
        import sys

        import psutil as _psutil

        from backend.services.dev_restart.launch_profile import (
            LaunchProfileError,
            build_active_launch_profile,
            write_active_launch_profile,
        )

        pid = os.getpid()
        try:
            created_time = _psutil.Process(pid).create_time()
        except _psutil.Error as exc:
            log.error("launch_profile_publish_failed", reason="process_inspection_failed", exc_info=True)
            raise LaunchProfileError("could not inspect own process for launch profile publication") from exc

        # Confirm the exact PID owns the configured port before publishing
        # (reuses the same listener-owner helper `cpl down`/`cpl restart` use
        # -- never a process-name scan).
        if pid not in _find_pids_on_port(port):
            log.error("launch_profile_publish_failed", reason="listener_ownership_unconfirmed", pid=pid, port=port)
            raise LaunchProfileError(f"PID {pid} does not appear to own the listener on port {port}")

        tunnel_ownership: str | None = None
        resolved_tunnel_name: str | None = None
        resolved_tunnel_origin: str | None = None
        resolved_tunnel_origin_reusable: bool | None = None
        if remote:
            externally_managed = tunnel_handle is not None and tunnel_handle.externally_managed
            tunnel_ownership = "external" if externally_managed else "managed"
            resolved_tunnel_name = tunnel_handle.name if tunnel_handle is not None else None
            resolved_tunnel_origin = tunnel_handle.origin if tunnel_handle is not None else None
            # ``origin_is_reusable`` is populated by the remote-recovery tunnel
            # integration (Story 1.6); tolerate its absence with getattr so
            # this story's publication path does not depend on that story's
            # TunnelHandle field landing first.
            resolved_tunnel_origin_reusable = (
                getattr(tunnel_handle, "origin_is_reusable", None) if tunnel_handle is not None else None
            )

        profile = build_active_launch_profile(
            executable=sys.executable,
            working_directory=os.getcwd(),
            host=host,
            port=port,
            dev=dev,
            remote=remote,
            provider=str(remote_provider),
            tunnel_ownership=tunnel_ownership,
            tunnel_name=resolved_tunnel_name,
            tunnel_origin=resolved_tunnel_origin,
            tunnel_origin_reusable=resolved_tunnel_origin_reusable,
            password_source=password_source,
            auth_mode=auth_mode,
            tunnel_credential_source=tunnel_credential_source,
            started_pid=pid,
            started_process_time=created_time,
        )
        try:
            write_active_launch_profile(profile)
        except OSError as exc:
            log.error("launch_profile_publish_failed", reason="write_failed", exc_info=True)
            raise LaunchProfileError("failed to write active launch profile") from exc
        log.info("launch_profile_published", pid=pid, port=port)

    class _LaunchProfileServer(uvicorn.Server):
        """``uvicorn.Server`` subclass that publishes the active launch profile
        immediately after the listener socket is bound and lifespan startup
        completes -- never before ``server.run()`` binds the socket."""

        async def startup(self, sockets: list[Any] | None = None) -> None:
            await super().startup(sockets=sockets)
            if self.started:
                _publish_active_launch_profile()

    server = _LaunchProfileServer(uv_config)

    _neutralize_fatal_termination_signals()

    _exit_signal_count = 0

    _original_handle_exit = server.handle_exit

    def _handle_exit_patched(sig: int, frame: Any) -> None:
        nonlocal _exit_signal_count
        _exit_signal_count += 1
        if _exit_signal_count == 1:
            if dashboard is not None:
                dashboard.stop()
            # Mute the event bus immediately to stop log spam from in-flight events.
            _event_bus = getattr(app.state, "event_bus", None)
            if _event_bus is not None:
                _event_bus.mute()
            # Raise the log level so only critical errors show during teardown.
            import logging

            logging.getLogger().setLevel(logging.CRITICAL)
            click.echo("\nShutting down…")
            _original_handle_exit(sig, frame)

            # Watchdog: if uvicorn hasn't exited within 10s, force-kill.
            import threading

            def _force_exit_watchdog() -> None:
                import os as _os

                if tunnel_handle is not None:
                    tunnel_handle.close()
                _os._exit(0)

            _wd = threading.Timer(10.0, _force_exit_watchdog)
            _wd.daemon = True
            _wd.start()
        else:
            # Second signal: force immediate exit — no more teardown errors.
            if dashboard is not None:
                dashboard.stop()
            if tunnel_handle is not None:
                tunnel_handle.close()
            import os as _os

            _os._exit(0)

    server.handle_exit = _handle_exit_patched  # type: ignore[method-assign]

    # Suppress the KeyboardInterrupt that uvicorn re-raises after shutdown
    # (it restores the default SIGINT handler then calls signal.raise_signal).
    try:
        server.run(sockets=[ephemeral_socket] if ephemeral_socket is not None else None)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if dashboard is not None:
            dashboard.stop()
        if tunnel_handle is not None:
            tunnel_handle.close()
        if ephemeral_socket is not None:
            # Idempotent: uvicorn also closes sockets handed to it.
            with contextlib.suppress(OSError):
                ephemeral_socket.close()


# ---------------------------------------------------------------------------
# Connection info (on-demand via ``cpl info``)
# ---------------------------------------------------------------------------


def _print_connection_info(host: str, port: int, tunnel_url: str | None, password: str | None = None) -> None:
    """Print connection details and QR code on demand."""
    url = tunnel_url or f"http://{host}:{port}"

    try:
        from rich.align import Align
        from rich.console import Console, Group
        from rich.panel import Panel
        from rich.text import Text

        console = Console()
        lines = [f"[bold]Server:[/bold]   http://{host}:{port}"]
        if tunnel_url:
            lines.append(f"[bold]Tunnel:[/bold]   {tunnel_url}")
        if password:
            lines.append(f"[bold]Password:[/bold] {password}")

        qr_section: list[object] = []
        try:
            import io

            import qrcode

            qr = qrcode.QRCode(box_size=1, border=1)
            qr.add_data(url)
            qr.make(fit=True)
            buf = io.StringIO()
            qr.print_ascii(out=buf, invert=True)
            qr_ascii = buf.getvalue().rstrip("\n")

            qr_section.append(Text(""))
            qr_section.append(Align.center(Text(qr_ascii)))
            qr_section.append(Text(""))
            qr_section.append(Align.center(Text.from_markup(f"Scan to open: [bold]{url}[/bold]")))
        except ImportError:
            structlog.get_logger().debug("qrcode_not_installed", package="qrcode", exc_info=True)

        body = Group(Text.from_markup("\n".join(lines)), *qr_section)  # type: ignore[arg-type]
        console.print(Panel(body, title="[bold cyan]CodePlane[/bold cyan]", border_style="cyan"))
    except ImportError:
        click.echo(f"CodePlane server: http://{host}:{port}")
        if tunnel_url:
            click.echo(f"Tunnel: {tunnel_url}")
        if password:
            click.echo(f"Password: {password}")


# ---------------------------------------------------------------------------
# Utility commands
# ---------------------------------------------------------------------------


@cli.command()
def version() -> None:
    """Print CodePlane version."""
    from backend import __version__

    click.echo(f"cpl {__version__}")


@cli.command()
@click.option("--host", default="127.0.0.1", help="Server host")
@click.option("--port", "-p", default=8080, type=int, help="Server port")
@click.option("--tunnel-url", default=None, help="Tunnel URL (auto-detected from config if omitted)")
@click.option("--password", default=None, help="Access password")
def info(host: str, port: int, tunnel_url: str | None, password: str | None) -> None:
    """Print connection details and QR code."""
    _print_connection_info(host=host, port=port, tunnel_url=tunnel_url, password=password)


@cli.command()
def setup() -> None:
    """Interactive setup wizard — check dependencies, configure data directory, authenticate."""
    from backend.services.setup.service import execute_setup_wizard

    execute_setup_wizard()


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Output results as JSON")
def doctor(as_json: bool) -> None:
    """Full non-interactive health check — deps, auth, SDK, environment."""
    from backend.services.setup.service import diagnose_configuration

    ok = diagnose_configuration(as_json=as_json)
    if not ok:
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# ``cpl down`` — gracefully stop the server
# ---------------------------------------------------------------------------


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on the given TCP port (deduplicated)."""
    import platform

    if platform.system() == "Windows":
        return _find_pids_on_port_windows(port)
    return _find_pids_on_port_posix(port)


def _find_pids_on_port_windows(port: int) -> list[int]:
    """Windows: enumerate TCP listeners via psutil (no lsof/ss on this platform)."""
    import psutil

    try:
        conns = psutil.net_connections(kind="tcp")
    except (psutil.Error, OSError):
        # Permission/enumeration issues — treat as "nothing found" rather than
        # crashing the shutdown flow. No traceback spam for an expected race.
        structlog.get_logger().debug("psutil_net_connections_failed", port=port, exc_info=True)
        return []

    pids = [
        conn.pid
        for conn in conns
        if conn.status == psutil.CONN_LISTEN and conn.laddr and conn.laddr.port == port and conn.pid
    ]
    return list(dict.fromkeys(pids))  # dedupe, preserve order


def _find_pids_on_port_posix(port: int) -> list[int]:
    """POSIX: try ``lsof``, then ``ss``. Missing tools are expected — log quietly."""
    import subprocess as _sp

    logger = structlog.get_logger()

    # Try lsof first (most POSIX systems)
    try:
        result = _sp.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
            return list(dict.fromkeys(pids))
    except FileNotFoundError:
        logger.debug("lsof_not_found", port=port)

    # Fallback: ss (Linux)
    try:
        import re

        result = _sp.run(["ss", "-tlnp", f"sport = :{port}"], capture_output=True, text=True)
        pids = [int(p) for p in re.findall(r"pid=(\d+)", result.stdout)]
        return list(dict.fromkeys(pids))
    except FileNotFoundError:
        logger.debug("ss_not_found", port=port)
    except OSError:
        # Genuine unexpected failure (not a missing-command case) — surface it.
        logger.warning("ss_probe_failed", port=port, exc_info=True)

    return []


def _is_server_running(host: str, port: int) -> tuple[bool, list[int]]:
    """Detect a running CodePlane instance.

    Uses the same layered strategy as ``cpl doctor``:
    1. /health endpoint (definitive)
    2. Port-level PID detection (real TCP listeners)

    A process merely *matching* ``cpl up``/``cpl restart`` in its command
    line (``find_cpl_processes``) is NOT sufficient evidence on its own —
    stale or orphaned processes can match without actually holding the port,
    and the match is machine-wide so it can't be trusted to belong to *this*
    port. Trusting that alone previously caused false "already running"
    conflicts. ``_stop_server`` does not consult ``find_cpl_processes`` at
    all — it only ever signals PIDs actually bound to the requested port.

    Returns (running, pids).  *pids* may be empty when detection succeeded
    via health-probe alone — callers that need PIDs should fall back to
    ``_find_pids_on_port``.
    """
    # 1. Health endpoint
    status, _ = _api_get(f"http://{host}:{port}", "/health")
    if status == 200:
        pids = _find_pids_on_port(port)
        return True, pids

    # 2. Port-level detection (definitive real-listener check)
    pids = _find_pids_on_port(port)
    if pids:
        return True, pids

    return False, []


def _api_get(base_url: str, path: str) -> tuple[int, dict[str, Any] | None]:
    """Perform a GET request. Returns (status, body | None)."""
    import json
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{base_url}{path}", method="GET")
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, None
    except (URLError, OSError):
        return 0, None


def _api_post(base_url: str, path: str) -> int:
    """Perform a POST request with no body. Returns the status code (0 on error)."""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    req = Request(f"{base_url}{path}", method="POST", data=b"", headers={"Content-Length": "0"})
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310
            return int(resp.status)
    except (URLError, OSError):
        return 0


def _pause_active_sessions(base_url: str) -> None:
    """Fire pause signals to all running agent sessions via the API."""
    # Collect running jobs (paginated)
    running: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        # Page size for cursor-based pagination — all pages are fetched via the loop below.
        path = "/api/jobs?state=running&limit=100"
        if cursor:
            path += f"&cursor={cursor}"
        status, body = _api_get(base_url, path)
        if status != 200 or not body:
            break
        running.extend(body.get("items", []))
        if not body.get("hasMore"):
            break
        cursor = body.get("cursor")

    if not running:
        click.echo("  No running sessions to pause.")
        return

    click.echo(f"  Pausing {len(running)} running session(s)…")
    for job in running:
        ok = _api_post(base_url, f"/api/jobs/{job['id']}/pause") == 204
        mark = "✓" if ok else "✗"
        title = job.get("title") or "(untitled)"
        click.echo(f"    {mark}  {job['id'][:8]}… {title}")


def _kill_process_group(pid: int, sig: int) -> None:
    """Send *sig* to the process group led by *pid*, falling back to the process itself.

    Windows has no process groups to signal, and ``os.kill`` there terminates
    only the named process. A connector (``cloudflared``/``devtunnel host``)
    is a child of the server, so signalling just the server left it running
    and still registered with the relay: ``cpl down`` reported "Server
    stopped" while the public hostname kept a live host connection. Children
    are therefore enumerated and terminated explicitly on Windows.

    The Windows path is *not* equivalent to the POSIX one: ``os.kill`` maps to
    ``TerminateProcess`` for anything but the two console-control events, so
    the server never runs a signal handler, uvicorn's graceful shutdown and
    the lifespan shutdown hooks are skipped, and every descendant is hard
    killed. See ``docs/configuration.md`` ("Tunnel Lifecycle") for what that
    costs and why it is accepted for now.
    """
    import os

    getpgid = getattr(os, "getpgid", None)
    killpg = getattr(os, "killpg", None)
    if getpgid is not None and killpg is not None:
        try:
            pgid = getpgid(pid)
            killpg(pgid, sig)
            return
        except (ProcessLookupError, PermissionError):
            pass
    else:
        _kill_windows_process_tree(pid)
    # Process gone or we don't own the group — try the PID directly.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(pid, sig)


#: Connector executables that must die before anything else in the tree: they
#: hold a public relay registration, so leaving one alive keeps the machine
#: reachable from the internet after ``cpl down`` claims it stopped.
_CONNECTOR_PROCESS_NAMES = frozenset({"cloudflared", "cloudflared.exe", "devtunnel", "devtunnel.exe"})


def _kill_windows_process_tree(pid: int) -> None:
    """Hard kill the descendants of *pid* (best effort); the caller kills *pid* itself.

    ``psutil.Process.terminate`` is an alias for ``kill`` on Windows, so this
    is unconditionally ungraceful for every descendant — agent CLIs and their
    subprocesses included. Connectors are killed first so that a slow or stuck
    descendant cannot delay closing the public entrypoint.
    """
    try:
        import psutil
    except ImportError:
        return
    try:
        children = psutil.Process(pid).children(recursive=True)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return

    def _is_connector(proc: object) -> bool:
        with contextlib.suppress(Exception):
            return (proc.name() or "").lower() in _CONNECTOR_PROCESS_NAMES  # type: ignore[attr-defined]
        return False

    ordered = sorted(children, key=lambda child: not _is_connector(child))
    for child in ordered:
        with contextlib.suppress(Exception):
            child.terminate()
    with contextlib.suppress(Exception):
        psutil.wait_procs(children, timeout=5)
    for child in children:
        with contextlib.suppress(Exception):
            if child.is_running():
                child.kill()


def _stop_server(port: int, timeout_seconds: int = 10) -> bool:
    """Send SIGTERM, wait up to *timeout_seconds*, then SIGKILL if still alive.

    Only targets processes actually bound to *port* (via
    ``_find_pids_on_port``) — never a machine-wide process-name/command-line
    scan. ``find_cpl_processes`` is deliberately NOT consulted here: since
    ``cpl down``/``cpl restart`` accept an explicit ``--port``, running
    multiple CodePlane instances on different ports is a supported usage,
    and a "cpl up"/"cpl restart" command-line match is machine-wide — it
    would return PIDs belonging to a *different*, unrelated instance bound
    to a different port (or a transient self-match of the scanning process
    itself) and kill/misreport it by mistake. Uses process-group signals so
    that child processes (tunnel daemons, uvicorn workers) are terminated
    together with their parent.
    """
    import os
    import time

    from backend.services.setup.checks import _port_is_listening

    pids = _find_pids_on_port(port)

    if not pids:
        if _port_is_listening(port):
            # Something is bound to the port but we couldn't attribute a PID
            # to it (e.g. psutil.AccessDenied, or POSIX with neither lsof
            # nor ss available). This is NOT "already stopped" — fail
            # loudly instead of silently claiming success, and don't widen
            # to a machine-wide process scan to compensate.
            click.secho(
                f"  A process is listening on port {port} but its PID could not be "
                "determined (permission denied or detection failure). Please stop it "
                "manually.",
                fg="yellow",
            )
            return False
        click.echo("  No process found — already stopped.")
        return True

    # Collect unique process groups so we kill entire subtrees (tunnel
    # children, uvicorn workers) instead of individual PIDs.
    pgids_seen: set[int] = set()
    ordered_pids: list[int] = []
    getpgid = getattr(os, "getpgid", None)
    for pid in pids:
        if getpgid is None:
            ordered_pids.append(pid)
            continue
        try:
            pgid = getpgid(pid)
        except (ProcessLookupError, PermissionError):
            continue
        if pgid not in pgids_seen:
            pgids_seen.add(pgid)
            ordered_pids.append(pid)

    click.echo(f"  Sending SIGTERM to PID(s) {pids}…")
    for pid in ordered_pids:
        _kill_process_group(pid, signal.SIGTERM)
    # Also signal any PID we couldn't resolve a group for
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)

    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = _find_pids_on_port(port)
        if not remaining:
            break
        if time.monotonic() > deadline:
            click.echo(f"  SIGTERM timed out after {timeout_seconds}s — sending SIGKILL to {remaining}…")
            sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
            for pid in remaining:
                _kill_process_group(pid, sigkill)
            time.sleep(1)
            break
        time.sleep(0.5)

    # Final verification — also use fuser as a last resort to mop up
    # orphaned children that escaped process-group signalling.
    leftover = _find_pids_on_port(port)
    if leftover:
        import subprocess as _sp

        click.echo(f"  Cleaning up remaining PIDs on port {port}: {leftover}…")
        with contextlib.suppress(FileNotFoundError, _sp.TimeoutExpired):
            _sp.run(["fuser", "-k", f"{port}/tcp"], capture_output=True, timeout=5)
        time.sleep(1)
        leftover = _find_pids_on_port(port)

    if leftover:
        click.secho(f"  Warning: PIDs still on port {port}: {leftover}", fg="yellow")
        return False

    click.secho("  Server stopped.", fg="green")
    return True


@cli.command()
@click.option("--host", default=None, help="Server host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Server port (default: from config or 8080)")
@click.option("--force", is_flag=True, help="Skip session pausing; stop immediately")
def down(host: str | None, port: int | None, force: bool) -> None:
    """Gracefully pause all active sessions and shut down the server."""
    config = load_config()
    host = host or config.server.host
    port = port or config.server.port
    base_url = f"http://{host}:{port}"

    running, _ = _is_server_running(host, port)
    if not running:
        click.echo("CodePlane is not running.")
        return

    # Install signal handler so Ctrl+C during shutdown still cleans up
    # the server processes instead of just aborting cpl down.
    _interrupted = False

    def _down_signal_handler(sig: int, frame: Any) -> None:
        nonlocal _interrupted
        if _interrupted:
            # Second Ctrl+C — hard exit
            raise SystemExit(1)
        _interrupted = True
        click.echo("\nInterrupted — forcing server stop…")

    prev_sigint = signal.signal(signal.SIGINT, _down_signal_handler)
    prev_sigterm = signal.signal(signal.SIGTERM, _down_signal_handler)

    try:
        # Pause sessions unless --force or interrupted
        if not force and not _interrupted:
            click.echo("Pausing active sessions…")
            _pause_active_sessions(base_url)
        elif force:
            click.echo("Skipping session pause (--force).")

        click.echo(f"Stopping CodePlane on port {port}…")
        if not _stop_server(port):
            raise SystemExit(1)
    finally:
        signal.signal(signal.SIGINT, prev_sigint)
        signal.signal(signal.SIGTERM, prev_sigterm)


# ---------------------------------------------------------------------------
# ``cpl restart`` — down (if running) then up
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default=None, help="Bind host (default: from config or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="Bind port (default: from config or 8080)")
@click.option("--dev", is_flag=True, help="Dev mode: skip frontend build")
@click.option("--remote", is_flag=True, help="Enable remote access via a tunnel provider")
@click.option(
    "--provider",
    default="devtunnel",
    type=click.Choice(["devtunnel", "cloudflare"], case_sensitive=False),
    show_default=True,
    help="Remote access provider (requires --remote)",
)
@click.option("--password", default=None, help="Set auth password (auto-generated with --remote)")
@click.option("--access-only", is_flag=True, help="Require Cloudflare Access JWT instead of a password")
@click.option("--no-password", is_flag=True, help="Disable password auth (not allowed with --remote)")
@click.option("--tunnel-name", default=None, help="Dev Tunnel name (default: random, reused across restarts)")
@click.option("--skip-preflight", is_flag=True, help="Skip preflight checks")
@click.option("--force", is_flag=True, help="Skip session pausing on shutdown")
def restart(
    host: str | None,
    port: int | None,
    dev: bool,
    remote: bool,
    provider: str,
    password: str | None,
    access_only: bool,
    no_password: bool,
    tunnel_name: str | None,
    skip_preflight: bool,
    force: bool,
) -> None:
    """Stop a running instance (if any) then start the server.

    Active agent sessions are paused before shutdown and will be recovered
    automatically on startup.
    """
    import sys

    config = load_config()
    host = host or config.server.host
    # ``restart`` cannot honor ``--port 0`` the way ``up`` can: the down phase
    # has to target a determinate port to find and stop a listener, and a
    # restart is only meaningful if the server comes back at the same address.
    # Reject it rather than silently substituting the configured default.
    if port == 0:
        click.secho(
            "ERROR: --port 0 is not supported by restart (it must target a specific "
            "port to stop, and would not come back at a predictable address). "
            "Use 'cpl up --port 0' to start on an OS-assigned port.",
            fg="red",
            err=True,
        )
        raise SystemExit(1)
    port = port or config.server.port
    base_url = f"http://{host}:{port}"

    # --- Down phase ---
    running, _ = _is_server_running(host, port)
    if running:
        click.echo("Stopping running instance…")
        if not force:
            _pause_active_sessions(base_url)
        if not _stop_server(port):
            click.secho("Failed to stop existing instance.", fg="red")
            raise SystemExit(1)
    else:
        click.echo("No running instance found — starting fresh.")

    # --- Up phase (exec into ``cpl up`` so it owns the terminal) ---
    args = [sys.executable, "-m", "backend.cli", "up", "--host", host, "--port", str(port)]
    if dev:
        args.append("--dev")
    if remote:
        args.append("--remote")
        # Replaying ``--provider`` unconditionally would make the child ``up``
        # see click's ParameterSource.COMMANDLINE for a value that is merely
        # ``restart``'s default, suppressing Cloudflare auto-detection — so
        # ``cpl restart --remote`` would start a Dev Tunnel where ``cpl up
        # --remote`` starts Cloudflare. Forward it only when the user asked.
        if _provider_explicit():
            args.extend(["--provider", provider])
    if password:
        args.extend(["--password", password])
    if access_only:
        args.append("--access-only")
    if no_password:
        args.append("--no-password")
    if tunnel_name:
        args.extend(["--tunnel-name", tunnel_name])
    if skip_preflight:
        args.append("--skip-preflight")

    click.echo("Starting CodePlane…")
    import os

    os.execv(sys.executable, args)


# ---------------------------------------------------------------------------
# ``cpl hook`` — Claude CLI hook bridge (stdin → HTTP POST)
# ---------------------------------------------------------------------------

from backend.services.external_gate_cli import gate as _external_gate_command  # noqa: E402

cli.add_command(_external_gate_command)


@cli.command()
@click.option("--port", default=None, type=int, help="CodePlane port (default: from config or 8080)")
def hook(port: int | None) -> None:
    """Read a Claude hook JSON payload from stdin and POST it to CodePlane.

    Required because Claude Code's SessionStart event only supports
    ``command`` hooks, not ``http``.  This command bridges the gap:
    Claude pipes the hook JSON to stdin, this command POSTs it to
    the local server, and prints the response JSON to stdout (which
    Claude reads as additionalContext for SessionStart).
    """
    import sys
    import urllib.error
    import urllib.request

    config = load_config()
    from backend.services.adapters.integration_policy import IntegrationPolicy

    if not IntegrationPolicy(config).enabled("claude"):
        raise click.ClickException("Claude hook is disabled by runtime.copilot_only")
    target_port = port or config.server.port

    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)

    url = f"http://127.0.0.1:{target_port}/api/hooks/claude"
    req = urllib.request.Request(
        url,
        data=raw.encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
            # Print response to stdout — Claude reads this as hook output
            sys.stdout.write(body)
    except urllib.error.URLError:
        # Server not running — silently exit so the hook doesn't block Claude
        pass
    except Exception:
        pass
