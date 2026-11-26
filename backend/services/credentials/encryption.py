"""PAT encryption-at-rest for global integration Credentials (Story 3.1, NFR1).

A single app-level symmetric key is generated on first use and persisted at
``get_codeplane_dir() / "credential.key"`` with owner-only permissions where
supported. Every ``CredentialRow.encrypted_secret`` is a Fernet token produced
with that key. The key file is developer-machine-local, file-backed
diagnostic-adjacent state — the same posture as ``run.json`` — and is never
logged or transmitted.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import stat

from cryptography.fernet import Fernet, InvalidToken

from backend import config as backend_config

_KEY_FILENAME = "credential.key"


class CredentialDecryptionError(Exception):
    """Raised when a stored secret cannot be decrypted with the current key."""


def _load_or_create_key() -> bytes:
    path = backend_config.get_codeplane_dir() / _KEY_FILENAME
    if path.exists():
        return path.read_bytes()

    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    path.write_bytes(key)
    with contextlib.suppress(OSError):
        # Best-effort on platforms without POSIX permission bits (e.g. Windows).
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return key


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a PAT for storage. Returns an opaque token, never the plaintext."""
    fernet = Fernet(_load_or_create_key())
    return fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a stored token back to the PAT. Only call this at point of use."""
    fernet = Fernet(_load_or_create_key())
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialDecryptionError("Stored credential secret could not be decrypted") from exc


def derive_export_identity_key(destination: str) -> bytes:
    """Domain-separate export pseudonyms from the existing local credential key."""
    return hmac.new(
        _load_or_create_key(),
        b"codeplane-export-correlation-v1\0" + destination.encode("utf-8"),
        hashlib.sha256,
    ).digest()
