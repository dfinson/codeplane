"""Remote-posture regressions without sockets, tunnels or lifespan side effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI

from backend.app_factory import _configure_middleware
from backend.services.auth import cf_access
from backend.services.auth import middleware as auth


@pytest.fixture(autouse=True)
def isolate_auth():
    auth._reset_for_testing()
    cf_access.reset()
    yield
    auth._reset_for_testing()
    cf_access.reset()


def protected_app(password=None):
    app = FastAPI()
    _configure_middleware(app, dev=False, tunnel_origin="https://cpl.example.com", password=password)

    async def protected():
        return {"protected": True}

    for path in ("/api/protected", "/api/events", "/mcp"):
        app.add_api_route(path, protected)
    return app


class TestRuntimeContract:
    @pytest.mark.parametrize("password", [None, "chosen-password"])
    def test_cloudflare_cli_retains_generated_or_explicit_password(self, tmp_path, monkeypatch, password):
        from backend.config import CPLConfig
        from backend.services.sharing.tunnel_service import RemoteProvider, TunnelHandle
        from backend.tests.unit.test_cli import _invoke_up

        for key in ("CPL_PASSWORD", "CPL_CF_ACCESS_TEAM", "CPL_CF_ACCESS_AUD"):
            monkeypatch.delenv(key, raising=False)
        handle = TunnelHandle(provider=RemoteProvider.cloudflare, origin="https://cpl.example.com")
        args = ["--remote", "--provider", "cloudflare", "--skip-preflight"]
        if password:
            args.extend(["--password", password])
        with (
            patch("backend.cli.DOTENV_PATH", tmp_path / "absent.env"),
            patch("backend.cli.load_config", return_value=CPLConfig()),
            patch("backend.config.get_codeplane_dir", return_value=tmp_path),
            patch("backend.services.sharing.tunnel_service.start_remote_access", return_value=handle),
        ):
            result, written = _invoke_up(args)
        assert result.exit_code == 0, result.output
        assert written["passwordSource"] == {"kind": "unreplayable"}

    @pytest.mark.anyio()
    @pytest.mark.parametrize("path", ["/api/protected", "/api/events", "/mcp"])
    @pytest.mark.parametrize("verification_state", ["pending", "failed", "verified"])
    async def test_remote_gate_never_trusts_connector_loopback(self, path, verification_state):
        app = protected_app("chosen-password")
        app.state.cloudflare_access_verification = verification_state
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="https://cpl.example.com") as client:
            response = await client.get(path, headers={"x-forwarded-for": "127.0.0.1"})
        assert response.status_code == 401

    @pytest.mark.anyio()
    async def test_remote_construction_without_password_still_installs_gate(self):
        app = protected_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/protected")
        assert response.status_code == 401

    @pytest.mark.anyio()
    async def test_remote_terminal_loopback_rejected_before_service_access(self):
        from backend.api.terminal import terminal_ws

        app = protected_app("chosen-password")
        ws = MagicMock(app=app, client=SimpleNamespace(host="127.0.0.1"), cookies={}, headers={})
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        from starlette.websockets import WebSocketDisconnect

        ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())
        app.state.dishka_container = SimpleNamespace(get=AsyncMock(return_value=MagicMock()))
        await terminal_ws(ws)
        ws.close.assert_awaited_once_with(code=1008, reason="Authentication required")
        ws.accept.assert_not_awaited()

    @pytest.mark.anyio()
    async def test_invalid_access_jwt_does_not_inherit_loopback_trust(self):
        app = protected_app("chosen-password")
        cf_access.configure(team="example", aud="audience", eager=False)
        with patch.object(cf_access, "verify_token", return_value=False) as verify:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 50000)),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/events", headers={"cf-access-jwt-assertion": "invalid"})
        assert response.status_code == 401
        verify.assert_called_once_with("invalid")
