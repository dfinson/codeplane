#!/usr/bin/env python3
"""Ground truth pipeline — local Copilot SDK orchestrator.

Replaces the GitHub coding agent (fork/issue/PR) approach with local
SDK sessions that run against ranking/clones/ and write directly to
ranking/data/.

Usage:
    python gt_orchestrator.py run                  # run all stages
    python gt_orchestrator.py run --stage audit     # only audit stage
    python gt_orchestrator.py run --repo cpp-abseil # one repo
    python gt_orchestrator.py run --concurrency 10  # override default
    python gt_orchestrator.py status               # show state table
    python gt_orchestrator.py retry                # reset failed → pending
    python gt_orchestrator.py retry cpp-abseil     # reset one repo
    python gt_orchestrator.py logs cpp-abseil audit  # show session log
    python gt_orchestrator.py collect              # merge JSONLs
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

# ── Paths ──

RANKING_DIR = Path(__file__).resolve().parent.parent
REPOS_DIR = RANKING_DIR / "repos"
ROLES_DIR = RANKING_DIR / "roles"
CLONES_DIR = RANKING_DIR / "clones"
DATA_DIR = RANKING_DIR / "data"
STATE_FILE = DATA_DIR / "gt_state.json"
LOGS_DIR = DATA_DIR / "logs"

STAGES = ["audit", "exec_n", "exec_m", "exec_w", "review"]
DEFAULT_CONCURRENCY = 5
MAX_ATTEMPTS = 3

MODELS = {
    "audit": "claude-sonnet-4.6",
    "exec_n": "claude-sonnet-4.6",
    "exec_m": "claude-sonnet-4.6",
    "exec_w": "claude-sonnet-4.6",
    "review": "claude-opus-4.6",
}

# ── State management ──


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"global_stage": "audit", "repos": {}}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def current_global_stage(state: dict) -> str:
    for stage in STAGES:
        for rd in state["repos"].values():
            if rd.get(stage, {}).get("status") != "merged":
                return stage
    return "done"


def repos_for_stage(state: dict, stage: str, status: str) -> list[str]:
    return [
        rid for rid, rd in state["repos"].items()
        if rd.get(stage, {}).get("status") == status
    ]


def find_clone(repo_id: str) -> Path | None:
    """Find the clone directory for a repo_id."""
    for set_dir in CLONES_DIR.iterdir():
        if not set_dir.is_dir():
            continue
        for d in set_dir.iterdir():
            if not d.is_dir():
                continue
            # Match by checking .gt-pipeline.json or by name heuristic
            config = d / ".gt-pipeline.json"
            if config.exists():
                try:
                    c = json.loads(config.read_text())
                    if c.get("repo_id") == repo_id:
                        return d
                except Exception:
                    pass
    # Fallback: try to match by repo name from state
    state = load_state()
    rd = state.get("repos", {}).get(repo_id, {})
    fork = rd.get("fork", "")
    if "/" in fork:
        repo_name = fork.split("/")[1]
        for set_dir in CLONES_DIR.iterdir():
            if not set_dir.is_dir():
                continue
            candidate = set_dir / repo_name
            if candidate.is_dir():
                return candidate
    return None


def find_task_file(repo_id: str) -> Path | None:
    for set_dir in REPOS_DIR.iterdir():
        if not set_dir.is_dir():
            continue
        p = set_dir / f"{repo_id}.md"
        if p.exists():
            return p
    return None


# ── Schema validation ──


GT_REQUIRED_KEYS = {
    "task_id", "task_complexity", "task_text", "diff", "solve_notes",
    "exploration_log", "confidence", "minimum_sufficient_defs",
    "thrash_preventing_defs", "tier_difference_reasoning",
    "excluded_defs", "queries", "test_selection", "reviewer_corrections",
}

NON_OK_REQUIRED_KEYS = {"repo_id", "reviewer_corrections", "non_ok_queries"}


def validate_gt_schema(data: dict) -> list[str]:
    errors = []
    missing = GT_REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
    if not isinstance(data.get("queries"), list):
        errors.append("'queries' must be a list")
    elif len(data.get("queries", [])) < 6:
        errors.append(f"Need ≥6 queries, got {len(data.get('queries', []))}")
    if not isinstance(data.get("minimum_sufficient_defs"), list):
        errors.append("'minimum_sufficient_defs' must be a list")
    if data.get("minimum_sufficient_defs") and not data["minimum_sufficient_defs"][0].get("path"):
        errors.append("Each def must have 'path', 'name', 'kind', 'start_line', 'reason'")
    return errors


def validate_non_ok_schema(data: dict) -> list[str]:
    errors = []
    missing = NON_OK_REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")
    queries = data.get("non_ok_queries", [])
    if len(queries) < 6:
        errors.append(f"Need ≥6 non-ok queries, got {len(queries)}")
    return errors


# ── Custom tools ──

# These are defined as functions that will be wrapped with @define_tool
# at session creation time (they need closure over the SessionRunner).


class WriteGTParams(BaseModel):
    repo_id: str = Field(description="Repository ID (e.g. 'cpp-abseil')")
    task_id: str = Field(description="Task ID (e.g. 'N1', 'M3', 'W11')")
    data: dict = Field(description="Complete ground truth JSON object")


class WriteNonOKParams(BaseModel):
    repo_id: str = Field(description="Repository ID")
    data: dict = Field(description="Complete non-ok queries JSON object")


class ReportCompleteParams(BaseModel):
    summary: str = Field(description="Brief summary of what was accomplished")


# ── Session runner ──


class SessionRunner:
    def __init__(self, repo_id: str, stage: str, state: dict):
        self.repo_id = repo_id
        self.stage = stage
        self.state = state
        self.start_time = time.time()
        self.status_line = "starting..."
        self.jsons_written = 0
        self.done_event = asyncio.Event()
        self.error: str | None = None

        # Logs
        session_log_dir = LOGS_DIR / "sessions"
        session_log_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = session_log_dir / f"{repo_id}_{stage}.log"

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with open(self.log_path, "a") as f:
            f.write(f"[{ts}] {msg}\n")

    def elapsed(self) -> str:
        m = int((time.time() - self.start_time) / 60)
        return f"{m}m"

    def build_tools(self):
        from copilot import define_tool

        runner = self

        @define_tool(description="Write a validated ground truth JSON for a completed task. Call this after solving each task.")
        async def write_ground_truth(params: WriteGTParams) -> str:
            errors = validate_gt_schema(params.data)
            if errors:
                runner.log(f"VALIDATION FAILED for {params.task_id}: {errors}")
                return f"VALIDATION FAILED — fix these errors and call again:\n" + "\n".join(f"  - {e}" for e in errors)
            path = DATA_DIR / params.repo_id / "ground_truth" / f"{params.task_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(params.data, indent=2) + "\n")
            runner.jsons_written += 1
            runner.log(f"WROTE {path} ({runner.jsons_written} total)")
            runner.status_line = f"wrote {params.task_id}.json"
            return f"Written successfully: {path} ({runner.jsons_written} JSONs total)"

        @define_tool(description="Write the non-OK queries JSON file to ground_truth/non_ok_queries.json. Call AFTER all W tasks are done (executor session C only). This file goes in the SAME directory as the task JSONs.")
        async def write_non_ok_queries(params: WriteNonOKParams) -> str:
            errors = validate_non_ok_schema(params.data)
            if errors:
                runner.log(f"NON-OK VALIDATION FAILED: {errors}")
                return f"VALIDATION FAILED:\n" + "\n".join(f"  - {e}" for e in errors)
            path = DATA_DIR / params.repo_id / "ground_truth" / "non_ok_queries.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(params.data, indent=2) + "\n")
            runner.log(f"WROTE {path}")
            return f"Written: {path}"

        @define_tool(description="Signal that you have completed all assigned tasks for this session.")
        async def report_complete(params: ReportCompleteParams) -> str:
            runner.log(f"COMPLETE: {params.summary}")
            runner.status_line = "done"
            # Update state
            rd = runner.state["repos"].get(runner.repo_id, {})
            rd[runner.stage] = {"status": "done", "jsons": runner.jsons_written}
            save_state(runner.state)
            runner.done_event.set()
            return "Session marked complete. You can stop."

        tools = [write_ground_truth, report_complete]
        if self.stage == "exec_w":
            tools.append(write_non_ok_queries)
        if self.stage == "review":
            # Reviewer also writes ground truth (corrections)
            pass  # write_ground_truth already included

        return tools

    def build_prompt(self) -> str:
        task_file = find_task_file(self.repo_id)
        if not task_file:
            raise FileNotFoundError(f"No task file for {self.repo_id}")

        role_file = {
            "audit": ROLES_DIR / "auditor.md",
            "exec_n": ROLES_DIR / "executor.md",
            "exec_m": ROLES_DIR / "executor.md",
            "exec_w": ROLES_DIR / "executor.md",
            "review": ROLES_DIR / "reviewer.md",
        }[self.stage]

        role_content = role_file.read_text()

        # Adapt paths in role content
        role_content = role_content.replace(
            "../../data/{repo_id}/ground_truth/", f"{DATA_DIR}/{self.repo_id}/ground_truth/"
        )

        base_prompt = f"Your tasks file is: {task_file}\nThe repo_id is: {self.repo_id}\n\n"

        if self.stage == "audit":
            base_prompt += "Begin the pre-flight audit.\n"
        elif self.stage == "exec_n":
            base_prompt += (
                "Execute tasks N1 through N11 only. Skip all M and W tasks.\n"
                "For each task, solve it, then call write_ground_truth with the complete JSON.\n"
                "When all N tasks are done, call report_complete.\n"
            )
        elif self.stage == "exec_m":
            base_prompt += (
                "Execute tasks M1 through M11 only. Skip all N and W tasks.\n"
                "For each task, solve it, then call write_ground_truth with the complete JSON.\n"
                "When all M tasks are done, call report_complete.\n"
            )
        elif self.stage == "exec_w":
            base_prompt += (
                "Execute tasks W1 through W11 only. Skip all N and M tasks.\n"
                "For each task, solve it, then call write_ground_truth with the complete JSON.\n"
                "After ALL W tasks are done, write the non-OK queries by calling write_non_ok_queries.\n"
                "The non_ok_queries file will be written to ground_truth/non_ok_queries.json — "
                "the SAME directory as the task JSONs. Do NOT write it anywhere else.\n"
                "When everything is complete (all W JSONs + non_ok_queries), call report_complete.\n"
            )
        elif self.stage == "review":
            gt_dir = DATA_DIR / self.repo_id / "ground_truth"
            base_prompt += (
                f"Ground truth files are in: {gt_dir}\n"
                "Review each one. To correct a JSON, call write_ground_truth with the fixed version.\n"
                "When all tasks are reviewed, call report_complete.\n"
            )

        return role_content + "\n\n---\n\n" + base_prompt

    async def run(self) -> None:
        from copilot import CopilotClient

        clone_dir = find_clone(self.repo_id)
        if not clone_dir:
            raise FileNotFoundError(f"No clone found for {self.repo_id}")

        self.log(f"START {self.repo_id} {self.stage} cwd={clone_dir}")
        self.status_line = "initializing..."

        client = CopilotClient({
            "cwd": str(clone_dir),
            "log_level": "warning",
        })
        await client.start()

        try:
            from copilot import PermissionHandler
            session = await client.create_session({
                "model": MODELS[self.stage],
                "tools": self.build_tools(),
                "infinite_sessions": {"enabled": True},
                "on_permission_request": PermissionHandler.approve_all,
            })

            def on_event(event):
                etype = event.type.value if hasattr(event.type, "value") else str(event.type)
                if etype == "assistant.message":
                    content = getattr(event.data, "content", "") or ""
                    self.log(f"ASSISTANT: {content[:300]}")
                    # Update status with last meaningful line
                    for line in reversed(content.split("\n")):
                        line = line.strip()
                        if line and len(line) > 5:
                            self.status_line = line[:60]
                            break
                elif etype == "session.idle":
                    if not self.done_event.is_set():
                        self.log("SESSION IDLE (no report_complete called)")
                        self.done_event.set()

            session.on(on_event)

            prompt = self.build_prompt()
            self.log(f"PROMPT ({len(prompt)} chars)")
            self.status_line = "prompt sent..."

            await session.send({"prompt": prompt})
            await asyncio.wait_for(self.done_event.wait(), timeout=7200)

            await session.disconnect()
        finally:
            await client.stop()

        self.log(f"END {self.repo_id} {self.stage} jsons={self.jsons_written} elapsed={self.elapsed()}")


# ── Progress display ──


def cmd_status(state: dict) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress

    console = Console()

    stage = current_global_stage(state)
    total = len(state.get("repos", {}))

    # Stage summary
    stage_table = Table(show_header=False, box=None, padding=(0, 1))
    for s in STAGES:
        counts = {}
        for rd in state["repos"].values():
            st = rd.get(s, {}).get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        merged = counts.get("merged", 0)
        done = counts.get("done", 0)
        active = counts.get("active", 0)
        failed = counts.get("failed", 0)
        pending = counts.get("pending", 0)
        pct = (merged + done) / total if total else 0
        bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
        marker = " ◀" if s == stage else ""
        detail = f"{merged+done}/{total}"
        if active:
            detail += f" ({active} active)"
        if failed:
            detail += f" ({failed} failed)"
        stage_table.add_row(bar, detail, s, marker)

    # Failed repos
    failed_list = []
    for rid, rd in state["repos"].items():
        for s in STAGES:
            if rd.get(s, {}).get("status") == "failed":
                err = rd[s].get("error", "")[:40]
                failed_list.append(f"  {rid}/{s}: {err}")

    console.print(Panel(stage_table, title=f"Ground Truth Pipeline — stage: {stage}"))
    if failed_list:
        console.print("[red]Failed:[/red]")
        for f in failed_list:
            console.print(f"  [red]{f}[/red]")


# ── Run command ──


async def cmd_run(state: dict, stage_filter: str | None, repo_filter: str | None,
                  concurrency: int) -> None:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    console = Console()
    sem = asyncio.Semaphore(concurrency)
    runners: dict[str, SessionRunner] = {}

    async def run_one(repo_id: str, stage: str):
        runner = SessionRunner(repo_id, stage, state)
        runners[repo_id] = runner

        # Mark active
        rd = state["repos"].get(repo_id, {})
        attempts = rd.get(stage, {}).get("attempts", 0) + 1
        rd[stage] = {"status": "active", "attempts": attempts}
        save_state(state)

        try:
            async with sem:
                await runner.run()
        except asyncio.TimeoutError:
            runner.log("TIMEOUT after 2h")
            rd[stage] = {"status": "failed", "error": "timeout", "attempts": attempts}
            save_state(state)
            # Archive log
            _archive_log(repo_id, stage, attempts)
        except Exception as e:
            runner.log(f"ERROR: {e}")
            if attempts < MAX_ATTEMPTS:
                rd[stage] = {"status": "pending", "attempts": attempts}
                runner.log(f"Will retry (attempt {attempts}/{MAX_ATTEMPTS})")
            else:
                rd[stage] = {"status": "failed", "error": str(e)[:200], "attempts": attempts}
            save_state(state)
            _archive_log(repo_id, stage, attempts)
        finally:
            runners.pop(repo_id, None)

    def render_display() -> Panel:
        table = Table(show_header=False, box=None, padding=(0, 1))
        total = len(state.get("repos", {}))
        for s in STAGES:
            merged = sum(1 for rd in state["repos"].values() if rd.get(s, {}).get("status") in ("merged", "done"))
            pct = merged / total if total else 0
            bar = "█" * int(pct * 30) + "░" * (30 - int(pct * 30))
            marker = " ◀" if s == current_global_stage(state) else ""
            table.add_row(bar, f"{merged}/{total}", s, marker)

        active_table = Table(show_header=False, box=None, padding=(0, 1))
        for rid, runner in runners.items():
            active_table.add_row("●", rid, runner.stage, runner.elapsed(), runner.status_line[:50])

        pending = len(repos_for_stage(state, current_global_stage(state), "pending"))
        failed = len(repos_for_stage(state, current_global_stage(state), "failed"))
        footer = Text(f"Queue: {pending} pending │ {failed} failed │ {len(runners)}/{concurrency} active")

        from rich.console import Group
        return Panel(Group(table, Text(""), active_table, Text(""), footer), title="Ground Truth Pipeline")

    # Determine stage and targets
    stage = stage_filter or current_global_stage(state)
    if stage == "done":
        console.print("[green]All stages complete![/green]")
        return

    if repo_filter:
        targets = [repo_filter] if state["repos"].get(repo_filter, {}).get(stage, {}).get("status") == "pending" else []
    else:
        targets = repos_for_stage(state, stage, "pending")

    if not targets:
        console.print(f"[yellow]No pending repos for stage {stage}[/yellow]")
        return

    console.print(f"Launching {len(targets)} session(s) for stage [bold]{stage}[/bold]")

    # Create all tasks upfront, semaphore controls concurrency
    async def run_with_display():
        flight = [asyncio.create_task(run_one(rid, stage)) for rid in targets]
        with Live(render_display(), console=console, refresh_per_second=0.5) as live:
            while not all(t.done() for t in flight):
                live.update(render_display())
                await asyncio.sleep(2)
            live.update(render_display())
        # Collect exceptions
        for t, rid in zip(flight, targets):
            if t.exception():
                console.print(f"[red]{rid}: {t.exception()}[/red]")

    await run_with_display()

    # Post-run: advance stage if all done
    all_done = all(
        rd.get(stage, {}).get("status") in ("merged", "done")
        for rd in state["repos"].values()
    )
    if all_done:
        for rd in state["repos"].values():
            if rd.get(stage, {}).get("status") == "done":
                rd[stage]["status"] = "merged"
        save_state(state)
        console.print(f"[green]Stage {stage} complete![/green]")


def _archive_log(repo_id: str, stage: str, attempt: int) -> None:
    src = LOGS_DIR / "sessions" / f"{repo_id}_{stage}.log"
    if src.exists():
        dst_dir = LOGS_DIR / "errors"
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f"{repo_id}_{stage}_attempt{attempt}.log"
        shutil.copy2(src, dst)


# ── Other commands ──


def cmd_retry(state: dict, target: str | None) -> None:
    stage = current_global_stage(state)
    if target:
        repos = [target]
    else:
        repos = repos_for_stage(state, stage, "failed")

    for rid in repos:
        rd = state["repos"].get(rid, {})
        attempts = rd.get(stage, {}).get("attempts", 0)
        _archive_log(rid, stage, attempts)
        rd[stage] = {"status": "pending", "attempts": attempts}
        print(f"  RESET {rid}/{stage} → pending (attempt {attempts})")

    save_state(state)


def cmd_logs(repo_id: str, stage: str) -> None:
    log_path = LOGS_DIR / "sessions" / f"{repo_id}_{stage}.log"
    if log_path.exists():
        print(log_path.read_text())
    else:
        # Check errors
        err_dir = LOGS_DIR / "errors"
        if err_dir.exists():
            for f in sorted(err_dir.glob(f"{repo_id}_{stage}_*.log")):
                print(f"=== {f.name} ===")
                print(f.read_text())
                print()
        else:
            print(f"No logs found for {repo_id}/{stage}")


def cmd_collect(state: dict) -> None:
    """Merge per-task JSONs into JSONL for all completed repos."""
    merge_script = RANKING_DIR / "infra" / "merge_ground_truth.py"
    for rid, rd in state["repos"].items():
        if rd.get("review", {}).get("status") in ("done", "merged"):
            repo_dir = DATA_DIR / rid
            if repo_dir.exists():
                import subprocess
                r = subprocess.run(
                    f"python3 {merge_script} {repo_dir}",
                    shell=True, capture_output=True, text=True, check=False,
                )
                if r.returncode == 0:
                    print(f"  {rid}: merged")
                else:
                    print(f"  {rid}: FAILED — {r.stderr[:100]}")


# ── Main ──


def main():
    parser = argparse.ArgumentParser(description="GT pipeline orchestrator (Copilot SDK)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status")

    run_p = sub.add_parser("run")
    run_p.add_argument("--stage", choices=STAGES, default=None)
    run_p.add_argument("--repo", default=None)
    run_p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)

    retry_p = sub.add_parser("retry")
    retry_p.add_argument("target", nargs="?", default=None)

    logs_p = sub.add_parser("logs")
    logs_p.add_argument("repo_id")
    logs_p.add_argument("stage", choices=STAGES)

    sub.add_parser("collect")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    state = load_state()

    if args.command == "status":
        cmd_status(state)
    elif args.command == "run":
        asyncio.run(cmd_run(state, args.stage, args.repo, args.concurrency))
    elif args.command == "retry":
        cmd_retry(state, args.target)
    elif args.command == "logs":
        cmd_logs(args.repo_id, args.stage)
    elif args.command == "collect":
        cmd_collect(state)


if __name__ == "__main__":
    main()
