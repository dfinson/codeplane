"""Tests for credential handling."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codeplane.git.credentials import SystemCredentialCallback, get_default_callbacks


class TestSystemCredentialCallback:
    """Tests for SystemCredentialCallback."""

    def test_get_default_callbacks_returns_instance(self) -> None:
        """get_default_callbacks should return SystemCredentialCallback."""
        callbacks = get_default_callbacks()
        assert isinstance(callbacks, SystemCredentialCallback)

    def test_credentials_ssh_returns_keypair_from_agent(self) -> None:
        """SSH credential request should return KeypairFromAgent."""
        import pygit2

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.SSH_KEY

        result = callback.credentials("git@github.com:user/repo.git", "git", allowed)

        assert isinstance(result, pygit2.KeypairFromAgent)

    def test_credentials_ssh_uses_username_from_url(self) -> None:
        """SSH should use username from URL if provided."""
        import pygit2

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.SSH_KEY

        result = callback.credentials("ssh://custom@github.com/repo.git", "custom", allowed)

        assert isinstance(result, pygit2.KeypairFromAgent)
        # KeypairFromAgent stores username as _username (private)
        assert result._username == "custom"

    def test_credentials_ssh_defaults_to_git_user(self) -> None:
        """SSH should default to 'git' username."""
        import pygit2

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.SSH_KEY

        result = callback.credentials("git@github.com:user/repo.git", None, allowed)

        assert isinstance(result, pygit2.KeypairFromAgent)
        assert result._username == "git"

    @patch("subprocess.run")
    def test_credentials_https_with_helper(self, mock_run: MagicMock) -> None:
        """HTTPS should query credential helper and return UserPass."""
        import pygit2

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="protocol=https\nhost=github.com\nusername=myuser\npassword=mytoken\n",
        )

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert isinstance(result, pygit2.UserPass)
        # UserPass stores credentials as _username/_password (private)
        assert result._username == "myuser"
        assert result._password == "mytoken"

    @patch("subprocess.run")
    def test_credentials_https_helper_failure(self, mock_run: MagicMock) -> None:
        """HTTPS should return None if helper fails."""
        import pygit2

        mock_run.return_value = MagicMock(returncode=1, stdout="")

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert result is None

    @patch("subprocess.run")
    def test_credentials_https_helper_timeout(self, mock_run: MagicMock) -> None:
        """HTTPS should return None on timeout."""
        import subprocess

        import pygit2

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=30)

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert result is None

    @patch("subprocess.run")
    def test_credentials_https_helper_not_found(self, mock_run: MagicMock) -> None:
        """HTTPS should return None if git not found."""
        import pygit2

        mock_run.side_effect = FileNotFoundError("git not found")

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert result is None

    @patch("subprocess.run")
    def test_credentials_https_missing_username(self, mock_run: MagicMock) -> None:
        """HTTPS should return None if credentials incomplete."""
        import pygit2

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="protocol=https\nhost=github.com\npassword=mytoken\n",
        )

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert result is None

    @patch("subprocess.run")
    def test_credentials_https_includes_port(self, mock_run: MagicMock) -> None:
        """HTTPS credential request should include port if present."""
        import pygit2

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="username=user\npassword=pass\n",
        )

        callback = SystemCredentialCallback()
        allowed = pygit2.enums.CredentialType.USERPASS_PLAINTEXT

        callback.credentials("https://example.com:8443/repo.git", None, allowed)

        # Check that port was included in the input
        call_args = mock_run.call_args
        input_data = call_args.kwargs.get("input", "")
        assert "port=8443" in input_data

    def test_credentials_unsupported_type_returns_none(self) -> None:
        """Unsupported credential type should return None."""
        import pygit2

        callback = SystemCredentialCallback()
        # Request a type we don't handle (e.g., USERNAME only)
        allowed = pygit2.enums.CredentialType.USERNAME

        result = callback.credentials("https://github.com/user/repo.git", None, allowed)

        assert result is None
