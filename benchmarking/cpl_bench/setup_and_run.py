#!/usr/bin/env python3
"""Automated setup and run for cpl-bench EVEE evaluations.

Handles everything: init, daemon start, env config, EVEE invocation.

Usage:
    python setup_and_run.py /path/to/target/repo
    python setup_and_run.py /path/to/target/repo --experiment agent-ab
    python setup_and_run.py /path/to/target/repo --port 8888
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import yaml

# ── Constants ────────────────────────────────────────────────────────

BENCH_DIR = Path(__file__).resolve().parent
EXPERIMENTS = {
    "recon": BENCH_DIR / "experiments" / "recon_baseline.yaml",
    "agent-ab": BENCH_DIR / "experiments" / "agent_ab.yaml",
}
HEALTH_POLL_INTERVAL = 2
HEALTH_POLL_TIMEOUT = 120  # seconds to wait for daemon to become healthy


# ── Helpers ──────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    print(f"  → {msg}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _die(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)
    sys.exit(1)


# ── Setup steps ──────────────────────────────────────────────────────


def _resolve_repo(repo_arg: str) -> Path:
    repo = Path(repo_arg).expanduser().resolve()
    if not repo.is_dir():
        _die(f"Path does not exist: {repo}")
    if not (repo / ".git").exists():
        _die(f"Not a git repository: {repo}")
    return repo


def _ensure_init(repo: Path, port: int) -> None:
    """Run `cpl init` if .codeplane doesn't exist yet."""
    if (repo / ".codeplane").is_dir():
        _ok("Already initialized")
        return
    _log(f"Initializing CodePlane in {repo} ...")
    result = subprocess.run(
        ["cpl", "init", "--port", str(port), str(repo)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _die(f"cpl init failed:\n{result.stderr}")
    _ok("Initialized")


def _daemon_healthy(port: int) -> bool:
    """Quick health check — returns True if daemon is up and index is ready."""
    try:
        r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.TimeoutException):
        return False


def _ensure_daemon(repo: Path, port: int) -> None:
    """Start the daemon if it isn't already running, then wait for health."""
    if _daemon_healthy(port):
        _ok(f"Daemon already running on port {port}")
        return

    _log(f"Starting daemon on port {port} ...")
    # Start `cpl up` in background — it runs as a foreground server,
    # so we launch it as a detached subprocess.
    log_path = repo / ".codeplane" / "bench_daemon.log"
    log_file = open(log_path, "w")  # noqa: SIM115
    subprocess.Popen(
        ["cpl", "up", "--port", str(port), str(repo)],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )

    # Poll until healthy
    deadline = time.monotonic() + HEALTH_POLL_TIMEOUT
    while time.monotonic() < deadline:
        if _daemon_healthy(port):
            _ok(f"Daemon healthy on port {port}")
            return
        time.sleep(HEALTH_POLL_INTERVAL)

    _die(f"Daemon did not become healthy within {HEALTH_POLL_TIMEOUT}s.\n  Check logs: {log_path}")


def _patch_config(config_path: Path, port: int, timeout: int) -> Path:
    """Patch experiment YAML with runtime port/timeout, return temp config path."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    for model_cfg in config.get("experiment", {}).get("models", []):
        if model_cfg.get("name") == "cpl-recon":
            model_cfg["args"] = [{"daemon_port": [port], "timeout": [timeout]}]

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="cpl_bench_", delete=False
    ) as patched:
        yaml.safe_dump(config, patched, default_flow_style=False, sort_keys=False)
    return Path(patched.name)


def _validate_data(experiment: str, config_path: Path) -> None:
    """Check that required data files exist for the chosen experiment."""
    if experiment == "recon":
        gt = BENCH_DIR / "data" / "ground_truth.json"
        if not gt.exists():
            _die(f"Ground truth not found: {gt}")
        with open(gt) as f:
            records = json.load(f)
        _ok(f"Ground truth: {len(records)} records")

    elif experiment == "agent-ab":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        traces_dir = Path(
            cfg.get("experiment", {})
            .get("dataset", {})
            .get("args", {})
            .get("traces_dir", "data/traces")
        )
        if not traces_dir.is_dir():
            _die(
                f"No traces directory: {traces_dir.resolve()}\n  Run chatreplay_to_traces.py first."
            )
        traces = list(traces_dir.glob("*_trace.json"))
        if not traces:
            _die(f"No *_trace.json files in {traces_dir.resolve()}")
        _ok(f"Traces: {len(traces)} files")


def _run_evee(config_path: Path) -> None:
    """Register components and invoke EVEE evaluator."""
    sys.path.insert(0, str(BENCH_DIR))

    import datasets  # noqa: F401  # isort: skip
    import metrics  # noqa: F401  # isort: skip
    import models  # noqa: F401  # isort: skip

    from evee.evaluation.evaluate import main

    main(str(config_path))


# ── Main ─────────────────────────────────────────────────────────────


def main_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Automated setup and run for cpl-bench EVEE evaluations",
    )
    parser.add_argument("repo", help="Path to the target repository")
    parser.add_argument(
        "--experiment",
        choices=list(EXPERIMENTS.keys()),
        default="recon",
        help="Experiment to run (default: recon)",
    )
    parser.add_argument("--port", type=int, default=7777, help="Daemon port (default: 7777)")
    parser.add_argument(
        "--timeout", type=int, default=120, help="MCP call timeout in seconds (default: 120)"
    )
    args = parser.parse_args()

    config_path = EXPERIMENTS[args.experiment]

    print(f"\ncpl-bench: {args.experiment}")
    print("=" * 50)

    # 1. Resolve repo
    repo = _resolve_repo(args.repo)
    _ok(f"Repo: {repo}")

    # 2. Init if needed
    _ensure_init(repo, args.port)

    # 3. Start daemon if needed, wait for healthy
    _ensure_daemon(repo, args.port)

    # 4. Set env
    os.environ["CPL_BENCH_TARGET_REPO"] = str(repo)

    # 5. Check data files
    _validate_data(args.experiment, config_path)

    # 6. Patch config with runtime args
    patched = _patch_config(config_path, args.port, args.timeout)

    # 7. Run
    print()
    print("Running evaluation")
    print("=" * 50)
    try:
        original_cwd = os.getcwd()
        os.chdir(BENCH_DIR)
        _run_evee(patched)
    finally:
        os.chdir(original_cwd)
        patched.unlink(missing_ok=True)

    print()
    _ok(f"Results: {BENCH_DIR / 'experiments' / 'output'}")


if __name__ == "__main__":
    main_cli()
