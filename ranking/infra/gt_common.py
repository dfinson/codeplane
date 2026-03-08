#!/usr/bin/env python3
"""Shared helpers for the GT pipeline batch scripts.

All 6 scripts import from here. No Actions, no events — just loops and polls.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

RANKING_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = RANKING_DIR / "repos"
ROLES_DIR = RANKING_DIR / "roles"
CODEPLANE_REPO = "dfinson/codeplane"
BASE_BRANCH = "gt-generation"


def run(cmd: str, *, check: bool = True, capture: bool = True) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=capture, text=True, check=check)
    return r.stdout.strip() if capture else ""


def gh_token() -> str:
    return run("gh auth token")


def gh_user() -> str:
    return run("gh api /user -q .login")


def parse_task_file(path: Path) -> dict:
    content = path.read_text()
    url_m = re.search(r"\*\*URL\*\*\s*\|\s*(https://github\.com/([^/\s|]+/[^/\s|]+))", content)
    commit_m = re.search(r"\*\*Commit\*\*\s*\|\s*`([a-f0-9]+)`", content)
    lang_m = re.search(r"\*\*Language\*\*\s*\|\s*(\w+)", content)
    set_m = re.search(r"\*\*Set\*\*\s*\|\s*(\w[\w-]*)", content)
    if not url_m or not commit_m:
        raise ValueError(f"Cannot parse metadata from {path}")
    return {
        "url": url_m.group(1),
        "upstream": url_m.group(2),
        "commit": commit_m.group(1),
        "language": lang_m.group(1) if lang_m else "unknown",
        "set": set_m.group(1) if set_m else "unknown",
        "repo_id": path.stem,
        "task_file": path,
    }


def find_task_file(repo_id: str) -> Path:
    for d in REPOS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = d / f"{repo_id}.md"
        if p.exists():
            return p
    raise FileNotFoundError(f"No task file for {repo_id}")


def all_repo_ids(set_name: str = "all") -> list[str]:
    ids = []
    dirs = [d for d in REPOS_DIR.iterdir() if d.is_dir()] if set_name == "all" else [REPOS_DIR / set_name]
    for d in dirs:
        if d.exists():
            ids.extend(f.stem for f in sorted(d.glob("*.md")))
    return ids


def fork_name(upstream: str) -> str:
    """dfinson/pydantic from pydantic/pydantic"""
    return f"{gh_user()}/{upstream.split('/')[1]}"


def commit_exists(upstream: str, sha: str) -> bool:
    r = subprocess.run(
        f'gh api "/repos/{upstream}/commits/{sha}" -q .sha',
        shell=True, capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def fork_exists(fork: str) -> bool:
    r = subprocess.run(
        f'gh api "/repos/{fork}" -q .full_name',
        shell=True, capture_output=True, text=True, check=False,
    )
    return r.returncode == 0


def wait_for_fork(fork: str, timeout: int = 60) -> None:
    for _ in range(timeout // 2):
        if fork_exists(fork):
            return
        time.sleep(2)
    raise TimeoutError(f"Fork {fork} not ready after {timeout}s")


def create_issue(fork: str, title: str, body: str, label: str,
                 instructions: str, model: str = "claude-sonnet-4.6") -> int:
    """Create an issue assigned to copilot-swe-agent. Returns issue number."""
    import requests
    token = gh_token()
    r = requests.post(
        f"https://api.github.com/repos/{fork}/issues",
        headers={
            "Authorization": f"bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={
            "title": title,
            "body": body,
            "labels": [label],
            "assignees": ["copilot-swe-agent[bot]"],
            "agent_assignment": {
                "target_repo": fork,
                "base_branch": BASE_BRANCH,
                "custom_instructions": instructions,
                "custom_agent": "",
                "model": model,
            },
        },
    )
    r.raise_for_status()
    return r.json()["number"]


def pr_for_issue(fork: str, issue_num: int) -> dict | None:
    """Find the PR that references a given issue."""
    import requests
    token = gh_token()
    r = requests.get(
        f"https://api.github.com/repos/{fork}/pulls?state=all&sort=created&direction=desc&per_page=20",
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"},
    )
    for pr in r.json():
        if f"#{issue_num}" in (pr.get("body") or ""):
            return pr
    # Fallback: check timeline
    r2 = requests.get(
        f"https://api.github.com/repos/{fork}/issues/{issue_num}/timeline",
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"},
    )
    for ev in r2.json():
        if ev.get("event") == "cross-referenced" and ev.get("source", {}).get("issue", {}).get("pull_request"):
            pr_url = ev["source"]["issue"]["pull_request"]["url"]
            r3 = requests.get(pr_url, headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
            return r3.json()
    return None


def is_copilot_done(fork: str, issue_num: int) -> bool:
    """Check if copilot_work_finished event exists for this issue."""
    import requests
    token = gh_token()
    # Check issue timeline for copilot events
    r = requests.get(
        f"https://api.github.com/repos/{fork}/issues/{issue_num}/timeline?per_page=100",
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"},
    )
    for ev in r.json():
        if ev.get("event") == "copilot_work_finished":
            return True
    return False


def merge_pr(fork: str, pr_number: int) -> bool:
    """Mark PR ready and squash-merge it. Returns True on success."""
    r = subprocess.run(
        f'gh pr ready {pr_number} --repo {fork}',
        shell=True, capture_output=True, text=True, check=False,
    )
    r2 = subprocess.run(
        f'gh pr merge {pr_number} --repo {fork} --squash',
        shell=True, capture_output=True, text=True, check=False,
    )
    return r2.returncode == 0


def inject_files_to_fork(fork: str, meta: dict) -> None:
    """Push role files, task file, config, and ground_truth dir to the fork."""
    token = gh_token()
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp) / "repo"
        run(f"git clone --branch {BASE_BRANCH} --depth 1 "
            f"https://x-access-token:{token}@github.com/{fork}.git {repo_dir}", capture=False)

        # Role files with path adaptation
        roles_dest = repo_dir / ".gt-roles"
        roles_dest.mkdir(exist_ok=True)
        for name in ("auditor.md", "executor.md", "reviewer.md"):
            src = ROLES_DIR / name
            if not src.exists():
                continue
            txt = src.read_text()
            txt = txt.replace("../../data/{repo_id}/ground_truth/", "ground_truth/")
            txt = txt.replace("`../../data/{repo_id}/ground_truth/{heading_id}.json`", "`ground_truth/{heading_id}.json`")
            txt = txt.replace("`../../data/{repo_id}/non_ok_queries.json`", "`ground_truth/non_ok_queries.json`")
            txt = txt.replace("python ../../../infra/merge_ground_truth.py ../../data/{REPO_NAME}",
                              "# Merge script runs externally after collection")
            (roles_dest / name).write_text(txt)

        # Task file
        shutil.copy2(meta["task_file"], repo_dir / ".gt-tasks.md")

        # Config
        (repo_dir / ".gt-pipeline.json").write_text(json.dumps({
            "repo_id": meta["repo_id"], "upstream": meta["upstream"],
            "commit": meta["commit"], "language": meta["language"],
        }, indent=2) + "\n")

        # Ground truth dir
        (repo_dir / "ground_truth").mkdir(exist_ok=True)
        (repo_dir / "ground_truth" / ".gitkeep").touch()

        run(f"cd {repo_dir} && git add -A && "
            f'git -c user.name=gt-pipeline -c user.email=gt@noreply.github.com '
            f'commit -m "chore: inject GT pipeline files" && '
            f"git push origin {BASE_BRANCH}", capture=False)
