#!/usr/bin/env python3
"""Streamlined runner for cpl-bench EVEE evaluations.

Validates prerequisites, configures the environment, and invokes EVEE.

Usage:
    # Recon benchmark (default)
    python setup_and_run.py /path/to/target/repo

    # Agent A/B benchmark
    python setup_and_run.py /path/to/target/repo --experiment agent-ab

    # Custom port / timeout
    python setup_and_run.py /path/to/target/repo --port 8888 --timeout 180
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import httpx
import yaml

# ── Constants ────────────────────────────────────────────────────────

BENCH_DIR = Path(__file__).resolve().parent
EXPERIMENTS = {
    "recon": BENCH_DIR / "experiments" / "recon_baseline.yaml",
    "agent-ab": BENCH_DIR / "experiments" / "agent_ab.yaml",
}

# ── Validation ───────────────────────────────────────────────────────


def _resolve_repo(repo_arg: str) -> Path:
    """Resolve and validate the target repo path."""
    repo = Path(repo_arg).expanduser().resolve()
    if not repo.is_dir():
        _fail(f"Repository path does not exist: {repo}")
    if not (repo / ".git").exists():
        _fail(f"Not a git repository: {repo}")
    return repo


def _check_codeplane_init(repo: Path) -> None:
    """Verify .codeplane directory exists."""
    if not (repo / ".codeplane").is_dir():
        _fail(f"CodePlane not initialized in {repo}\n  Run:  cd {{repo}} && cpl init && cpl up")


def _check_daemon(port: int) -> dict:
    """Check the CodePlane daemon is running and reachable."""
    health_url = f"http://127.0.0.1:{port}/health"
    try:
        r = httpx.get(health_url, timeout=5)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        _fail(
            f"CodePlane daemon not reachable on port {port}\n"
            f"  Start it:  cd <repo> && cpl up --port {port}"
        )
    except httpx.HTTPStatusError as e:
        _fail(f"Daemon health check failed: {e.response.status_code}")
    return {}  # unreachable, keeps type checker happy


def _check_ground_truth() -> None:
    """Verify ground truth data exists for recon benchmarks."""
    gt = BENCH_DIR / "data" / "ground_truth.json"
    if not gt.exists():
        _fail(f"Ground truth not found: {gt}")
    with open(gt) as f:
        records = json.load(f)
    _info(f"Ground truth: {len(records)} records")


def _check_traces(traces_dir: str) -> None:
    """Verify trace files exist for agent A/B benchmarks."""
    td = Path(traces_dir)
    if not td.is_dir():
        _fail(
            f"Traces directory not found: {td.resolve()}\n"
            "  Convert chatreplay exports first:\n"
            "    python -m benchmarking.cpl_bench.preprocessing.chatreplay_to_traces \\\n"
            "      path/to/*.json --repo <name>"
        )
    traces = list(td.glob("*_trace.json"))
    if not traces:
        _fail(f"No *_trace.json files in {td.resolve()}")
    _info(f"Traces: {len(traces)} files in {td}")


# ── Config patching ──────────────────────────────────────────────────


def _patch_config(config_path: Path, port: int, timeout: int) -> Path:
    """Patch experiment YAML with runtime port/timeout, return path to patched config."""
    with open(config_path) as f:
        config = yaml.safe_load(f)

    experiment = config.get("experiment", {})

    # Patch model args for recon model
    for model_cfg in experiment.get("models", []):
        if model_cfg.get("name") == "cpl-recon":
            model_cfg["args"] = [{"daemon_port": [port], "timeout": [timeout]}]

    # Write patched config to a temp file so the original stays clean
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="cpl_bench_", delete=False
    ) as patched:
        yaml.safe_dump(config, patched, default_flow_style=False, sort_keys=False)
    return Path(patched.name)


# ── EVEE invocation ──────────────────────────────────────────────────


def _run_evee(config_path: Path) -> None:
    """Register components and invoke EVEE evaluator."""
    # Ensure cpl_bench package root is importable
    sys.path.insert(0, str(BENCH_DIR))

    # Register EVEE components (decorator side-effects)
    import datasets  # noqa: F401  # isort: skip
    import metrics  # noqa: F401  # isort: skip
    import models  # noqa: F401  # isort: skip

    from evee.evaluation.evaluate import main

    _info(f"Running: {config_path.name}")
    _info("─" * 50)
    main(str(config_path))


# ── CLI ──────────────────────────────────────────────────────────────


def _info(msg: str) -> None:
    print(f"  ✓ {msg}")


def _fail(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)
    sys.exit(1)


def main_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Set up and run cpl-bench EVEE evaluations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python setup_and_run.py ~/repos/evee\n"
            "  python setup_and_run.py ~/repos/evee --experiment agent-ab\n"
            "  python setup_and_run.py ~/repos/evee --port 8888 --timeout 180\n"
        ),
    )
    parser.add_argument(
        "repo",
        help="Path to the target repository (must have CodePlane initialized)",
    )
    parser.add_argument(
        "--experiment",
        choices=list(EXPERIMENTS.keys()),
        default="recon",
        help="Which experiment to run (default: recon)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7777,
        help="CodePlane daemon port (default: 7777)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="MCP call timeout in seconds (default: 120)",
    )
    args = parser.parse_args()

    print("\ncpl-bench setup")
    print("=" * 50)

    # 1. Resolve and validate repo
    repo = _resolve_repo(args.repo)
    _info(f"Repository: {repo}")

    # 2. Check CodePlane init
    _check_codeplane_init(repo)
    _info("CodePlane initialized")

    # 3. Check daemon is running
    health = _check_daemon(args.port)
    idx_status = health.get("index", {}).get("status", "unknown")
    _info(f"Daemon: running on port {args.port} (index: {idx_status})")

    # 4. Set env var for the recon model
    os.environ["CPL_BENCH_TARGET_REPO"] = str(repo)
    _info(f"CPL_BENCH_TARGET_REPO={repo}")

    # 5. Experiment-specific validation
    experiment = args.experiment
    config_path = EXPERIMENTS[experiment]

    if experiment == "recon":
        _check_ground_truth()
    elif experiment == "agent-ab":
        # Read traces_dir from the config
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        traces_dir = (
            cfg.get("experiment", {})
            .get("dataset", {})
            .get("args", {})
            .get("traces_dir", "data/traces")
        )
        _check_traces(traces_dir)

    # 6. Patch config with runtime args
    patched_config = _patch_config(config_path, args.port, args.timeout)
    _info(f"Config: {config_path.name} (port={args.port}, timeout={args.timeout})")

    # 7. Run
    print()
    print("Running evaluation")
    print("=" * 50)

    try:
        # chdir so relative paths in configs resolve correctly
        original_cwd = os.getcwd()
        os.chdir(BENCH_DIR)
        _run_evee(patched_config)
    finally:
        os.chdir(original_cwd)
        # Clean up patched config
        patched_config.unlink(missing_ok=True)

    print()
    print("=" * 50)
    output_dir = BENCH_DIR / "experiments" / "output"
    _info(f"Results in: {output_dir}")


if __name__ == "__main__":
    main_cli()
