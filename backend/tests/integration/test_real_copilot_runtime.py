"""Opt-in real Copilot acceptance with owned runtime and disposable repositories."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import psutil
import pytest
import yaml

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("CODEPLANE_REAL_COPILOT_E2E") != "1", reason="Explicit real-Copilot opt-in required"
    ),
    pytest.mark.timeout(1800),
]

ROOT = Path(__file__).resolve().parents[3]


def _wait(predicate, *, timeout=180, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.5)
    raise AssertionError(f"Timed out: {description}")


class _Runtime:
    def __init__(self, root):
        self.root = root
        self.home = root / "runtime-home"
        self.home.mkdir(parents=True)
        self.copilot_home = root / "copilot-home"
        self.copilot_home.mkdir()
        self.env = dict(os.environ, CODEPLANE_HOME=str(self.home), COPILOT_HOME=str(self.copilot_home))
        assert self.env.get("CODEPLANE_E2E_FAKE_AGENT") != "1", "Fake adapters cannot satisfy real acceptance"
        for key in list(self.env):
            if key.startswith("CPL_") or key == "CODEPLANE_E2E_FAKE_AGENT":
                self.env.pop(key)
        self.repository = root / "fixture"
        self.repository.mkdir()
        self._git("init", "--initial-branch=main")
        self._git("config", "user.name", "CodePlane fixture")
        self._git("config", "user.email", "fixture@localhost.invalid")
        (self.repository / "sum.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (self.repository / "check.py").write_text(
            "from sum import add\nassert add(2, 3) == 5\nprint('FIXTURE_CHECK_PASSED')\n", encoding="utf-8"
        )
        self._git("add", "sum.py", "check.py")
        self._git("commit", "-m", "test: create isolated acceptance fixture")
        self.base = self._git("rev-parse", "HEAD").strip()
        config = {
            "server": {"host": "127.0.0.1", "port": 0},
            "runtime": {"copilot_only": True, "max_concurrent_jobs": 2},
            "completion": {"strategy": "manual", "auto_push": False, "cleanup_worktree": False},
            "logging": {"file": str(root / "server.log"), "level": "info"},
            "repos": [],
        }
        (self.home / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
        self.process = None
        self.profile = None
        self.client = None
        self.logs = []
        self.manifest = {
            "product_head": self._product_head(),
            "working_diff_sha256": hashlib.sha256(
                subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=ROOT)
            ).hexdigest(),
            "fixture_base": self.base,
            "runs": [],
            "jobs": [],
        }

    @staticmethod
    def _product_head():
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def _git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repository, text=True, stderr=subprocess.STDOUT)

    def start(self):
        profile_path = self.home / "run.json"
        old_written = self.profile["writtenAt"] if self.profile else None
        log = (self.root / f"process-{len(self.logs)}.log").open("w", encoding="utf-8")
        self.logs.append(log)
        command = [
            shutil.which("uv"),
            "run",
            "--no-sync",
            "cpl",
            "up",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--no-password",
            "--skip-preflight",
        ]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )

        def ready():
            assert self.process.poll() is None, "Owned CodePlane process exited during startup"
            if not profile_path.exists():
                return None
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            if old_written and profile["writtenAt"] == old_written:
                return None
            pid = profile["startedPid"]
            candidates = [psutil.Process(self.process.pid), *psutil.Process(self.process.pid).children(recursive=True)]
            assert any(item.pid == pid for item in candidates), "Launch profile names an unrelated process"
            process = psutil.Process(pid)
            assert abs(process.create_time() - profile["startedProcessTime"]) < 0.1
            url = f"http://127.0.0.1:{profile['port']}"
            try:
                response = httpx.get(url + "/api/health", timeout=2)
            except httpx.TransportError:
                return None
            if response.status_code != 200:
                return None
            return profile

        self.profile = _wait(ready, timeout=180, description="isolated migrated runtime startup")
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.profile['port']}", timeout=180)
        self.manifest["runs"].append(
            {
                key: self.profile[key]
                for key in ("startedPid", "startedProcessTime", "port", "workingDirectory", "writtenAt")
            }
        )
        with sqlite3.connect(self.home / "data.db") as db:
            revision = db.execute("SELECT version_num FROM alembic_version").fetchone()
            assert revision, "Acceptance must run guarded migrations, not direct schema creation"
            self.manifest["schema_revision"] = revision[0]
        self.save()

    def stop(self, *, crash=False):
        if self.client:
            self.client.close()
            self.client = None
        if not self.process:
            return
        owned = psutil.Process(self.process.pid) if self.process.poll() is None else None
        children = owned.children(recursive=True) if owned else []
        if owned:
            if crash:
                target = psutil.Process(self.profile["startedPid"])
                assert abs(target.create_time() - self.profile["startedProcessTime"]) < 0.1
                target.kill()
            elif os.name == "nt":
                target = psutil.Process(self.profile["startedPid"])
                assert abs(target.create_time() - self.profile["startedProcessTime"]) < 0.1
                stopped = subprocess.run(
                    [
                        shutil.which("uv"),
                        "run",
                        "--no-sync",
                        "cpl",
                        "down",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(self.profile["port"]),
                    ],
                    cwd=ROOT,
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                (self.root / f"stop-{len(self.manifest['runs'])}.log").write_text(
                    stopped.stdout + stopped.stderr, encoding="utf-8"
                )
                self.manifest["runs"][-1]["stop_exit"] = stopped.returncode
            else:
                self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=55)
            except subprocess.TimeoutExpired:
                for child in reversed(children):
                    if child.is_running():
                        child.kill()
                if owned.is_running():
                    owned.kill()
                self.process.wait(timeout=10)
        for log in self.logs:
            log.flush()
        self.manifest["runs"][-1]["stop_requested"] = "crash" if crash else "graceful"
        self.save()
        self.process = None

    def request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def job(self, prompt, **overrides):
        created = self.request(
            "POST",
            "/api/jobs",
            json={
                "repo": str(self.repository),
                "prompt": prompt,
                "verify": True,
                "maxTurns": 1,
                **overrides,
            },
        )
        self.manifest["jobs"].append(created["id"])
        self.save()
        return created["id"]

    def wait_state(self, job_id, states):
        def poll():
            job = self.request("GET", f"/api/jobs/{job_id}")
            assert job["state"] not in ("failed", "canceled") or job["state"] in states, job
            return job if job["state"] in states else None

        return _wait(poll, timeout=300, description=f"{job_id} reaches {states}")

    def save(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest, indent=2), encoding="utf-8")

    def mcp(self, action, **arguments):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async def call():
            async with (
                streamablehttp_client(str(self.client.base_url) + "/mcp/") as (read, write, _),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("codeplane_job", {"action": action, **arguments})
                assert not result.isError, result
                text = next(item.text for item in result.content if item.type == "text")
                parsed = json.loads(text)
                assert "error" not in parsed, parsed
                return parsed

        return asyncio.run(call())

    def assert_artifact_isolation(self):
        expected = (self.home / "artifacts").resolve()
        with sqlite3.connect(f"file:{self.home / 'data.db'}?mode=ro", uri=True) as db:
            artifacts = db.execute("SELECT id, disk_path FROM artifacts").fetchall()
        observed = []
        for artifact_id, disk_path in artifacts:
            path = Path(disk_path).resolve()
            assert path.is_relative_to(expected), f"Artifact {artifact_id} escaped configured home: {path}"
            assert path.is_file(), f"Artifact {artifact_id} is missing: {path}"
            observed.append({"id": artifact_id, "path": str(path)})
        self.manifest["artifact_storage"] = observed
        self.save()

    def close(self):
        try:
            self.stop()
        finally:
            for log in self.logs:
                log.close()
            if "schema_revision" in self.manifest:
                self.assert_artifact_isolation()


@pytest.fixture
def real_runtime():
    supplied = os.environ.get("CODEPLANE_E2E_ROOT")
    assert supplied, "CODEPLANE_E2E_ROOT is required when real acceptance is requested"
    root = Path(supplied).resolve()
    assert root.is_relative_to(Path(r"C:\agent-artifacts").resolve())
    assert not root.is_relative_to(ROOT)
    run_root = root / uuid.uuid4().hex
    run_root.mkdir(parents=True)
    runtime = _Runtime(run_root)
    try:
        runtime.start()
        runtime.request(
            "POST",
            "/api/settings/projects",
            json={"name": "Real Copilot acceptance", "repoPaths": [str(runtime.repository)]},
        )
        yield runtime
    finally:
        runtime.close()


def test_managed_chat_and_restart_evidence(real_runtime):
    runtime = real_runtime
    failed = subprocess.run([sys.executable, "check.py"], cwd=runtime.repository, capture_output=True, text=True)
    assert failed.returncode != 0
    job_id = runtime.job(
        "Fix add() in sum.py, run check.py, and report the exact command result. Do not push or merge."
    )
    job = runtime.wait_state(job_id, {"review"})
    worktree = Path(job["worktreePath"]).resolve()
    assert worktree != runtime.repository
    check = subprocess.run([sys.executable, "check.py"], cwd=worktree, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert "FIXTURE_CHECK_PASSED" in check.stdout
    snapshot = runtime.request("GET", f"/api/jobs/{job_id}/snapshot")
    assert snapshot["transcript"] and snapshot["diff"]
    assert snapshot["verificationResults"]
    (runtime.root / "managed-snapshot.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    chat = runtime.request("POST", "/api/chats", json={"title": "Discussion-only acceptance"})
    turn = runtime.request(
        "POST",
        f"/api/chats/{chat['id']}/turns",
        json={"content": "Remember the fixture word CITRUS for our discussion. Acknowledge it briefly."},
    )
    assert turn["state"] == "assistant"
    turn = runtime.request(
        "POST",
        f"/api/chats/{chat['id']}/turns",
        json={"content": "What fixture word did I ask you to remember? Reply with that word only."},
    )
    assert turn["state"] == "assistant"
    assert "CITRUS" in turn["assistantMessage"]["content"]
    runtime.stop()
    runtime.start()
    restored = runtime.request("GET", f"/api/jobs/{job_id}/snapshot")
    assert restored["job"]["state"] == "review"
    assert restored["verificationResults"] == snapshot["verificationResults"]
    messages = runtime.request("GET", f"/api/chats/{chat['id']}/messages")
    assert len(messages) == 4
    assert runtime._git("rev-parse", "HEAD").strip() == runtime.base
    assert (runtime.repository / "sum.py").read_text(encoding="utf-8") == "def add(a, b):\n    return a - b\n"


def test_approval_recovery_and_mcp_cancellation(real_runtime):
    runtime = real_runtime
    gate_prompt = (
        "In this disposable fixture, execute exactly git rebase --show-current-patch once. "
        "This is an explicitly requested read-only diagnostic, not permission to rebase. "
        "If permission is denied, stop and report that fact. Do not substitute another command."
    )
    job_id = runtime.job(gate_prompt, verify=False, preset="locked")
    runtime.wait_state(job_id, {"waiting_for_approval"})
    before = runtime.request("GET", f"/api/jobs/{job_id}/approvals")["items"]
    assert any(item["resolution"] is None and item["requiresExplicitApproval"] for item in before)
    runtime.stop(crash=True)
    runtime.start()
    runtime.wait_state(job_id, {"waiting_for_approval"})
    runtime.request("POST", f"/api/jobs/{job_id}/approvals/trust")
    pending = runtime.request("GET", f"/api/jobs/{job_id}/approvals")["items"]
    current = [item for item in pending if item["resolution"] is None]
    assert current and all(item["requiresExplicitApproval"] for item in current)
    for item in current:
        runtime.request("POST", f"/api/approvals/{item['id']}/resolve", json={"resolution": "approved"})
    runtime.wait_state(job_id, {"review"})
    restored = runtime.request("GET", f"/api/jobs/{job_id}/snapshot")
    approvals = runtime.request("GET", f"/api/jobs/{job_id}/approvals")["items"]
    assert {item["id"] for item in restored["approvals"]} == {item["id"] for item in approvals}
    for item in restored["approvals"]:
        durable = next(approval for approval in approvals if approval["id"] == item["id"])
        assert item["requiresExplicitApproval"] == durable["requiresExplicitApproval"]
        assert item["notes"] == durable["notes"]
    assert any(item["requiresExplicitApproval"] and item["resolution"] == "approved" for item in restored["approvals"])
    (runtime.root / "approval-recovery.json").write_text(json.dumps(restored, indent=2), encoding="utf-8")

    denied = runtime.job(gate_prompt, verify=False, preset="locked")
    runtime.wait_state(denied, {"waiting_for_approval"})
    approval = next(
        item for item in runtime.request("GET", f"/api/jobs/{denied}/approvals")["items"] if item["resolution"] is None
    )
    runtime.request("POST", f"/api/approvals/{approval['id']}/resolve", json={"resolution": "rejected"})
    runtime.wait_state(denied, {"failed"})

    created = runtime.mcp(
        "create",
        repo=str(runtime.repository),
        prompt="Read sum.py, then wait for the next operator instruction. Do not change files.",
    )
    runtime.manifest["jobs"].append(created["id"])
    runtime.wait_state(created["id"], {"running", "review"})
    result = runtime.mcp("cancel", job_id=created["id"])
    assert result["state"] == "canceled"
    runtime.wait_state(created["id"], {"canceled"})
    runtime.save()


def _verify_native_round_trip(runtime, session_id):
    path = runtime.copilot_home / "session-state" / session_id / "events.jsonl"

    def records():
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def imported():
        jobs = runtime.request("GET", "/api/jobs")["items"]
        return next((job for job in jobs if job.get("externalSessionId") == session_id), None)

    observed = _wait(imported, timeout=120, description="current supported native control discovery")

    def persisted_responses(*markers):
        snapshot = runtime.request("GET", f"/api/jobs/{observed['id']}/snapshot")
        messages = [item["content"] for item in snapshot["transcript"] if item["kind"] == "message.assistant"]
        return snapshot if all(any(marker in content for content in messages) for marker in markers) else None

    _wait(
        lambda: persisted_responses("NATIVE_ACCEPTANCE_READY"),
        timeout=120,
        description="native assistant response persisted in CodePlane",
    )
    runtime.request(
        "POST",
        f"/api/jobs/{observed['id']}/messages",
        json={"content": "Reply exactly NATIVE_STEERING_RECEIVED. Do not use tools."},
    )
    _wait(
        lambda: any(
            "NATIVE_STEERING_RECEIVED" in str(e.get("data")) for e in records() if e["type"] == "assistant.message"
        ),
        timeout=180,
        description="actual native steering execution",
    )
    runtime.mcp("message", job_id=observed["id"], content="Reply exactly NATIVE_MCP_RECEIVED. Use no tools.")
    _wait(
        lambda: any("NATIVE_MCP_RECEIVED" in str(e.get("data")) for e in records() if e["type"] == "assistant.message"),
        timeout=180,
        description="actual native MCP steering execution",
    )
    expected = ("NATIVE_ACCEPTANCE_READY", "NATIVE_STEERING_RECEIVED", "NATIVE_MCP_RECEIVED")
    snapshot = _wait(
        lambda: persisted_responses(*expected), timeout=120, description="all real native replies persisted"
    )
    (runtime.root / "native-before-restart.json").write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    with sqlite3.connect(runtime.home / "data.db") as db:
        ids = [row[0] for row in db.execute("SELECT event_id FROM events WHERE job_id=?", (observed["id"],))]
    assert ids and len(ids) == len(set(ids))
    runtime.stop()
    runtime.start()
    restored = _wait(
        lambda: persisted_responses(*expected), timeout=120, description="native replies preserved after restart"
    )
    (runtime.root / "native-after-restart.json").write_text(json.dumps(restored, indent=2), encoding="utf-8")
    with sqlite3.connect(runtime.home / "data.db") as db:
        after = [row[0] for row in db.execute("SELECT event_id FROM events WHERE job_id=?", (observed["id"],))]
    assert set(ids) <= set(after)
    assert len(after) == len(set(after))
    runtime.request("POST", f"/api/jobs/{observed['id']}/cancel")
    runtime.wait_state(observed["id"], {"canceled"})


def test_native_capability_is_real_and_observed(real_runtime):
    """The visible terminal operator owns CLI creation, control changes and restoration."""
    from datetime import UTC, datetime

    runtime = real_runtime
    session_id = str(uuid.uuid4())
    events_path = runtime.copilot_home / "session-state" / session_id / "events.jsonl"
    assert not events_path.exists(), "Native acceptance requires a fresh session after backend readiness"
    with sqlite3.connect(runtime.home / "data.db") as db:
        managed = [
            Path(path).resolve()
            for (paths,) in db.execute("SELECT repo_paths FROM projects")
            for path in json.loads(paths)
        ]
    assert managed == [runtime.repository.resolve()]

    executable = shutil.which("copilot")
    assert executable
    launcher = [executable]
    if executable.lower().endswith(".cmd"):
        node = shutil.which("node")
        assert node
        launcher = [node, str(Path(executable).parent / "node_modules" / "@github" / "copilot" / "npm-loader.js")]
    ready_at = datetime.now(UTC)
    ready = {
        "session_id": session_id,
        "ready_at": ready_at.isoformat(),
        "backend_pid": runtime.profile["startedPid"],
        "backend_port": runtime.profile["port"],
        "working_directory": str(runtime.repository),
        "copilot_home": str(runtime.copilot_home),
        "argv": [
            *launcher,
            "-C",
            str(runtime.repository),
            "--session-id",
            session_id,
            "--no-custom-instructions",
            "--disable-builtin-mcps",
            "--no-auto-update",
            "--available-tools=",
            "--no-ask-user",
            "--screen-reader",
            "--no-color",
            "-i",
            "Reply exactly NATIVE_ACCEPTANCE_READY. Use no tools.",
        ],
    }
    (runtime.root / "native-ready.json").write_text(json.dumps(ready, indent=2), encoding="utf-8")
    print(f"NATIVE_READY {runtime.root / 'native-ready.json'}", flush=True)
    published = None
    result = {"session_id": session_id, "status": "waiting_for_visible_operator"}
    try:
        published_path = runtime.root / "native-published.json"
        _wait(published_path.exists, timeout=600, description="visible operator publication receipt")
        published = json.loads(published_path.read_text(encoding="utf-8"))
        assert published["session_id"] == session_id
        assert published["steering_enabled"] is True
        assert published["control_before_text"] and published["control_after_text"]
        assert published["account_setting_observation"] in {"unchanged", "unknown", "temporarily_changed"}
        first = json.loads(events_path.read_text(encoding="utf-8").splitlines()[0])
        assert first["type"] == "session.start" and first["data"]["sessionId"] == session_id
        assert datetime.fromisoformat(first["data"]["startTime"]) >= ready_at
        assert first["data"]["copilotVersion"] == published["native_cli_version"]
        runtime.manifest["native_publication"] = published
        runtime.save()
        _verify_native_round_trip(runtime, session_id)
        result["status"] = "control_checks_passed"
    except Exception as exc:
        result["status"] = "control_checks_failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        (runtime.root / "native-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"NATIVE_RESTORE_STEERING {runtime.root / 'native-result.json'}", flush=True)
        if published is not None:
            restored_path = runtime.root / "native-restored.json"
            _wait(restored_path.exists, timeout=180, description="visible operator steering-disabled receipt")
            restored = json.loads(restored_path.read_text(encoding="utf-8"))
            assert restored["session_id"] == session_id
            assert restored["steering_enabled"] is False
            assert restored["control_after_text"]
            runtime.manifest["native_restoration"] = restored
            runtime.save()
