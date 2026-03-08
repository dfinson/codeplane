#!/usr/bin/env python3
"""Batch index all ranking repos via Azure Container Instances.

Reads repo URL + commit from md files, launches ACI jobs in parallel
batches, monitors completion, fetches logs + index artifacts, and
installs them into local clones.

Usage:
    # First: terraform apply in ranking/infra/
    # Then:  build + push the indexer image (see Dockerfile)
    # Then:
    python -m ranking.infra.batch_index --parallelism 10
    python -m ranking.infra.batch_index --parallelism 10 --set cutoff
    python -m ranking.infra.batch_index --repo python-fastapi  # single repo
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# ── Configuration (from terraform outputs) ───────────────────────

# These must be set after `terraform output` — or passed via env/args
DEFAULTS = {
    "resource_group": "rg-cpl-idx",
    "acr_login_server": "",  # filled from terraform output
    "image": "",  # filled from terraform output
    "storage_account": "",
    "storage_container": "indexes",
    "identity_id": "",
    "location": "eastus",
    "cpu": 4,
    "memory_gb": 8,
    "timeout_minutes": 60,
}


@dataclass
class Repo:
    repo_id: str
    url: str
    commit: str
    set_name: str  # ranker-gate, cutoff, eval
    clone_dir: str


@dataclass
class JobResult:
    repo: Repo
    aci_name: str
    state: str = "unknown"
    exit_code: int | None = None
    duration_sec: int = 0
    logs: str = ""
    error: str = ""
    profile: dict = field(default_factory=dict)


def load_terraform_outputs(infra_dir: Path) -> dict:
    """Load terraform outputs as config."""
    try:
        out = subprocess.check_output(
            ["terraform", "output", "-json"],
            cwd=str(infra_dir), text=True, stderr=subprocess.DEVNULL
        )
        raw = json.loads(out)
        return {k: v["value"] for k, v in raw.items()}
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
        return {}


def get_repos(repo_root: Path, set_filter: str | None = None, repo_filter: str | None = None) -> list[Repo]:
    """Discover repos from clones directory (not md files).

    Reads URL + commit from the clone's git config and HEAD.
    Falls back to md file metadata if clone has no remote.
    """
    repos = []
    clones_root = repo_root / "ranking" / "clones"

    for set_name in ["ranker-gate", "cutoff", "eval"]:
        if set_filter and set_name != set_filter:
            continue
        set_dir = clones_root / set_name
        if not set_dir.exists():
            continue
        for clone_dir in sorted(set_dir.iterdir()):
            if not (clone_dir / ".git").exists():
                continue
            clone_name = clone_dir.name
            if repo_filter and repo_filter not in clone_name:
                continue

            # Get commit from HEAD
            try:
                commit = subprocess.check_output(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(clone_dir), text=True, stderr=subprocess.DEVNULL
                ).strip()
            except subprocess.CalledProcessError:
                continue

            # Try to get URL from md file (clones have origin removed)
            url = _find_url_from_md(repo_root, set_name, clone_name)
            if not url:
                continue

            repos.append(Repo(
                repo_id=clone_name,
                url=url,
                commit=commit,
                set_name=set_name,
                clone_dir=f"ranking/clones/{set_name}/{clone_name}",
            ))
    return repos


def _find_url_from_md(repo_root: Path, set_name: str, clone_name: str) -> str | None:
    """Find the GitHub URL for a clone by searching md files."""
    repos_dir = repo_root / "ranking" / "repos" / set_name
    for md in repos_dir.glob("*.md"):
        if md.name == "README.md":
            continue
        text = md.read_text()
        url_m = re.search(r'\*\*URL\*\*\s*\|\s*(https://\S+)', text)
        if not url_m:
            url_m = re.search(r'\|\s*URL\s*\|\s*(https://\S+)', text)
        if url_m:
            url = url_m.group(1).rstrip(" |").rstrip("/")
            if url.rsplit("/", 1)[-1] == clone_name:
                return url
    return None


def az(*args: str, check: bool = True) -> str:
    """Run az CLI and return stdout."""
    cmd = ["az"] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"az {' '.join(args[:3])}... failed: {result.stderr[:200]}")
    return result.stdout.strip()


def _list_existing_containers(config: dict) -> set[str]:
    """List existing ACI container names in the resource group."""
    try:
        out = az(
            "container", "list",
            "-g", config["resource_group"],
            "--query", "[].name",
            "-o", "tsv",
            check=False,
        )
        return set(out.strip().splitlines()) if out.strip() else set()
    except RuntimeError:
        return set()


def launch_job(repo: Repo, config: dict) -> str:
    """Create ACI container for one repo. Returns ACI name."""
    # ACI names: lowercase alnum + hyphens, max 63 chars
    name = re.sub(r'[^a-z0-9-]', '-', f"cpl-{repo.repo_id}".lower())[:63].rstrip("-")

    cmd_line = (
        f"/index_job.sh {repo.url} {repo.commit} {repo.repo_id} "
        f"{config['storage_account']} {config['storage_container']}"
    )

    print(f"  Launching {name}...", end=" ", flush=True)

    az(
        "container", "create",
        "--resource-group", config["resource_group"],
        "--name", name,
        "--image", config["image"],
        "--os-type", "Linux",
        "--restart-policy", "Never",
        "--cpu", str(config["cpu"]),
        "--memory", str(config["memory_gb"]),
        "--location", config["location"],
        "--assign-identity", config["identity_id"],
        "--acr-identity", config["identity_id"],
        "--command-line", cmd_line,
        "--no-wait",
    )
    print("launched")
    return name


def poll_job(name: str, config: dict, timeout_min: int) -> tuple[str, int | None]:
    """Poll ACI until terminated or timeout. Returns (state, exit_code)."""
    deadline = time.time() + timeout_min * 60
    while time.time() < deadline:
        try:
            out = az(
                "container", "show",
                "-g", config["resource_group"],
                "-n", name,
                "--query", "[containers[0].instanceView.currentState.state, containers[0].instanceView.currentState.exitCode]",
                "-o", "json",
                check=False,
            )
            if out:
                state_info = json.loads(out)
                state = state_info[0] if state_info[0] else "unknown"
                exit_code = state_info[1]
                if state == "Terminated":
                    return state, exit_code
        except (json.JSONDecodeError, RuntimeError):
            pass
        time.sleep(15)

    return "timeout", None


def fetch_logs(name: str, config: dict) -> str:
    """Fetch container logs."""
    try:
        return az(
            "container", "logs",
            "-g", config["resource_group"],
            "-n", name,
            check=False,
        )
    except RuntimeError:
        return "(failed to fetch logs)"


def download_index(repo: Repo, config: dict, repo_root: Path) -> bool:
    """Download index tar from blob and extract to local clone."""
    local_tar = Path(f"/tmp/{repo.repo_id}.tar.gz")
    clone_path = repo_root / repo.clone_dir

    try:
        az(
            "storage", "blob", "download",
            "--account-name", config["storage_account"],
            "--container-name", config["storage_container"],
            "--name", f"{repo.repo_id}.tar.gz",
            "--file", str(local_tar),
            "--auth-mode", "login",
            "--only-show-errors",
        )
    except RuntimeError as e:
        print(f"    Download failed: {e}")
        return False

    if not clone_path.exists():
        print(f"    Clone dir not found: {clone_path}")
        return False

    # Remove existing .codeplane dir and extract fresh
    cpl_dir = clone_path / ".codeplane"
    if cpl_dir.exists():
        shutil.rmtree(cpl_dir)

    subprocess.run(
        ["tar", "xzf", str(local_tar), "-C", str(clone_path)],
        check=True,
    )

    # Commit
    subprocess.run(["git", "-C", str(clone_path), "add", "-A"], check=True)
    diff = subprocess.run(
        ["git", "-C", str(clone_path), "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if diff.returncode != 0:
        subprocess.run(
            ["git", "-C", str(clone_path), "commit", "-m",
             "chore: index artifacts from ACI batch job", "--no-verify", "-q"],
            check=True,
        )
        print("    Committed index artifacts")
    else:
        print("    No changes to commit")

    local_tar.unlink(missing_ok=True)
    return True


def download_profile(repo: Repo, config: dict) -> dict:
    """Download profiling data from blob."""
    local = Path(f"/tmp/{repo.repo_id}_profile.json")
    try:
        az(
            "storage", "blob", "download",
            "--account-name", config["storage_account"],
            "--container-name", config["storage_container"],
            "--name", f"profiles/{repo.repo_id}.json",
            "--file", str(local),
            "--auth-mode", "login",
            "--only-show-errors",
        )
        data = json.loads(local.read_text())
        local.unlink(missing_ok=True)
        return data
    except (RuntimeError, json.JSONDecodeError):
        return {}


def cleanup_aci(name: str, config: dict) -> None:
    """Delete the ACI container."""
    az("container", "delete", "-g", config["resource_group"], "-n", name, "-y", check=False)


def process_batch(batch: list[Repo], config: dict, repo_root: Path) -> list[JobResult]:
    """Launch a batch of ACI jobs, monitor, download results."""
    results: list[JobResult] = []

    # Launch all
    names = {}
    for repo in batch:
        try:
            name = launch_job(repo, config)
            names[repo.repo_id] = name
        except RuntimeError as e:
            results.append(JobResult(repo=repo, aci_name="", error=str(e)))

    # Poll all
    for repo in batch:
        name = names.get(repo.repo_id)
        if not name:
            continue

        print(f"  Waiting for {repo.repo_id}...", end=" ", flush=True)
        t0 = time.time()
        state, exit_code = poll_job(name, config, config["timeout_minutes"])
        duration = int(time.time() - t0)

        logs = fetch_logs(name, config)

        result = JobResult(
            repo=repo,
            aci_name=name,
            state=state,
            exit_code=exit_code,
            duration_sec=duration,
            logs=logs,
        )

        if state == "Terminated" and exit_code == 0:
            print(f"OK ({duration}s)")
            # Download index
            if download_index(repo, config, repo_root):
                result.profile = download_profile(repo, config)
            else:
                result.error = "download failed"
        elif state == "timeout":
            print(f"TIMEOUT after {config['timeout_minutes']}min")
            result.error = f"timeout after {config['timeout_minutes']}min"
            # Still fetch logs for debugging
            print(f"    Last logs:\n{logs[-500:]}" if logs else "    (no logs)")
        else:
            print(f"FAILED (exit={exit_code}, {duration}s)")
            result.error = f"exit_code={exit_code}"
            print(f"    Last logs:\n{logs[-500:]}" if logs else "    (no logs)")

        # Save logs to disk
        log_dir = repo_root / "ranking" / "data" / "index_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{repo.repo_id}.log").write_text(logs)

        cleanup_aci(name, config)
        results.append(result)

    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch index repos via ACI")
    parser.add_argument("--parallelism", type=int, default=10, help="Concurrent ACI jobs")
    parser.add_argument("--set", choices=["ranker-gate", "cutoff", "eval"], help="Only index one set")
    parser.add_argument("--repo", help="Only index one repo (md filename stem)")
    parser.add_argument("--cpu", type=int, default=4, help="CPUs per ACI instance")
    parser.add_argument("--memory", type=int, default=8, help="GB RAM per ACI instance")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout per job in minutes")
    parser.add_argument("--dry-run", action="store_true", help="List repos without launching")
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    infra_dir = repo_root / "ranking" / "infra"

    # Load terraform outputs
    tf = load_terraform_outputs(infra_dir)
    config = {
        "resource_group": tf.get("resource_group", DEFAULTS["resource_group"]),
        "image": f"{tf.get('acr_login_server', '')}/codeplane-indexer:latest",
        "storage_account": tf.get("storage_account", ""),
        "storage_container": tf.get("storage_container", DEFAULTS["storage_container"]),
        "identity_id": tf.get("identity_id", ""),
        "location": DEFAULTS["location"],
        "cpu": args.cpu,
        "memory_gb": args.memory,
        "timeout_minutes": args.timeout,
    }

    if not config["storage_account"] or not config["identity_id"]:
        print("ERROR: Run 'terraform apply' in ranking/infra/ first")
        print(f"  Missing: storage_account={config['storage_account']!r}, identity_id={config['identity_id']!r}")
        return 1

    repos = get_repos(repo_root, set_filter=args.set, repo_filter=args.repo)
    print(f"Found {len(repos)} repos to index")

    if args.dry_run:
        for r in repos:
            print(f"  {r.set_name}/{r.repo_id} @ {r.commit[:12]} → {r.clone_dir}")
        return 0

    # Check what's already running/completed in ACI
    existing_containers = _list_existing_containers(config)
    already_running = set()
    for name in existing_containers:
        # Extract repo_id from container name (cpl-{repo_id})
        already_running.add(name)

    # Fire jobs, skipping already-running and retrying on quota errors
    print(f"\n=== Launching ACI jobs (existing: {len(already_running)}) ===")
    jobs: dict[str, tuple[str, Repo]] = {}  # repo_id → (aci_name, repo)
    retry_queue: list[Repo] = []

    for repo in repos:
        aci_name = re.sub(r'[^a-z0-9-]', '-', f"cpl-{repo.repo_id}".lower())[:63].rstrip("-")
        if aci_name in already_running:
            print(f"  {aci_name} already exists, tracking")
            jobs[repo.repo_id] = (aci_name, repo)
            continue
        try:
            name = launch_job(repo, config)
            jobs[repo.repo_id] = (name, repo)
        except RuntimeError as e:
            if "QuotaReached" in str(e) or "quota" in str(e).lower():
                print(f"    QUOTA — queued for retry: {repo.repo_id}")
                retry_queue.append(repo)
            else:
                print(f"    LAUNCH FAILED {repo.repo_id}: {e}")

    print(f"\n=== Launched {len(jobs)}/{len(repos)}, {len(retry_queue)} queued for retry ===")

    # Collect as they finish (poll all, process completed ones)
    all_results: list[JobResult] = []
    pending = dict(jobs)  # copy
    start_times = {rid: time.time() for rid in pending}
    deadline = time.time() + args.timeout * 60

    while pending and time.time() < deadline:
        newly_done = []

        for repo_id, (name, repo) in list(pending.items()):
            try:
                out = az(
                    "container", "show",
                    "-g", config["resource_group"],
                    "-n", name,
                    "--query", "[containers[0].instanceView.currentState.state, containers[0].instanceView.currentState.exitCode]",
                    "-o", "json",
                    check=False,
                )
                if out:
                    info = json.loads(out)
                    state = info[0] if info[0] else "unknown"
                    if state == "Terminated":
                        exit_code = info[1]
                        duration = int(time.time() - start_times[repo_id])
                        logs = fetch_logs(name, config)

                        result = JobResult(
                            repo=repo, aci_name=name,
                            state=state, exit_code=exit_code,
                            duration_sec=duration, logs=logs,
                        )

                        if exit_code == 0:
                            print(f"  ✓ {repo_id} ({duration}s)")
                            if download_index(repo, config, repo_root):
                                result.profile = download_profile(repo, config)
                            else:
                                result.error = "download failed"
                        else:
                            print(f"  ✗ {repo_id} exit={exit_code} ({duration}s)")
                            result.error = f"exit_code={exit_code}"
                            if logs:
                                print(f"    {logs.splitlines()[-1][:100]}" if logs.strip() else "")

                        # Save logs
                        log_dir = repo_root / "ranking" / "data" / "index_logs"
                        log_dir.mkdir(parents=True, exist_ok=True)
                        (log_dir / f"{repo_id}.log").write_text(logs)

                        cleanup_aci(name, config)
                        all_results.append(result)
                        newly_done.append(repo_id)
            except (json.JSONDecodeError, RuntimeError):
                pass

        for rid in newly_done:
            del pending[rid]

        # Retry queued jobs as slots free up
        if newly_done and retry_queue:
            to_retry = list(retry_queue)
            retry_queue.clear()
            for repo in to_retry:
                try:
                    name = launch_job(repo, config)
                    jobs[repo.repo_id] = (name, repo)
                    pending[repo.repo_id] = (name, repo)
                    start_times[repo.repo_id] = time.time()
                except RuntimeError as e:
                    if "QuotaReached" in str(e) or "quota" in str(e).lower():
                        retry_queue.append(repo)
                    else:
                        print(f"    RETRY FAILED {repo.repo_id}: {e}")

        if pending:
            remaining = len(pending)
            queued = len(retry_queue)
            elapsed_total = int(time.time() - min(start_times.values()))
            status = f"  ... {remaining} pending"
            if queued:
                status += f", {queued} queued"
            status += f" ({elapsed_total}s elapsed)"
            print(status, end="\r")
            time.sleep(15)

    # Handle timeouts
    for repo_id, (name, repo) in pending.items():
        duration = int(time.time() - start_times[repo_id])
        logs = fetch_logs(name, config)
        log_dir = repo_root / "ranking" / "data" / "index_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"{repo_id}.log").write_text(logs)
        print(f"  ⏱ {repo_id} TIMEOUT after {duration}s")
        if logs:
            print(f"    Last: {logs.splitlines()[-1][:100]}" if logs.strip() else "")
        cleanup_aci(name, config)
        all_results.append(JobResult(
            repo=repo, aci_name=name, state="timeout",
            duration_sec=duration, logs=logs,
            error=f"timeout after {args.timeout}min",
        ))

    # Summary
    print(f"\n{'='*60}")
    print(f"{'SUMMARY':^60}")
    print(f"{'='*60}")
    ok = [r for r in all_results if r.state == "Terminated" and r.exit_code == 0]
    failed = [r for r in all_results if r not in ok]
    print(f"  OK:     {len(ok)}/{len(all_results)}")
    print(f"  Failed: {len(failed)}/{len(all_results)}")

    if ok:
        durations = [r.duration_sec for r in ok]
        print(f"  Time:   min={min(durations)}s, max={max(durations)}s, avg={sum(durations)//len(durations)}s")

    # Write profiles summary
    profiles = [r.profile for r in ok if r.profile]
    if profiles:
        summary_path = repo_root / "ranking" / "data" / "index_profiles.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(profiles, summary_path.open("w"), indent=2)
        print(f"  Profiles: {summary_path}")

    if failed:
        print(f"\n  Failed repos:")
        for r in failed:
            print(f"    {r.repo.repo_id}: {r.error or r.state}")
            log_path = repo_root / "ranking" / "data" / "index_logs" / f"{r.repo.repo_id}.log"
            if log_path.exists():
                print(f"      Log: {log_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
