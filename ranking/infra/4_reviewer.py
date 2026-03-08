#!/usr/bin/env python3
"""Step 4: Create reviewer issues (Opus 4.6). Wait/merge when done.

Usage:
    python 4_reviewer.py ruby-grape
    python 4_reviewer.py --status
    python 4_reviewer.py --merge
"""
import argparse
from gt_common import (
    all_repo_ids, create_issue, find_task_file, fork_name, is_copilot_done,
    merge_pr, parse_task_file, pr_for_issue, BASE_BRANCH,
)


def create_reviewer(repo_id: str, model: str = "claude-opus-4.6",
                    branch: str = BASE_BRANCH) -> int:
    meta = parse_task_file(find_task_file(repo_id))
    fork = fork_name(meta["upstream"])
    body = (
        "## Instructions\n\n"
        "Read `.gt-roles/reviewer.md` — those are your instructions.\n"
        "Read `.gt-tasks.md` — those are your tasks.\n"
        "Ground truth outputs are in `ground_truth/` at repo root.\n\n"
        "**Path note**: Where the role file says `../../data/{repo_id}/ground_truth/`, "
        "read `ground_truth/` instead.\n"
        "Do NOT run the merge script.\n\nBegin."
    )
    instr = (
        "You are the outputs reviewer. Read .gt-roles/reviewer.md for full instructions. "
        "Read .gt-tasks.md for tasks. Ground truth files are in ground_truth/ at repo root. "
        "Do NOT run the merge script."
    )
    import requests
    from gt_common import gh_token
    token = gh_token()
    payload = {
        "title": f"GT Reviewer — {repo_id}",
        "body": body, "labels": ["gt:reviewer"],
        "assignees": ["copilot-swe-agent[bot]"],
        "agent_assignment": {
            "target_repo": fork, "base_branch": branch,
            "custom_instructions": instr, "custom_agent": "", "model": model,
        },
    }
    r = requests.post(f"https://api.github.com/repos/{fork}/issues",
        headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        json=payload)
    r.raise_for_status()
    num = r.json()["number"]
    print(f"  {repo_id}: reviewer issue #{num} (model={model}, branch={branch})")
    return num


def check_status(repo_ids: list[str]) -> None:
    import requests
    from gt_common import gh_token
    token = gh_token()
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels=gt:reviewer&state=all&per_page=5",
                         headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
        for iss in r.json():
            done = is_copilot_done(fork, iss["number"])
            pr = pr_for_issue(fork, iss["number"])
            pr_info = f"PR #{pr['number']} {pr['state']}" if pr else "no PR"
            print(f"  {rid}: #{iss['number']} {'DONE' if done else 'working'} | {pr_info}")


def merge_all(repo_ids: list[str]) -> None:
    import requests
    from gt_common import gh_token
    token = gh_token()
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels=gt:reviewer&state=all&per_page=5",
                         headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
        for iss in r.json():
            pr = pr_for_issue(fork, iss["number"])
            if pr and pr["state"] == "open":
                ok = merge_pr(fork, pr["number"])
                print(f"  {rid}: PR #{pr['number']} {'merged' if ok else 'FAILED'}")
            elif pr and pr.get("merged_at"):
                print(f"  {rid}: already merged")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", default=None)
    p.add_argument("--status", action="store_true")
    p.add_argument("--merge", action="store_true")
    p.add_argument("--model", default="claude-opus-4.6")
    p.add_argument("--branch", default=BASE_BRANCH)
    args = p.parse_args()
    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")

    if args.status:
        check_status(ids)
    elif args.merge:
        merge_all(ids)
    else:
        for rid in ids:
            try:
                create_reviewer(rid, args.model, args.branch)
            except Exception as e:
                print(f"  {rid}: ERROR {e}")


if __name__ == "__main__":
    main()
