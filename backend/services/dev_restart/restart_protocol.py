"""Shared restart-protocol primitives.

Every restart-related module (launch-profile validation, the parent
preparation flow, and the detached helper) imports paths, timeouts, phase
logging, atomic JSON I/O, process-identity checks, and the restart lock from
here instead of re-implementing them. Centralizing this logic is required by
Story 1.1's Dev Notes ("Keep schema parsing, serialization, atomic
persistence, and validation in one focused internal helper surface") and
generalized here so Stories 1.2-1.7 share one implementation.

See:
- ``_bmad-output/planning-artifacts/architecture/architecture-codeplane-self-restart-2026-08-07/ARCHITECTURE-SPINE.md``
- ``_bmad-output/planning-artifacts/architecture/architecture-codeplane-self-restart-2026-08-07/SOLUTION-DESIGN.md``
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO

import psutil
import structlog

from backend import config as backend_config
from backend.models.domain import CodePlaneError

log = structlog.get_logger()

_DEV_RESTART_SUBDIR = "dev-restart"
_RESTART_LOG_FILENAME = "restart.log"
_RESTART_LOCK_FILENAME = "restart.lock"
_REPLACEMENT_LOG_SUFFIX = ".server.log"

# psutil.Process.create_time() is a float; allow a small epsilon for
# platform/float-precision jitter without weakening the PID-reuse check.
_CREATE_TIME_TOLERANCE_SECONDS = 0.01


class RestartProtocolError(CodePlaneError):
    """Base error for restart-protocol path, timeout, or JSON I/O failures."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep local credentials and remote probes on their exact destination."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def get_dev_restart_dir() -> Path:
    """Return ``~/.codeplane/dev-restart/`` (``CODEPLANE_HOME``-aware), creating it if needed."""
    path = backend_config.get_codeplane_dir() / _DEV_RESTART_SUBDIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_restart_log_path() -> Path:
    """Return the single absolute restart log path, opened/appended by parent and helper alike."""
    return get_dev_restart_dir() / _RESTART_LOG_FILENAME


def get_replacement_log_path(request_id: str) -> Path:
    """Return the per-request replacement stdout/stderr log path.

    The replacement must never inherit ``restart.log`` itself: keeping the
    helper's log file open in the long-lived server process can block the next
    attempt's restart-log rotation on Windows. A request-specific sibling log
    preserves diagnostics without holding ``restart.log`` across attempts.
    """
    return get_dev_restart_dir() / f"{request_id}{_REPLACEMENT_LOG_SUFFIX}"


# Story 1.7 AC3: rotate at 5 MiB with exactly one backup.
_RESTART_LOG_MAX_BYTES = 5 * 1024 * 1024
_RESTART_LOG_BACKUP_SUFFIX = ".1"


def rotate_restart_log_if_needed(log_path: Path, max_bytes: int = _RESTART_LOG_MAX_BYTES) -> None:
    """Rotate *log_path* to a single ``.1`` backup once it reaches *max_bytes*.

    Called once per restart attempt, before the parent opens the log for
    append -- the log is only ever opened at the start of a bounded
    parent/helper restart attempt (never held open across attempts), so
    rotate-on-open is sufficient to bound its size while preserving the most
    recent prior attempt's output. Any existing backup is replaced so at
    most one backup is ever retained.
    """
    try:
        size = log_path.stat().st_size
    except FileNotFoundError:
        return
    if size < max_bytes:
        return
    backup_path = log_path.with_name(log_path.name + _RESTART_LOG_BACKUP_SUFFIX)
    try:
        backup_path.unlink(missing_ok=True)
        log_path.replace(backup_path)
    except OSError as exc:
        raise RestartProtocolError(f"could not rotate {log_path}: {exc}") from exc


def get_restart_lock_path() -> Path:
    return get_dev_restart_dir() / _RESTART_LOCK_FILENAME


@dataclass(frozen=True, slots=True)
class RestartRequestPaths:
    """The four request-lifecycle file paths for one restart request ID."""

    pending: Path
    claimed: Path
    started: Path
    ready: Path


def get_request_paths(request_id: str) -> RestartRequestPaths:
    base = get_dev_restart_dir()
    return RestartRequestPaths(
        pending=base / f"{request_id}.pending.json",
        claimed=base / f"{request_id}.claimed.json",
        started=base / f"{request_id}.started.json",
        ready=base / f"{request_id}.ready.json",
    )


# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestartTimeouts:
    """Restart phase timeouts. Defaults match SPEC.md; every field is CLI-overridable and logged."""

    adoption_seconds: float = 5.0
    response_grace_seconds: float = 2.0
    pause_wait_seconds: float = 10.0
    stop_seconds: float = 15.0
    readiness_seconds: float = 60.0
    remote_probe_seconds: float = 30.0

    def to_dict(self) -> dict[str, float]:
        return {
            "adoptionSeconds": self.adoption_seconds,
            "responseGraceSeconds": self.response_grace_seconds,
            "pauseWaitSeconds": self.pause_wait_seconds,
            "stopSeconds": self.stop_seconds,
            "readinessSeconds": self.readiness_seconds,
            "remoteProbeSeconds": self.remote_probe_seconds,
        }

    @staticmethod
    def from_dict(data: Any) -> RestartTimeouts:
        if not isinstance(data, dict):
            raise RestartProtocolError("restart timeouts must be a JSON object")
        try:
            return RestartTimeouts(
                adoption_seconds=float(data["adoptionSeconds"]),
                response_grace_seconds=float(data["responseGraceSeconds"]),
                pause_wait_seconds=float(data["pauseWaitSeconds"]),
                stop_seconds=float(data["stopSeconds"]),
                readiness_seconds=float(data["readinessSeconds"]),
                remote_probe_seconds=float(data["remoteProbeSeconds"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise RestartProtocolError(f"malformed restart timeouts: {exc}") from exc


# ---------------------------------------------------------------------------
# Phase logging
# ---------------------------------------------------------------------------


class RestartPhase(StrEnum):
    spawned = "spawned"
    pausing = "pausing"
    stopping = "stopping"
    starting = "starting"
    checking_health = "checking_health"
    checking_remote = "checking_remote"
    succeeded = "succeeded"
    failed = "failed"


def log_phase(phase: RestartPhase, request_id: str, *, stream: TextIO | None = None, **fields: Any) -> None:
    """Write one flushed, secret-free JSON line describing a restart phase transition.

    ``stream`` defaults to ``sys.stdout`` resolved at call time (not import
    time): the detached helper's stdout is redirected to the inherited
    CodePlane log handle only after it spawns, so binding ``sys.stdout`` at
    import time would silently write to the pre-redirection stream instead.
    Callers must never pass secret values in ``fields``.
    """
    target = stream if stream is not None else sys.stdout
    record: dict[str, Any] = {
        "requestId": request_id,
        "phase": str(phase),
        "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **fields,
    }
    target.write(json.dumps(record, default=str) + "\n")
    target.flush()


# ---------------------------------------------------------------------------
# Atomic JSON I/O
# ---------------------------------------------------------------------------


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON to *path* atomically via a same-directory temp file + ``os.replace``.

    An interrupted write can never be mistaken for a valid file: only a
    fully flushed, fsynced temporary file is ever renamed onto *path*, an
    older complete file at *path* is never deleted before the replacement
    succeeds, and only the failed temporary file is removed on error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def read_json_file(path: Path) -> dict[str, Any]:
    """Read and parse *path* as a JSON object. Fails closed on any I/O or shape error."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RestartProtocolError(f"missing file: {path}") from exc
    except OSError as exc:
        raise RestartProtocolError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RestartProtocolError(f"malformed JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RestartProtocolError(f"expected a JSON object in {path}")
    return data


# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------


def is_identity_alive(pid: int, process_time: float) -> bool:
    """True iff *pid* is a live process whose creation time matches *process_time*.

    The creation-time check is required, not optional: it is what prevents a
    reused PID from being mistaken for the original process.
    """
    try:
        actual = psutil.Process(pid).create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return abs(actual - process_time) <= _CREATE_TIME_TOLERANCE_SECONDS


# ---------------------------------------------------------------------------
# Restart lock
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RestartLock:
    request_id: str
    helper_pid: int
    helper_process_time: float
    path: Path


class RestartLockHeldError(RestartProtocolError):
    """Raised when a live restart lock already belongs to another helper."""


def _read_lock_payload(path: Path) -> dict[str, Any] | None:
    try:
        return read_json_file(path)
    except RestartProtocolError:
        return None


def acquire_restart_lock(request_id: str, helper_pid: int, helper_process_time: float) -> RestartLock:
    """Create ``restart.lock`` exclusively. Refuses when a live lock belongs to another helper.

    A lock left behind by a helper that is no longer alive (identified via
    ``is_identity_alive``) is stale and safe to reclaim -- this is a
    single-machine, developer-only file lock, not a distributed lock.
    """
    path = get_restart_lock_path()
    payload = {
        "requestId": request_id,
        "helperPid": helper_pid,
        "helperProcessTime": helper_process_time,
        "createdAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    def _create_exclusive() -> int:
        return os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)

    try:
        fd = _create_exclusive()
    except FileExistsError:
        existing = _read_lock_payload(path)
        if (
            existing is not None
            and isinstance(existing.get("helperPid"), int)
            and isinstance(existing.get("helperProcessTime"), (int, float))
            and is_identity_alive(existing["helperPid"], existing["helperProcessTime"])
        ):
            raise RestartLockHeldError(f"restart lock is held by live helper PID {existing['helperPid']}") from None
        with contextlib.suppress(OSError):
            path.unlink()
        fd = _create_exclusive()

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
    except OSError:
        with contextlib.suppress(OSError):
            path.unlink()
        raise
    return RestartLock(request_id=request_id, helper_pid=helper_pid, helper_process_time=helper_process_time, path=path)


def release_restart_lock(lock: RestartLock) -> None:
    """Delete the lock file only if it still matches this lock's identity.

    Never deletes a lock that belongs to a different request/helper -- e.g.
    one acquired by a newer helper after this lock was already considered
    stale by someone else.
    """
    existing = _read_lock_payload(lock.path)
    if existing is None:
        return
    if existing.get("requestId") == lock.request_id and existing.get("helperPid") == lock.helper_pid:
        with contextlib.suppress(FileNotFoundError, OSError):
            lock.path.unlink()
