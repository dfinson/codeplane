"""VAPID key management for Web Push notifications.

Generates and persists VAPID EC P-256 key pairs. Keys are stored in
``~/.codeplane/vapid.json`` and reused across restarts so existing push
subscriptions remain valid.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import tempfile
from typing import TYPE_CHECKING, Any

import structlog
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_der_private_key

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger()


def _decode(value: str) -> bytes:
    encoded = value.replace("\n", "").encode("ascii")
    return base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _retained_keys(data: Any) -> dict[str, str]:
    if not isinstance(data, dict) or not isinstance(private := data.get("private_key"), str) or not private:
        raise ValueError("Persisted VAPID private key is missing or malformed")
    try:
        raw = _decode(private)
        # Match py_vapid 1.9.4 from_string: 32 raw scalar octets, otherwise DER.
        key = (
            ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
            if len(raw) == 32
            else load_der_private_key(raw, password=None)
        )
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
            raise ValueError("Wrong curve")
        public_bytes = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        public = data.get("public_key")
        if public is None or public == "":
            public = _encode(public_bytes)
            log.info("vapid_public_key_derived_from_retained_private_key")
        elif not isinstance(public, str) or _decode(public) != public_bytes:
            raise ValueError("Mismatched public key")
    except (ValueError, TypeError, binascii.Error, UnicodeError, UnsupportedAlgorithm):
        raise ValueError("Persisted VAPID key material is malformed or inconsistent") from None
    return {"private_key": private, "public_key": public}


def get_or_create_vapid_keys(codeplane_dir: Path) -> dict[str, str]:
    """Generate only for an absent file; never rotate or rewrite retained keys."""
    vapid_path = codeplane_dir / "vapid.json"

    try:
        retained = vapid_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        retained = None
    if retained is not None:
        try:
            return _retained_keys(json.loads(retained))
        except (ValueError, UnicodeError):
            log.error("vapid_keys_invalid", path=str(vapid_path))
            raise ValueError("Persisted VAPID keys are invalid; explicit repair is required") from None

    key = ec.generate_private_key(ec.SECP256R1())
    keys = {
        "public_key": _encode(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)),
        "private_key": _encode(key.private_numbers().private_value.to_bytes(32, "big")),
    }
    vapid_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, candidate = tempfile.mkstemp(prefix=".vapid-", suffix=".tmp", dir=codeplane_dir)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(keys, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # A same-directory hard link publishes completed bytes atomically
            # and fails rather than replacing another initializer's winner.
            os.link(candidate, vapid_path)
        except FileExistsError:
            return get_or_create_vapid_keys(codeplane_dir)
    finally:
        os.unlink(candidate)
    log.info("vapid_keys_generated", path=str(vapid_path))
    return keys
