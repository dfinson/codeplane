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


def _access_only_process_fixture():
    """Owned synthetic verifier and real detached helper; never a public tunnel."""
    import json
    import os
    import subprocess
    import sys
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from types import SimpleNamespace

    import psutil

    shadow = os.environ.get("CODEPLANE_TEST_HANDOFF_SHADOW")
    if shadow:
        import runpy

        runpy.run_path(shadow, run_name="handoff_child_loader")

    from backend.services.dev_restart import restart_helper as rh
    from backend.services.dev_restart.launch_profile import (
        SecretSource,
        build_active_launch_profile,
        write_active_launch_profile,
    )
    from backend.services.dev_restart.restart_protocol import get_request_paths, write_json_atomic

    mode, config_path, role = sys.argv[1:4]
    config = json.loads(Path(config_path).read_text())
    os.environ["CODEPLANE_HOME"] = config["home"]
    request_id = config["request_id"]
    paths = get_request_paths(request_id)
    if mode == "server":
        from starlette.requests import Request

        from backend.services.auth import cf_access, middleware

        cf_access.configure(team="example", aud="audience", eager=False)
        cf_access._jwks_keys = [config["public_key"]]
        cf_access._jwks_fetched_at = time.monotonic()
        policy = middleware.configure_auth(remote=True, access_only=True, password=None)
        app = SimpleNamespace(state=SimpleNamespace(auth_policy=policy))

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                request = Request(
                    {
                        "type": "http",
                        "method": "GET",
                        "path": self.path.split("?")[0],
                        "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
                        "client": ("127.0.0.1", 1),
                        "app": app,
                    }
                )
                authenticated = middleware.is_request_authenticated(request)
                status = 200 if authenticated or self.path == "/api/health" else 401
                self.send_response(status)
                self.end_headers()
                self.wfile.write(json.dumps({"items": [{"id": "job-1"}], "hasMore": False}).encode())

            def do_POST(self):
                request = Request(
                    {
                        "type": "http",
                        "method": "POST",
                        "path": self.path,
                        "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
                        "client": ("127.0.0.1", 1),
                        "app": app,
                    }
                )
                allowed = middleware.is_request_authenticated(request) and self.path == "/api/jobs/job-1/pause"
                if allowed:
                    write_json_atomic(Path(config["home"]) / "mutation.json", {"paused": True})
                self.send_response(204 if allowed else 401)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", config["port"]), Handler)
        profile = build_active_launch_profile(
            executable=sys.executable,
            working_directory=config["root"],
            host="127.0.0.1",
            port=server.server_port,
            dev=True,
            remote=True,
            provider="cloudflare",
            tunnel_ownership="external",
            tunnel_name=None,
            tunnel_origin="https://access.example.test",
            password_source=SecretSource.not_required(),
            tunnel_credential_source=SecretSource.not_required(),
            started_pid=os.getpid(),
            started_process_time=psutil.Process().create_time(),
            auth_mode="access_only",
        )
        write_active_launch_profile(profile)
        write_json_atomic(Path(config["home"]) / f"{role}.json", profile.to_dict())
        if role == "new":
            write_json_atomic(paths.ready, {"requestId": request_id, "pid": os.getpid(), "writtenAt": "fixture"})
        server.serve_forever()
    elif mode == "helper":
        real_popen = subprocess.Popen

        def replacement(args, **kwargs):
            assert args[1:4] == ["-m", "backend.main", "up"]
            assert "--access-only" in args
            assert kwargs["stdin"] == subprocess.DEVNULL
            assert rh._helper_access_token is None
            assert rh._helper_session is None
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            write_json_atomic(Path(config["home"]) / "replacement-channel.json", {"stdin": "DEVNULL", "scrubbed": True})
            child = real_popen([args[0], __file__, "server", config_path, "new"], **kwargs)
            write_json_atomic(
                Path(config["home"]) / "replacement-identity.json",
                {"pid": child.pid, "processTime": psutil.Process(child.pid).create_time()},
            )
            return child

        rh.subprocess.Popen = replacement
        rh.probe_remote_origin = lambda *_: None
        code = rh.run_helper(paths.pending)
        write_json_atomic(Path(config["home"]) / "helper-result.json", {"code": code})
        raise SystemExit(code)
    else:
        write_json_atomic(
            Path(config["home"]) / "parent-identity.json",
            {"pid": os.getpid(), "processTime": psutil.Process().create_time()},
        )
        frame = json.load(sys.stdin)
        # The generated helper shim carries only nonsecret fixture paths.
        helper_path = Path(config["home"]) / "helper-entry.py"
        with (Path(config["home"]) / "helper.log").open("a", encoding="utf-8") as log:
            handle = rh.spawn_detached_helper(
                Path(config["helper_executable"]),
                helper_path,
                paths.pending,
                log,
                handoff=frame,
                adoption_seconds=15,
            )
        frame.clear()
        if not handle.wait_ready(paths):
            handle.close()
            raise SystemExit(4)
        write_json_atomic(
            Path(config["home"]) / "helper-identity.json", {"pid": handle.pid, "processTime": handle.process_time}
        )
        write_json_atomic(Path(config["home"]) / "parent-ready.json", {"ready": True})
        if role == "die":
            while True:
                time.sleep(0.1)
        if not handle.commit(paths):
            raise SystemExit(5)
        os._exit(0)


