"""Cloudflare Access JWT verification.

When CodePlane sits behind a Cloudflare Access application, every proxied
request carries a ``Cf-Access-Jwt-Assertion`` header containing a signed JWT.
This module validates that JWT against Cloudflare's public signing keys
(fetched from the team's JWKS endpoint) so the internal password gate can be
safely bypassed.

Configuration
-------------
Two environment variables (or ``.env`` entries) are required:

* ``CPL_CF_ACCESS_TEAM``  — the Cloudflare Access *team name*
  (the ``<team>`` portion of ``https://<team>.cloudflareaccess.com``).
* ``CPL_CF_ACCESS_AUD``   — the *Application Audience (AUD) tag* shown in
  the Cloudflare Zero Trust dashboard under the Access application settings.

If either variable is missing, CF Access verification is **disabled** and the
``Cf-Access-Jwt-Assertion`` header is ignored entirely (the request falls
through to normal password auth).
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import jwt
import structlog

log = structlog.get_logger()

# JWKS cache TTL — Cloudflare rotates keys infrequently but we should
# re-fetch periodically so a key rotation doesn't cause a hard outage.
_JWKS_CACHE_TTL: float = 3600  # 1 hour

# Module-level state set by ``configure()``.
_cf_team: str | None = None
_cf_aud: str | None = None
_certs_url: str | None = None

# Cached JWKS keyset — protected by a lock for thread safety (the JWKS
# refresh can be triggered from any request thread).
_jwks_lock = threading.Lock()
_jwks_keys: list[dict[str, Any]] = []
from backend.models.domain import CodePlaneError  # noqa: E402

_jwks_fetched_at: float = 0


class CfAccessConfigError(CodePlaneError):
    """Raised when CF Access configuration is invalid or unreachable."""


def configure(*, team: str, aud: str, eager: bool = True) -> None:
    """Set CF Access parameters and optionally fetch the initial JWKS keyset.

    When *eager* is True (default), raises ``CfAccessConfigError`` if the
    JWKS endpoint is unreachable — preventing the server from starting with
    a misconfigured gate.  When *eager* is False, configuration is stored
    and the JWKS fetch is deferred to the first ``verify_token()`` call.
    """
    global _cf_team, _cf_aud, _certs_url  # noqa: PLW0603

    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?", team) or not aud.strip():
        raise CfAccessConfigError("Cloudflare Access requires a valid team name and non-empty audience")
    reset()
    _cf_team = team
    _cf_aud = aud
    _certs_url = f"https://{team}.cloudflareaccess.com/cdn-cgi/access/certs"

    if eager:
        # Eagerly fetch keys — fail fast if the gate doesn't exist.
        _refresh_jwks(force=True)

    log.info(
        "cf_access_configured",
        team=team,
        certs_url=_certs_url,
        keys_loaded=len(_jwks_keys),
        eager=eager,
    )


def is_configured() -> bool:
    """Return True when CF Access verification is active."""
    return _cf_team is not None and _cf_aud is not None


def verify_token(token: str) -> bool:
    """Validate a Cloudflare Access JWT.

    Returns True when the token signature, audience, and expiration are
    all valid.  Returns False (never raises) on any verification failure
    so callers can fall through to alternative auth methods.
    """
    if not is_configured():
        return False

    _maybe_refresh_jwks()

    with _jwks_lock:
        keys = list(_jwks_keys)

    if not keys:
        log.warning("cf_access_no_keys", msg="JWKS keyset is empty")
        return False

    # Try each key — CF may have multiple active signing keys during rotation.
    for key in keys:
        try:
            jwt.decode(
                token,
                key=jwt.algorithms.RSAAlgorithm.from_jwk(key),  # type: ignore[arg-type]  # from_jwk returns a key object accepted by decode
                algorithms=["RS256"],
                audience=_cf_aud,
                issuer=f"https://{_cf_team}.cloudflareaccess.com",
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
            return True
        except jwt.ExpiredSignatureError:
            log.debug("cf_access_token_expired")
            return False
        except jwt.InvalidAudienceError:
            log.warning("cf_access_bad_audience")
            return False
        except (jwt.DecodeError, jwt.InvalidTokenError):
            # Wrong key — try next.
            log.debug("cf_access_key_mismatch", key_index=keys.index(key))
            continue
        except Exception:
            log.warning("cf_access_verify_error", exc_info=True)
            continue

    log.warning("cf_access_no_matching_key", msg="JWT could not be verified with any JWKS key")
    return False


# ---------------------------------------------------------------------------
# JWKS management
# ---------------------------------------------------------------------------


def _maybe_refresh_jwks() -> None:
    """Refresh the cached JWKS if the TTL has elapsed."""
    if time.monotonic() - _jwks_fetched_at < _JWKS_CACHE_TTL:
        return
    try:
        _refresh_jwks()
    except CfAccessConfigError:
        # Non-fatal at runtime — keep using stale keys.
        log.warning("cf_access_jwks_refresh_failed", exc_info=True)


def _refresh_jwks(*, force: bool = False) -> None:
    """Fetch the JWKS keyset from Cloudflare.

    Raises ``CfAccessConfigError`` when *force* is True and the fetch fails
    (used at startup to fail fast).  When *force* is False, failures are
    silent so stale keys can still serve requests.
    """
    global _jwks_fetched_at  # noqa: PLW0603

    if _certs_url is None:
        if force:
            raise CfAccessConfigError("CF Access certs URL not configured")
        return

    try:
        req = urllib.request.Request(_certs_url, method="GET")
        req.add_header("User-Agent", "cpl-cfaccess/1.0")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            jwks = json.loads(resp.read())
    except Exception as exc:
        if force:
            raise CfAccessConfigError(f"Failed to fetch Cloudflare Access JWKS from {_certs_url}: {exc}") from exc
        log.debug("cf_access_jwks_fetch_failed", certs_url=_certs_url, exc_info=True)
        return

    keys = jwks.get("keys")
    if not isinstance(keys, list) or not keys:
        if force:
            raise CfAccessConfigError(
                f"Cloudflare Access JWKS from {_certs_url} contains no keys — "
                "is the team name correct and does an Access Application exist?"
            )
        return

    with _jwks_lock:
        _jwks_keys.clear()
        _jwks_keys.extend(keys)

    _jwks_fetched_at = time.monotonic()
    log.debug("cf_access_jwks_refreshed", key_count=len(keys))


def reset() -> None:
    """Reset all module state — for tests only."""
    global _cf_team, _cf_aud, _certs_url, _jwks_fetched_at  # noqa: PLW0603
    _cf_team = None
    _cf_aud = None
    _certs_url = None
    _jwks_fetched_at = 0
    with _jwks_lock:
        _jwks_keys.clear()
