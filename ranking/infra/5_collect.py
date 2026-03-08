#!/usr/bin/env python3
"""Step 5: Collect ground truth JSONs from forks into codeplane.

Usage:
    python 5_collect.py ruby-grape
    python 5_collect.py --set eval
"""
import argparse, json, shutil, subprocess, tempfile
from pathlib import Path
from gt_common import (
    all_repo_ids, find_task_file, fork_name, gh_token, parse_task_file,
    run, RANKING_DIR, BASE_BRANCH,
)


def collect(repo_id: str, branch: str = BASE_BRANCH, suffix: str = "") -> None:
    meta = parse_task_file(find_task_file(repo_id))
    fork = fork_name(meta["upstream"])
    token = gh_token()

    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp) / "repo"
        run(f"git clone --branch {branch} --depth 1 "
            f"https://x-access-token:{token}@github.com/{fork}.git {repo_dir}",
            capture=False)

        gt_src = repo_dir / "ground_truth"
        if not gt_src.exists():
            print(f"  {repo_id}: no ground_truth/ dir on {branch}")
            return

        jsons = list(gt_src.glob("*.json"))
        jsons = [f for f in jsons if f.name != ".gitkeep"]
        print(f"  {repo_id}: {len(jsons)} JSON files on {branch}")

        # Copy to codeplane
        dir_name = f"{repo_id}{suffix}" if suffix else repo_id
        target = RANKING_DIR / "data" / dir_name / "ground_truth"
        target.mkdir(parents=True, exist_ok=True)
        for f in jsons:
            shutil.copy2(f, target / f.name)

        # Run merge script
        merge_script = RANKING_DIR / "infra" / "merge_ground_truth.py"
        if merge_script.exists():
            subprocess.run(
                f"python3 {merge_script} {target.parent}",
                shell=True, check=False,
            )

        print(f"  {repo_id}: collected to {target.parent.relative_to(RANKING_DIR)}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("repos", nargs="*")
    p.add_argument("--set", default=None)
    p.add_argument("--branch", default=BASE_BRANCH)
    p.add_argument("--suffix", default="", help="Append to dir name (e.g. '-gpt41')")
    args = p.parse_args()
    ids = list(args.repos) if args.repos else all_repo_ids(args.set or "all")
    for rid in ids:
        try:
            collect(rid, args.branch, args.suffix)
        except Exception as e:
            print(f"  {rid}: ERROR {e}")


if __name__ == "__main__":
    main()