class TestAccessOnlyRestart:
    @pytest.mark.parametrize("commit", [False, True])
    def test_owned_parent_death_or_committed_restart(self, tmp_path, commit):
        import inspect
        import json
        import subprocess
        import sys
        import time
        import urllib.error
        import urllib.request
        from pathlib import Path

        import jwt
        import psutil
        from cryptography.hazmat.primitives.asymmetric import rsa

        from backend.services.dev_restart import restart_helper as rh
        from backend.services.dev_restart.restart_protocol import RestartTimeouts

        assert "handoff" in inspect.signature(rh.spawn_detached_helper).parameters, (
            "Introduce inherited handoff framing for actual detached-process restart"
        )
        root = Path(__file__).resolve().parents[3]
        executable = getattr(sys, "_base_executable", sys.executable)
        script = tmp_path / "process-fixture.py"
        script.write_text(
            "import sys\nfrom pathlib import Path\n"
            f"sys.path[:0] = {[str(root), *sys.path]!r}\n"
            + inspect.getsource(_access_only_process_fixture)
            + "\n_access_only_process_fixture()\n",
            encoding="utf-8",
        )
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        config_path = tmp_path / "fixture.json"
        config = {
            "home": str(tmp_path),
            "root": str(root),
            "port": 0,
            "request_id": "owned-handoff",
            "helper_executable": sys.executable,
            "public_key": jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True),
        }
        config_path.write_text(json.dumps(config), encoding="utf-8")
        # The helper executable is the real helper flow with fixture-only replacement transport.
        (tmp_path / "helper-entry.py").write_text(
            "import runpy,sys\n"
            f"sys.argv=[{str(script)!r},'helper',{str(config_path)!r},'helper']\n"
            f"runpy.run_path({str(script)!r},run_name='__main__')\n",
            encoding="utf-8",
        )
        identities = []
        processes: list[subprocess.Popen[bytes]] = []
        isolation = (
            {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if sys.platform == "win32"
            else {"start_new_session": True}
        )

        def wait_file(name, seconds=25):
            path = tmp_path / name
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if path.exists():
                    try:
                        return json.loads(path.read_text())
                    except (OSError, json.JSONDecodeError):
                        pass
                time.sleep(0.03)
            states = [process.poll() for process in processes]
            pytest.fail(f"fixture did not publish {name}; owned process exits={states}")

        try:
            with (tmp_path / "old-process.log").open("wb") as output:
                old = subprocess.Popen(
                    [executable, str(script), "server", str(config_path), "old"],
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=output,
                    **isolation,
                )
            processes.append(old)
            old_profile = wait_file("old.json")
            identities.append((old_profile["startedPid"], old_profile["startedProcessTime"]))
            config["port"] = old_profile["port"]
            config_path.write_text(json.dumps(config), encoding="utf-8")
            pending_dir = tmp_path / "dev-restart"
            pending_dir.mkdir(exist_ok=True)
            (pending_dir / "owned-handoff.pending.json").write_text(
                json.dumps(
                    {
                        "requestId": "owned-handoff",
                        "targetSourceRoot": str(root),
                        "launchProfile": old_profile,
                        "timeouts": RestartTimeouts(
                            adoption_seconds=15,
                            response_grace_seconds=0,
                            pause_wait_seconds=0,
                            stop_seconds=5,
                            readiness_seconds=15,
                            remote_probe_seconds=1,
                        ).to_dict(),
                    }
                ),
                encoding="utf-8",
            )
            now = int(time.time())
            token = jwt.encode(
                {
                    "iss": "https://example.cloudflareaccess.com",
                    "aud": "audience",
                    "iat": now,
                    "exp": now + 120,
                },
                key,
                algorithm="RS256",
            )
            frame = {
                "version": 1,
                "kind": "credential",
                "requestId": "owned-handoff",
                "serverPid": old_profile["startedPid"],
                "serverProcessTime": old_profile["startedProcessTime"],
                "origin": old_profile["tunnelOrigin"],
                "jwt": token,
            }
            with (tmp_path / "parent-process.log").open("wb") as output:
                parent = subprocess.Popen(
                    [executable, str(script), "parent", str(config_path), "commit" if commit else "die"],
                    stdin=subprocess.PIPE,
                    stdout=output,
                    stderr=output,
                    **isolation,
                )
            processes.append(parent)
            parent.stdin.write(json.dumps(frame).encode())
            parent.stdin.close()
            parent_identity = wait_file("parent-identity.json")
            identities.append((parent_identity["pid"], parent_identity["processTime"]))
            helper = wait_file("helper-identity.json")
            identities.append((helper["pid"], helper["processTime"]))
            wait_file("parent-ready.json")
            if not commit:
                owner = psutil.Process(parent_identity["pid"])
                assert owner.create_time() == parent_identity["processTime"]
                owner.kill()
            parent.wait(timeout=10)
            result = wait_file("helper-result.json")
            assert result["code"] == (0 if commit else 1)
            assert (tmp_path / "mutation.json").exists() is commit
            if commit:
                replacement = wait_file("new.json")
                identities.append((replacement["startedPid"], replacement["startedProcessTime"]))
                assert replacement["authMode"] == "access_only"
                assert replacement["startedPid"] != old_profile["startedPid"]
                assert wait_file("replacement-channel.json") == {"stdin": "DEVNULL", "scrubbed": True}
                old.wait(timeout=5)
            else:
                assert old.poll() is None
            url = f"http://127.0.0.1:{config['port']}/api/jobs?state=running&limit=1"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as unauthenticated:
                opener.open(url, timeout=2)
            assert unauthenticated.value.code == 401
            with opener.open(
                urllib.request.Request(url, headers={"Cf-Access-Jwt-Assertion": token}), timeout=2
            ) as response:
                assert response.status == 200
            for path in tmp_path.rglob("*"):
                if path.is_file():
                    assert token.encode() not in path.read_bytes(), f"credential leaked into {path.name}"
        finally:
            for name, pid_key, time_key in (
                (Path("dev-restart") / "owned-handoff.claimed.json", "helperPid", "helperProcessTime"),
                ("replacement-identity.json", "pid", "processTime"),
                ("new.json", "startedPid", "startedProcessTime"),
            ):
                try:
                    identity = json.loads((tmp_path / name).read_text())
                    identities.append((identity[pid_key], identity[time_key]))
                except (OSError, ValueError, KeyError):
                    pass
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            for pid, created in identities:
                try:
                    process = psutil.Process(pid)
                    if process.create_time() == created:
                        process.kill()
                except psutil.NoSuchProcess:
                    pass
