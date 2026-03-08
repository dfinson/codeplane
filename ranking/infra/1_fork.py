#!/usr/bin/env python3
"""Step 1: Create forks for all repos, set up branches and inject files.

Usage:
    python 1_fork.py                     # all sets
    python 1_fork.py --set eval          # one set
    python 1_fork.py python-pydantic     # one repo
"""
import argparse
import sys
import time

from gt_common import (
    all_repo_ids, commit_exists, find_task_file, fork_exists, fork_name,
    gh_user, inject_files_to_fork, parse_task_file, run, wait_for_fork,
    BASE_BRANCH, gh_token,
)


def setup_fork(repo_id: str) -> None:
    meta = parse_task_file(find_task_file(repo_id))
    user = gh_user()
    fork = fork_name(meta["upstream"])
    print(f"\n{'='*50}")
    print(f"{repo_id}: {meta['upstream']} @ {meta['commit'][:8]}")

    # Validate commit
    if not commit_exists(meta["upstream"], meta["commit"]):
        print(f"  SKIP — commit {meta['commit'][:8]} not found in upstream")
        return

    # Fork
    if not fork_exists(fork):
        run(f"gh repo fork {meta['upstream']} --clone=false", check=False)
        wait_for_fork(fork)
        print(f"  Forked → {fork}")
    else:
        print(f"  Fork exists: {fork}")

    # Enable issues
    run(f'gh api --method PATCH "/repos/{fork}" -f has_issues=true', check=False)

    # Create branch at commit (clone upstream bare, push to fork)
    branch_check = run(
        f'gh api "/repos/{fork}/git/ref/heads/{BASE_BRANCH}" -q .ref',
        check=False,
    )
    if BASE_BRANCH not in branch_check:
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / "bare"
            run(f"git clone --bare https://github.com/{meta['upstream']}.git {bare}", capture=False)
            run(f"cd {bare} && git branch {BASE_BRANCH} {meta['commit']}", capture=False)
            token = gh_token()
            run(f"cd {bare} && git push https://x-access-token:{token}@github.com/{fork}.git {BASE_BRANCH}",
                capture=False)
        print(f"  Branch {BASE_BRANCH} at {meta['commit'][:8]}")
    else:
        print(f"  Branch {BASE_BRANCH} exists")

    # Inject files
    inject_files_to_fork(fork, meta)
    print(f"  Files injected")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", choices=["eval", "ranker-gate", "cutoff", "all"], default=None)
    args = p.parse_args()

    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")
    print(f"Setting up {len(ids)} fork(s)")

    for rid in ids:
        try:
            setup_fork(rid)
        except Exception as e:
            print(f"  ERROR: {e}")


if __name__ == "__main__":
    main()
