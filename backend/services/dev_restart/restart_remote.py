"""Remote-origin probing for the restart helper's ``checking_remote`` phase.

Resolves the exact origin the restart helper must probe after replacing a
running CodePlane process, and performs a single bounded reachability check
against that exact origin (SPEC.md CAP-6, ARCHITECTURE-SPINE.md AD-8). Never
scans local processes or spawns a connector to discover an origin — the
origin is always read directly off a ``LaunchProfile`` — matching CAP-6's
"external tunnel mode never scans/spawns" requirement.

Owned by this integration slice (coordinated with integration session
3fd0b7af-27ee-4346-9098-911b5350a34c). ``backend.services.dev_restart.
restart_helper`` is expected to import and call these two functions from its
``RestartPhase.checking_remote`` step in a follow-up commit from that
session; this module intentionally does not modify ``restart_helper.py``
itself.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from backend.services.dev_restart.restart_protocol import RestartProtocolError

if TYPE_CHECKING:
    from backend.services.dev_restart.launch_profile import LaunchProfile


class RemoteProbeError(RestartProtocolError):
    """Raised when the expected remote origin cannot be resolved or is unreachable."""


@dataclass(frozen=True, slots=True)
class RemoteProbeTarget:
    """The exact origin to probe, and whether it changed from the original launch."""

    origin: str
    changed: bool


def resolve_remote_probe_target(original: LaunchProfile, replacement: LaunchProfile) -> RemoteProbeTarget:
    """Resolve the exact origin the helper must probe after a restart (SPEC.md CAP-6).

    A reusable origin (``original.tunnel_origin_reusable`` is true — a fixed
    Cloudflare hostname or an explicitly named/already-registered Dev Tunnel)
    is expected to be identical across the restart, so the *original*
    profile's recorded origin is authoritative and ``changed`` is always
    ``False``.

    A non-reusable origin (``original.tunnel_origin_reusable`` is false or
    unset — e.g. a freshly generated Dev Tunnel name) cannot be predicted
    before the replacement process starts, so the *replacement* profile's
    recorded origin is authoritative, and ``changed`` reports whether it
    actually differs from the original — the signal the helper surfaces to
    the developer as "reconnect using the new address" (SPEC.md CAP-6).

    Raises ``RemoteProbeError`` if the profile that should own the origin for
    the resolved mode did not record one.
    """
    if original.effective_auth_mode != replacement.effective_auth_mode:
        raise RemoteProbeError("replacement authentication mode differs from the original launch")
    if original.tunnel_origin_reusable:
        origin = original.tunnel_origin
        if not origin:
            raise RemoteProbeError(
                "original launch profile has tunnel_origin_reusable=True but no tunnel_origin recorded"
            )
        return RemoteProbeTarget(origin=origin, changed=False)

    origin = replacement.tunnel_origin
    if not origin:
        raise RemoteProbeError(
            "replacement launch profile has tunnel_origin_reusable=False but no tunnel_origin recorded"
        )
    return RemoteProbeTarget(origin=origin, changed=origin != original.tunnel_origin)


def probe_remote_origin(origin: str, timeout_seconds: float) -> None:
    """Perform one bounded reachability check against the exact *origin*.

    Never scans local processes or resolves any identity other than the
    literal *origin* string passed in — CAP-6's "external mode probes the
    exact hostname without process scans" requirement. Reuses the plain
    ``urllib.request`` pattern already used elsewhere in this codebase (see
    ``backend/lifespan.py``'s ``_deferred_cloudflare_access_check`` and
    ``backend/services/dev_restart/restart_helper.py``'s ``_http_request``)
    rather than adding a new HTTP client dependency.

    Raises ``RemoteProbeError`` on any timeout, connection failure, or
    non-2xx response — never returns a partial or ambiguous result.
    """
    url = f"{origin.rstrip('/')}/api/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 - fixed GET, exact recorded origin
            status = resp.status
    except urllib.error.HTTPError as exc:
        raise RemoteProbeError(f"remote origin {origin} returned HTTP {exc.code}") from exc
    except TimeoutError as exc:
        raise RemoteProbeError(f"remote origin {origin} timed out after {timeout_seconds}s") from exc
    except urllib.error.URLError as exc:
        raise RemoteProbeError(f"remote origin {origin} is unreachable: {exc.reason}") from exc

    if not (200 <= status < 300):
        raise RemoteProbeError(f"remote origin {origin} returned unhealthy status {status}")
