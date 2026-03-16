#!/usr/bin/env python3
"""
dev_restart.py — Graceful CodePlane server restart for frontend development.

Intended for use by agents working on this repository. Pauses all running
agent sessions, rebuilds the frontend, restarts the CodePlane server, then
resumes the sessions that were paused.

Usage:
    python tools/dev_restart.py [--port 8080] [--host 127.0.0.1] [--pause-wait 10]

The script exits non-zero if the frontend build fails, in which case the
server is NOT restarted so the existing instance keeps running.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = REPO_ROOT / "frontend"

# Jobs in these states will be collected before restart and resumed after.
# waiting_for_approval jobs are already paused — we don't send a pause signal
# but we do resume them once the server is back up.
_RESUMABLE_STATES = ("running", "waiting_for_approval")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _request(method: str, url: str, body: dict | None = None) -> tuple[int, dict | None]:
    """Perform an HTTP request; returns (status_code, parsed_body | None)."""
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=10) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return resp.status, None
    except URLError:
        return 0, None


def list_jobs(base_url: str, state: str) -> list[dict]:
    """Return jobs in the given state (handles pagination)."""
    results: list[dict] = []
    cursor: str | None = None
    while True:
        path = f"/api/jobs?state={state}&limit=100"
        if cursor:
            path += f"&cursor={cursor}"
        status, body = _request("GET", f"{base_url}{path}")
        if status != 200 or not body:
            break
        results.extend(body.get("items", []))
        if not body.get("hasMore"):
            break
        cursor = body.get("cursor")
    return results


def pause_job(base_url: str, job_id: str) -> bool:
    """Send a pause signal to a running job. Returns True on success."""
    status, _ = _request("POST", f"{base_url}/api/jobs/{job_id}/pause")
    return status == 204


def resume_job(base_url: str, job_id: str) -> bool:
    """Resume a failed job after server restart. Returns True on success."""
    instruction = (
        "Resuming after a scheduled CodePlane server restart (frontend rebuild). "
        "Please continue exactly where you left off."
    )
    status, _ = _request("POST", f"{base_url}/api/jobs/{job_id}/resume", {"instruction": instruction})
    return status == 200


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------


def _find_pids_on_port(port: int) -> list[int]:
    """Return PIDs of processes listening on the given TCP port."""
    # Try lsof first (available on most POSIX systems)
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [int(p) for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
    except FileNotFoundError:
        pass

    # Fallback: ss (Linux)
    try:
        import re

        result = subprocess.run(
            ["ss", "-tlnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
        )
        pids = re.findall(r"pid=(\d+)", result.stdout)
        return [int(p) for p in pids]
    except Exception:
        pass

    return []


def stop_server(port: int, graceful_timeout: int = 15) -> bool:
    """SIGTERM the server, wait for it to stop, SIGKILL if needed."""
    pids = _find_pids_on_port(port)
    if not pids:
        print("  No process found listening on that port — already stopped?")
        return True

    print(f"  Found process(es) on port {port}: PIDs {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"  Sent SIGTERM to PID {pid}")
        except ProcessLookupError:
            pass

    deadline = time.monotonic() + graceful_timeout
    while time.monotonic() < deadline:
        if not _find_pids_on_port(port):
            print("  Server stopped gracefully.")
            return True
        time.sleep(0.5)

    # Force-kill anything still running
    print("  Graceful timeout reached — sending SIGKILL.")
    for pid in _find_pids_on_port(port):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
    time.sleep(1)
    still_up = _find_pids_on_port(port)
    if still_up:
        print(f"  WARNING: process(es) {still_up} still alive after SIGKILL.")
        return False
    return True


def start_server(host: str, port: int) -> int:
    """Start CodePlane in the background, detached from this process. Returns PID."""
    proc = subprocess.Popen(
        ["uv", "run", "cpl", "up", "--host", host, "--port", str(port)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach: survives after this script exits
    )
    return proc.pid


def wait_for_health(base_url: str, timeout: int = 60) -> bool:
    """Poll /health until the server responds 200."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _ = _request("GET", f"{base_url}/health")
        if status == 200:
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------


