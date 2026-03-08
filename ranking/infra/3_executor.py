#!/usr/bin/env python3
"""Step 3: Create executor issues (N/M/W sessions). Wait/merge when done.

Usage:
    python 3_executor.py ruby-grape              # all 3 sessions
    python 3_executor.py ruby-grape --tier N     # just N tasks
    python 3_executor.py --status                # check progress
    python 3_executor.py --merge                 # merge finished PRs
    python 3_executor.py --model gpt-4.1         # use a different model
    python 3_executor.py --branch gt-generation-gpt41  # target a different branch
"""
import argparse, sys
from gt_common import (
    all_repo_ids, create_issue, find_task_file, fork_name, is_copilot_done,
    merge_pr, parse_task_file, pr_for_issue, BASE_BRANCH,
)

SESSIONS = {
    "N": ("a", "N1 through N10 and N11", "M and W", ""),
    "M": ("b", "M1 through M10 and M11", "N and W", ""),
    "W": ("c", "W1 through W10 and W11", "N and M",
          "\nAfter all W tasks, execute STEP 4 (non-OK queries)."),
}


def create_executor(repo_id: str, tier: str, model: str = "claude-sonnet-4.6",
                    branch: str = BASE_BRANCH) -> int:
    meta = parse_task_file(find_task_file(repo_id))
    fork = fork_name(meta["upstream"])
    session, range_desc, skip, extra = SESSIONS[tier]

    body = (
        "## Instructions\n\n"
        "Read `.gt-roles/executor.md` — those are your instructions.\n"
        "Read `.gt-tasks.md` — those are your tasks.\n\n"
        f"**Output**: Write JSON files to `ground_truth/{{heading_id}}.json` at repo root.\n"
        f"**Scope**: Execute tasks {range_desc} only. Skip all {skip} tasks.\n"
        "Do NOT touch files from other sessions.\n"
        f"{extra}\n\nBegin."
    )
    instr = (
        f"You are the task executor. Read .gt-roles/executor.md for full instructions. "
        f"Read .gt-tasks.md for tasks. Write output to ground_truth/ at repo root. "
        f"Execute only {tier} tasks ({range_desc}). Do NOT touch other sessions files."
    )
    label = f"gt:executor-{session}"
    title = f"GT Executor {session.upper()} ({tier} tasks) — {repo_id}"

    # Override base branch if specified
    import requests
    from gt_common import gh_token
    token = gh_token()
    payload = {
        "title": title, "body": body, "labels": [label],
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
    print(f"  {repo_id}/{tier}: issue #{num} on {fork} (model={model}, branch={branch})")
    return num


def check_status(repo_ids: list[str], label_filter: str = "gt:executor") -> None:
    import requests
    from gt_common import gh_token
    token = gh_token()
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        for tier, (session, *_) in SESSIONS.items():
            label = f"gt:executor-{session}"
            r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels={label}&state=all&per_page=5",
                             headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
            for iss in r.json():
                done = is_copilot_done(fork, iss["number"])
                pr = pr_for_issue(fork, iss["number"])
                pr_info = f"PR #{pr['number']} {pr['state']}" if pr else "no PR"
                print(f"  {rid}/{tier}: #{iss['number']} {'DONE' if done else 'working'} | {pr_info}")


def merge_all(repo_ids: list[str]) -> None:
    import requests
    from gt_common import gh_token
    token = gh_token()
    for rid in repo_ids:
        meta = parse_task_file(find_task_file(rid))
        fork = fork_name(meta["upstream"])
        for tier, (session, *_) in SESSIONS.items():
            label = f"gt:executor-{session}"
            r = requests.get(f"https://api.github.com/repos/{fork}/issues?labels={label}&state=all&per_page=5",
                             headers={"Authorization": f"bearer {token}", "Accept": "application/vnd.github+json"})
            for iss in r.json():
                pr = pr_for_issue(fork, iss["number"])
                if pr and pr["state"] == "open":
                    ok = merge_pr(fork, pr["number"])
                    print(f"  {rid}/{tier}: PR #{pr['number']} {'merged' if ok else 'FAILED'}")
                elif pr and pr.get("merged_at"):
                    print(f"  {rid}/{tier}: already merged")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", default=None)
    p.add_argument("--tier", choices=["N", "M", "W"], default=None)
    p.add_argument("--status", action="store_true")
    p.add_argument("--merge", action="store_true")
    p.add_argument("--model", default="claude-sonnet-4.6")
    p.add_argument("--branch", default=BASE_BRANCH)
    args = p.parse_args()
    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")

    if args.status:
        check_status(ids)
    elif args.merge:
        merge_all(ids)
    else:
        tiers = [args.tier] if args.tier else ["N", "M", "W"]
        for rid in ids:
            for t in tiers:
                try:
                    create_executor(rid, t, args.model, args.branch)
                except Exception as e:
                    print(f"  {rid}/{t}: ERROR {e}")


if __name__ == "__main__":
    main()
