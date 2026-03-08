#!/usr/bin/env python3
"""Step 2: Create auditor issues for all forks. Wait/merge when done.

Usage:
    python 2_auditor.py                      # all
    python 2_auditor.py ruby-grape           # one repo
    python 2_auditor.py --status             # check progress
    python 2_auditor.py --merge              # merge all finished PRs
"""
import argparse, json, sys
from gt_common import (
    all_repo_ids, create_issue, find_task_file, fork_name, is_copilot_done,
    merge_pr, parse_task_file, pr_for_issue, BASE_BRANCH,
)


def create_auditor(repo_id: str, model: str = "claude-sonnet-4.6") -> int:
    meta = parse_task_file(find_task_file(repo_id))
    fork = fork_name(meta["upstream"])
    body = (
        "## Instructions\n\n"
        "Read `.gt-roles/auditor.md` — those are your instructions.\n"
        "Read `.gt-tasks.md` — those are your tasks.\n\n"
        f"**repo_id**: `{repo_id}`\n"
        "**Output dir**: `ground_truth/` at repo root.\n\n"
        "Begin."
    )
    instr = (
        f"You are the pre-flight auditor. Read .gt-roles/auditor.md for full instructions. "
        f"Read .gt-tasks.md for tasks. repo_id={repo_id}. "
        f"Write to ground_truth/ at repo root (not ../../data/)."
    )
    num = create_issue(fork, f"GT Auditor — {repo_id}", body, "gt:auditor", instr, model)
    print(f"  {repo_id}: issue #{num} on {fork}")
    return num


def check_status(repo_ids: list[str]) -> None:
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        # Find auditor issue (assume #1 or search)
        import requests
        from gt_common import gh_token
        token = gh_token()
        r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels=gt:auditor&state=all&per_page=5",
                         headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
        issues = r.json()
        if not issues:
            print(f"  {rid}: no auditor issue found")
            continue
        iss = issues[0]
        done = is_copilot_done(fork, iss["number"])
        pr = pr_for_issue(fork, iss["number"])
        pr_state = f"PR #{pr['number']} {pr['state']}" if pr else "no PR"
        print(f"  {rid}: issue #{iss['number']} {'DONE' if done else 'working'} | {pr_state}")


def merge_all(repo_ids: list[str]) -> None:
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        import requests
        from gt_common import gh_token
        token = gh_token()
        r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels=gt:auditor&state=all&per_page=5",
                         headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
        issues = r.json()
        if not issues:
            continue
        pr = pr_for_issue(fork, issues[0]["number"])
        if pr and pr["state"] == "open":
            ok = merge_pr(fork, pr["number"])
            print(f"  {rid}: PR #{pr['number']} {'merged' if ok else 'FAILED'}")
        elif pr and pr.get("merged_at"):
            print(f"  {rid}: already merged")
        else:
            print(f"  {rid}: not ready")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", default=None)
    p.add_argument("--status", action="store_true")
    p.add_argument("--merge", action="store_true")
    p.add_argument("--model", default="claude-sonnet-4.6")
    args = p.parse_args()
    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")

    if args.status:
        check_status(ids)
    elif args.merge:
        merge_all(ids)
    else:
        for rid in ids:
            try:
                create_auditor(rid, args.model)
            except Exception as e:
                print(f"  {rid}: ERROR {e}")


if __name__ == "__main__":
    main()