def build_frontend() -> bool:
    """Run `npm run build` in the frontend directory. Streams output live."""
    result = subprocess.run(["npm", "run", "build"], cwd=FRONTEND_DIR)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gracefully restart CodePlane for frontend development.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", default="127.0.0.1", help="CodePlane bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8080, help="CodePlane port (default: 8080)")
    parser.add_argument(
        "--pause-wait",
        type=int,
        default=10,
        metavar="SECONDS",
        help="Seconds to wait after pausing before stopping the server (default: 10)",
    )
    args = parser.parse_args()
    base_url = f"http://{args.host}:{args.port}"

    # ------------------------------------------------------------------
    # 1. Collect active sessions
    # ------------------------------------------------------------------
    print("\n[1/6] Checking for active agent sessions…")
    active_jobs: list[dict] = []
    for state in _RESUMABLE_STATES:
        jobs = list_jobs(base_url, state)
        active_jobs.extend(jobs)

    running_jobs = [j for j in active_jobs if j.get("state") == "running"]
    paused_jobs = [j for j in active_jobs if j.get("state") == "waiting_for_approval"]

    if active_jobs:
        print(f"  {len(running_jobs)} running, {len(paused_jobs)} waiting for approval")
        for j in active_jobs:
            print(f"    • {j['id'][:8]}… {j.get('state'):24s} {j.get('title') or '(untitled)'}")
    else:
        print("  No active sessions found.")

    # ------------------------------------------------------------------
    # 2. Pause running sessions
    # ------------------------------------------------------------------
    if running_jobs:
        print(f"\n[2/6] Pausing {len(running_jobs)} running session(s)…")
        for job in running_jobs:
            ok = pause_job(base_url, job["id"])
            mark = "✓" if ok else "✗ (pause signal failed — will still be recovered on restart)"
            print(f"  {mark}  {job['id'][:8]}…")
        print(f"  Waiting {args.pause_wait}s for agents to reach a stopping point…")
        time.sleep(args.pause_wait)
    else:
        print("\n[2/6] No running sessions to pause.")

    # ------------------------------------------------------------------
    # 3. Stop the server
    # ------------------------------------------------------------------
    print(f"\n[3/6] Stopping CodePlane server on port {args.port}…")
    stop_server(args.port)

    # ------------------------------------------------------------------
    # 4. Rebuild frontend
    # ------------------------------------------------------------------
    print("\n[4/6] Building frontend…")
    if not build_frontend():
        print("\n  ✗ Frontend build failed. The server has NOT been restarted.")
        print("    Fix the build errors and run this script again.")
        print("    (You may also restart the server manually: uv run cpl up)\n")
        sys.exit(1)
    print("  ✓ Frontend build succeeded.")

    # ------------------------------------------------------------------
    # 5. Start the server
    # ------------------------------------------------------------------
    print(f"\n[5/6] Starting CodePlane server ({args.host}:{args.port})…")
    pid = start_server(args.host, args.port)
    print(f"  Server started (PID {pid}). Waiting for health check…")

    if not wait_for_health(base_url):
        print("  ✗ Server did not become healthy within 60 s.")
        print("    Check the CodePlane logs for errors.")
        sys.exit(1)
    print("  ✓ Server is healthy.")

    # ------------------------------------------------------------------
    # 6. Resume previously active sessions
    # ------------------------------------------------------------------
    if active_jobs:
        print(f"\n[6/6] Resuming {len(active_jobs)} session(s)…")
        for job in active_jobs:
            ok = resume_job(base_url, job["id"])
            mark = "✓" if ok else "✗ (resume failed — you may need to resume manually)"
            print(f"  {mark}  {job['id'][:8]}… {job.get('title') or '(untitled)'}")
    else:
        print("\n[6/6] No sessions to resume.")

    print("\n✓ Done. CodePlane is running with the latest frontend build.\n")


if __name__ == "__main__":
    main()
