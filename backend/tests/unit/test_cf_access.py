"""Tests for Cloudflare Access JWT verification (backend.services.auth.cf_access)."""

from __future__ import annotations

import json
import time
from io import BytesIO
from typing import Any
from unittest.mock import patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from backend.services.auth import cf_access

# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _generate_rsa_keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """Generate an RSA keypair and return (private_key, jwk_dict)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # Build a JWK dict from the public key
    public_numbers = private_key.public_key().public_numbers()

    def _b64url(n: int, length: int) -> str:
        import base64

        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": "test-key-1",
        "n": _b64url(public_numbers.n, 256),
        "e": _b64url(public_numbers.e, 3),
    }
    return private_key, jwk


def _make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    aud: str = "test-aud-tag",
    iss: str = "https://testteam.cloudflareaccess.com",
    exp_offset: int = 3600,
    iat_offset: int = 0,
) -> str:
    """Create a signed JWT."""
    now = int(time.time())
    payload = {
        "aud": [aud],
        "iss": iss,
        "iat": now - iat_offset,
        "exp": now + exp_offset,
        "sub": "test-user-id",
        "email": "user@example.com",
    }
    return pyjwt.encode(payload, private_key, algorithm="RS256")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cf_access():
    """Reset cf_access module state before and after each test."""
    cf_access.reset()
    yield
    cf_access.reset()


@pytest.fixture()
def rsa_keypair():
    return _generate_rsa_keypair()


@pytest.fixture()
def jwks_response(rsa_keypair):
    """Return a JWKS JSON response body containing the test public key."""
    _, jwk = rsa_keypair
    return json.dumps({"keys": [jwk]}).encode()


# ---------------------------------------------------------------------------
# configure()
# ---------------------------------------------------------------------------


class TestConfigure:
    def test_configure_fetches_jwks_and_sets_state(self, rsa_keypair, jwks_response):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp

            cf_access.configure(team="testteam", aud="test-aud-tag")

        assert cf_access.is_configured()
        assert len(cf_access._jwks_keys) == 1

    def test_configure_raises_on_network_error(self):
        _urlopen = "backend.services.auth.cf_access.urllib.request.urlopen"
        with (
            patch(_urlopen, side_effect=Exception("timeout")),
            pytest.raises(cf_access.CfAccessConfigError, match="Failed to fetch"),
        ):
            cf_access.configure(team="badteam", aud="aud")

    def test_configure_raises_on_empty_keys(self):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(json.dumps({"keys": []}).encode())
            mock_urlopen.return_value = resp

            with pytest.raises(cf_access.CfAccessConfigError, match="contains no keys"):
                cf_access.configure(team="testteam", aud="aud")


# ---------------------------------------------------------------------------
# verify_token()
# ---------------------------------------------------------------------------


class TestVerifyToken:
    def test_valid_token_passes(self, rsa_keypair, jwks_response):
        private_key, _ = rsa_keypair
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        token = _make_token(private_key, aud="test-aud-tag")
        assert cf_access.verify_token(token) is True

    def test_expired_token_rejected(self, rsa_keypair, jwks_response):
        private_key, _ = rsa_keypair
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        token = _make_token(private_key, aud="test-aud-tag", exp_offset=-100)
        assert cf_access.verify_token(token) is False

    def test_wrong_audience_rejected(self, rsa_keypair, jwks_response):
        private_key, _ = rsa_keypair
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        token = _make_token(private_key, aud="wrong-aud")
        assert cf_access.verify_token(token) is False

    def test_wrong_key_rejected(self, rsa_keypair, jwks_response):
        # Sign with a different key than the one in JWKS
        other_key, _ = _generate_rsa_keypair()
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        token = _make_token(other_key, aud="test-aud-tag")
        assert cf_access.verify_token(token) is False

    def test_not_configured_returns_false(self):
        assert cf_access.verify_token("anything") is False

    def test_empty_token_returns_false(self, rsa_keypair, jwks_response):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        assert cf_access.verify_token("") is False

    def test_garbage_token_returns_false(self, rsa_keypair, jwks_response):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")

        assert cf_access.verify_token("not.a.jwt") is False


# ---------------------------------------------------------------------------
# is_configured()
# ---------------------------------------------------------------------------


class TestIsConfigured:
    def test_not_configured_by_default(self):
        assert cf_access.is_configured() is False

    def test_configured_after_setup(self, rsa_keypair, jwks_response):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")
        assert cf_access.is_configured() is True

    def test_reset_clears_configuration(self, rsa_keypair, jwks_response):
        with patch("backend.services.auth.cf_access.urllib.request.urlopen") as mock_urlopen:
            resp = BytesIO(jwks_response)
            mock_urlopen.return_value = resp
            cf_access.configure(team="testteam", aud="test-aud-tag")
        cf_access.reset()
        assert cf_access.is_configured() is False
