#!/usr/bin/env python3
"""Step 6: Delete all forks.

Usage:
    python 6_cleanup.py ruby-grape
    python 6_cleanup.py --set eval
    python 6_cleanup.py --all-forks   # delete every fork with gt-generation branch
"""
import argparse
from gt_common import (
    all_repo_ids, find_task_file, fork_exists, fork_name, gh_user,
    parse_task_file, run,
)


def delete_fork(repo_id: str) -> None:
    meta = parse_task_file(find_task_file(repo_id))
    fork = fork_name(meta["upstream"])
    if not fork_exists(fork):
        print(f"  {repo_id}: no fork")
        return
    result = run(f'gh repo delete {fork} --yes', check=False)
    if "Must have admin rights" in result or "delete_repo" in result:
        print(f"  {repo_id}: NEEDS delete_repo scope — run: gh auth refresh -h github.com -s delete_repo")
    else:
        print(f"  {repo_id}: deleted {fork}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", default=None)
    args = p.parse_args()
    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")
    for rid in ids:
        try:
            delete_fork(rid)
        except Exception as e:
            print(f"  {rid}: ERROR {e}")


if __name__ == "__main__":
    main()
