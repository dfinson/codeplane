"""Credential handling for git remote operations."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pygit2

if TYPE_CHECKING:
    from pygit2.enums import CredentialType


class SystemCredentialCallback(pygit2.RemoteCallbacks):
    """
    RemoteCallbacks that uses system credential helpers.

    Supports:
    - SSH via KeypairFromAgent (uses system SSH agent)
    - HTTPS via git-credential-manager or other configured helpers
    """

    def credentials(  # type: ignore[override]
        self,
        url: str,
        username_from_url: str | None,
        allowed_types: CredentialType,
    ) -> pygit2.Username | pygit2.UserPass | pygit2.Keypair | None:
        """Provide credentials for remote operations."""
        # SSH: use agent
        if allowed_types & pygit2.enums.CredentialType.SSH_KEY:
            username = username_from_url or "git"
            return pygit2.KeypairFromAgent(username)

        # HTTPS: query system credential helper
        if allowed_types & pygit2.enums.CredentialType.USERPASS_PLAINTEXT:
            creds = self._query_credential_helper(url)
            if creds:
                return pygit2.UserPass(creds["username"], creds["password"])

        return None

    def _query_credential_helper(self, url: str) -> dict[str, str] | None:
        """
        Query system git credential helper.

        Invokes: git credential fill
        See: https://git-scm.com/docs/git-credential
        """
        parsed = urlparse(url)
        host = parsed.hostname or parsed.netloc
        input_lines = [
            f"protocol={parsed.scheme}",
            f"host={host}",
        ]
        if parsed.port is not None:
            input_lines.append(f"port={parsed.port}")
        if parsed.path:
            input_lines.append(f"path={parsed.path.lstrip('/')}")
        input_lines.append("")  # Empty line terminates input
        input_data = "\n".join(input_lines)

        try:
            result = subprocess.run(
                ["git", "credential", "fill"],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                return None

            creds: dict[str, str] = {}
            for line in result.stdout.strip().split("\n"):
                if "=" in line:
                    key, value = line.split("=", 1)
                    creds[key] = value

            if "username" in creds and "password" in creds:
                return creds
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            # Credential helper unavailable or failed - fall through to return None
            # This is expected when git isn't installed or credential helper isn't configured
            pass

        return None


def get_default_callbacks() -> SystemCredentialCallback:
    """Get default remote callbacks with system credential support."""
    return SystemCredentialCallback()
