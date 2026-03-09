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
DEFAULT_CONCURRENCY = 10
MAX_ATTEMPTS = 3
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF = [30, 60, 120, 300]  # seconds between retries

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

# Retryable error detection — prefer typed exceptions, fall back to heuristics

def _is_retryable(exc: Exception) -> bool:
    """Determine if an exception is a transient/rate-limit error worth retrying."""
    try:
        from copilot.jsonrpc import JsonRpcError, ProcessExitedError
        if isinstance(exc, JsonRpcError):
            # HTTP 429 or server-side rate limit codes
            if isinstance(exc.code, int) and exc.code in (429, -32000, -32001, -32603):
                return True
        if isinstance(exc, ProcessExitedError):
            return True  # CLI crashed — always retry
    except ImportError:
        pass

    # Fallback: string heuristic for errors that don't use typed exceptions
    err = str(exc).lower()
    return any(k in err for k in ("rate limit", "429", "too many", "throttl", "capacity", "overloaded", "try again later", "service unavailable", "503"))

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

    def log(self, msg: str, level: str = "INFO") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        prefix = {"INFO": "│", "TOOL": "├─🔧", "WRITE": "├─📝", "ERROR": "├─❌", "DONE": "└─✅", "START": "┌─▶"}.get(level, "│")
        with open(self.log_path, "a") as f:
            f.write(f"[{ts}] {prefix} {msg}\n")

    def elapsed(self) -> str:
        secs = int(time.time() - self.start_time)
        if secs < 60:
            return f"{secs}s"
        return f"{secs // 60}m"

    def build_tools(self):
        from copilot import define_tool

        runner = self

        @define_tool(description="Write a validated ground truth JSON for a completed task. Call this after solving each task.")
        async def write_ground_truth(params: WriteGTParams) -> str:
            errors = validate_gt_schema(params.data)
            if errors:
                runner.log(f"Schema validation failed for {params.task_id}: {errors}", "ERROR")
                return f"VALIDATION FAILED — fix these errors and call again:\n" + "\n".join(f"  - {e}" for e in errors)
            path = DATA_DIR / params.repo_id / "ground_truth" / f"{params.task_id}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(params.data, indent=2) + "\n")
            runner.jsons_written += 1
            runner.log(f"{params.task_id}.json written ({runner.jsons_written}/11)", "WRITE")
            runner.status_line = f"wrote {params.task_id}.json ({runner.jsons_written}/11)"
            return f"Written successfully: {path} ({runner.jsons_written} JSONs total)"

        @define_tool(description="Write the non-OK queries JSON file to ground_truth/non_ok_queries.json. Call AFTER all W tasks are done (executor session C only). This file goes in the SAME directory as the task JSONs.")
        async def write_non_ok_queries(params: WriteNonOKParams) -> str:
            errors = validate_non_ok_schema(params.data)
            if errors:
                runner.log(f"Non-OK validation failed: {errors}", "ERROR")
                return f"VALIDATION FAILED:\n" + "\n".join(f"  - {e}" for e in errors)
            path = DATA_DIR / params.repo_id / "ground_truth" / "non_ok_queries.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(params.data, indent=2) + "\n")
            runner.log(f"non_ok_queries.json written ({len(params.data.get('non_ok_queries', []))} queries)", "WRITE")
            return f"Written: {path}"

        @define_tool(description="Signal that you have completed all assigned tasks for this session.")
        async def report_complete(params: ReportCompleteParams) -> str:
            runner.log(f"{params.summary}", "DONE")
            runner.status_line = "✅ done"
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

        self.log(f"{self.repo_id}/{self.stage} cwd={clone_dir}", "START")
        self.status_line = "initializing..."

        for attempt in range(RATE_LIMIT_RETRIES):
            try:
                await self._run_session(clone_dir)
                return  # success
            except Exception as e:
                if _is_retryable(e) and attempt < RATE_LIMIT_RETRIES - 1:
                    wait = RATE_LIMIT_BACKOFF[attempt]
                    self.log(f"Retryable error, waiting {wait}s (attempt {attempt + 1}/{RATE_LIMIT_RETRIES}): {e}", "ERROR")
                    self.status_line = f"⏳ retry in {wait}s"
                    await asyncio.sleep(wait)
                    continue
                raise  # not retryable, or exhausted retries

    async def _run_session(self, clone_dir: Path) -> None:
        from copilot import CopilotClient, PermissionHandler

        client = CopilotClient({
            "cwd": str(clone_dir),
            "log_level": "warning",
        })
        await client.start()

        try:
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
                    for line in reversed(content.split("\n")):
                        line = line.strip()
                        if line and len(line) > 5:
                            self.status_line = line[:60]
                            break
                elif etype == "session.idle":
                    if not self.done_event.is_set():
                        self.log("Session ended naturally", "DONE")
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

        self.log(f"Completed in {self.elapsed()} — {self.jsons_written} JSONs written", "DONE")


# ── Progress display ──


def cmd_status(state: dict) -> None:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text

    console = Console()
    stage = current_global_stage(state)
    total = len(state.get("repos", {}))

    # Stage progress
    stage_table = Table(show_header=True, box=None, padding=(0, 2))
    stage_table.add_column("Stage", style="bold")
    stage_table.add_column("Progress", min_width=32)
    stage_table.add_column("Done", justify="right")
    stage_table.add_column("Active", justify="right", style="cyan")
    stage_table.add_column("Failed", justify="right", style="red")
    stage_table.add_column("Pending", justify="right", style="dim")
    stage_table.add_column("", width=2)

    for s in STAGES:
        counts = {}
        for rd in state["repos"].values():
            st = rd.get(s, {}).get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
        done = counts.get("merged", 0) + counts.get("done", 0)
        active = counts.get("active", 0)
        failed = counts.get("failed", 0)
        pending = counts.get("pending", 0)
        pct = done / total if total else 0
        filled = int(pct * 25)
        bar = f"[green]{'━' * filled}[/green][dim]{'─' * (25 - filled)}[/dim] {done*100//total}%"
        marker = "[bold yellow]◀[/bold yellow]" if s == stage else ""
        s_display = {
            "audit": "Audit", "exec_n": "Exec N", "exec_m": "Exec M",
            "exec_w": "Exec W", "review": "Review",
        }.get(s, s)
        stage_table.add_row(
            s_display, bar, str(done),
            str(active) if active else "·",
            str(failed) if failed else "·",
            str(pending) if pending else "·",
            marker,
        )

    console.print(Panel(stage_table, title="[bold]Ground Truth Pipeline[/bold]", subtitle=f"stage: [yellow]{stage}[/yellow]", border_style="blue"))

    # Failed details
    failed_list = []
    for rid, rd in state["repos"].items():
        for s in STAGES:
            if rd.get(s, {}).get("status") == "failed":
                err = rd[s].get("error", "?")[:50]
                att = rd[s].get("attempts", "?")
                failed_list.append(f"  [red]✗[/red] {rid}/{s} (attempt {att}): {err}")
    if failed_list:
        console.print()
        for f in failed_list:
            console.print(f)


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
        async with sem:  # Gate EVERYTHING — session creation included
            runner = SessionRunner(repo_id, stage, state)
            runners[repo_id] = runner

            # Mark active
            rd = state["repos"].get(repo_id, {})
            attempts = rd.get(stage, {}).get("attempts", 0) + 1
            rd[stage] = {"status": "active", "attempts": attempts}
            save_state(state)

            try:
                await runner.run()

                # If session ended naturally (session.idle) without report_complete,
                # mark as done anyway — auditor sessions don't use report_complete
                if rd.get(stage, {}).get("status") == "active":
                    rd[stage] = {"status": "done", "jsons": runner.jsons_written}
                    save_state(state)

            except asyncio.TimeoutError:
                runner.log("TIMEOUT after 2h", "ERROR")
                rd[stage] = {"status": "failed", "error": "timeout", "attempts": attempts}
                save_state(state)
                _archive_log(repo_id, stage, attempts)
            except Exception as e:
                runner.log(f"{e}", "ERROR")
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
        from rich.console import Group
        total = len(state.get("repos", {}))

        # Stage bars
        stage_lines = []
        for s in STAGES:
            done = sum(1 for rd in state["repos"].values() if rd.get(s, {}).get("status") in ("merged", "done"))
            active = sum(1 for rd in state["repos"].values() if rd.get(s, {}).get("status") == "active")
            pct = done / total if total else 0
            filled = int(pct * 20)
            bar = f"[green]{'━' * filled}[/green][dim]{'─' * (20 - filled)}[/dim]"
            marker = " [bold yellow]◀[/bold yellow]" if s == current_global_stage(state) else ""
            s_name = {"audit": "Audit", "exec_n": "Exec N", "exec_m": "Exec M",
                      "exec_w": "Exec W", "review": "Review"}.get(s, s)
            active_str = f" [cyan]+{active}[/cyan]" if active else ""
            stage_lines.append(Text.from_markup(f"  {s_name:8} {bar} {done}/{total}{active_str}{marker}"))

        # Active sessions table
        if runners:
            session_table = Table(show_header=True, box=None, padding=(0, 1), show_edge=False)
            session_table.add_column("", width=1)
            session_table.add_column("Repo", style="bold", min_width=28)
            session_table.add_column("Time", justify="right", style="cyan", width=5)
            session_table.add_column("JSONs", justify="right", style="green", width=5)
            session_table.add_column("Status", max_width=45, no_wrap=True)

            for rid in sorted(runners.keys()):
                runner = runners[rid]
                jsons = str(runner.jsons_written) if runner.jsons_written else "·"
                status = runner.status_line[:45]
                session_table.add_row("●", rid, runner.elapsed(), jsons, status)
        else:
            session_table = Text("[dim]  No active sessions[/dim]")

        # Footer
        cur_stage = current_global_stage(state)
        pending_n = len(repos_for_stage(state, cur_stage, "pending"))
        failed_n = len(repos_for_stage(state, cur_stage, "failed"))
        done_n = sum(1 for rd in state["repos"].values() if rd.get(cur_stage, {}).get("status") in ("merged", "done"))
        elapsed_total = ""
        if runners:
            oldest = min(r.start_time for r in runners.values())
            elapsed_total = f" │ wall: {int((time.time() - oldest) / 60)}m"

        footer = Text.from_markup(
            f"  [dim]{done_n}[/dim] done  [cyan]{len(runners)}[/cyan]/{concurrency} active  "
            f"[dim]{pending_n}[/dim] queued  [red]{failed_n}[/red] failed{elapsed_total}"
        )

        return Panel(
            Group(*stage_lines, Text(""), session_table, Text(""), footer),
            title="[bold]Ground Truth Pipeline[/bold]",
            border_style="blue",
        )

    # ── Main loop: run stages until all done or blocked ──

    while True:
        # Reload state each iteration (might have been updated by sessions)
        state = load_state()

        stage = stage_filter or current_global_stage(state)
        if stage == "done":
            console.print("[bold green]✅ All stages complete![/bold green]")
            return

        if repo_filter:
            targets = [repo_filter] if state["repos"].get(repo_filter, {}).get(stage, {}).get("status") == "pending" else []
        else:
            targets = repos_for_stage(state, stage, "pending")

        if not targets:
            # Check if there are failed repos blocking progress
            failed = repos_for_stage(state, stage, "failed")
            if failed:
                console.print(f"[yellow]Stage {stage}: {len(failed)} failed, 0 pending. Run 'retry' to reset.[/yellow]")
                return
            # Stage might be complete
            all_done = all(
                rd.get(stage, {}).get("status") in ("merged", "done")
                for rd in state["repos"].values()
            )
            if all_done:
                for rd in state["repos"].values():
                    if rd.get(stage, {}).get("status") == "done":
                        rd[stage]["status"] = "merged"
                save_state(state)
                console.print(f"[bold green]━━━ Stage {stage} complete! ━━━[/bold green]")
                if stage_filter or repo_filter:
                    return
                continue  # next stage
            else:
                console.print(f"[yellow]Stage {stage}: nothing to do[/yellow]")
                return

        console.print(f"[bold]━━━ Launching {len(targets)} session(s) for stage {stage} ━━━[/bold]")

        # Create all tasks, semaphore controls concurrency
        flight = [asyncio.create_task(run_one(rid, stage)) for rid in targets]
        with Live(render_display(), console=console, refresh_per_second=0.5) as live:
            while not all(t.done() for t in flight):
                live.update(render_display())
                await asyncio.sleep(2)
            live.update(render_display())

        # Report any exceptions
        for t, rid in zip(flight, targets):
            exc = t.exception() if t.done() and not t.cancelled() else None
            if exc:
                console.print(f"  [red]✗ {rid}: {exc}[/red]")

        # Post-batch: advance stage if all done
        state = load_state()
        all_done = all(
            rd.get(stage, {}).get("status") in ("merged", "done")
            for rd in state["repos"].values()
        )
        if all_done:
            for rd in state["repos"].values():
                if rd.get(stage, {}).get("status") == "done":
                    rd[stage]["status"] = "merged"
            save_state(state)
            console.print(f"[bold green]━━━ Stage {stage} complete! ━━━[/bold green]")
            if stage_filter or repo_filter:
                return
            # loop continues to next stage
        else:
            # Some failed — stop unless running full auto
            failed = repos_for_stage(state, stage, "failed")
            if failed and not stage_filter and not repo_filter:
                console.print(f"[yellow]Stage {stage}: {len(failed)} failed. Run 'retry' to reset, then 'run' again.[/yellow]")
                return
            elif repo_filter or stage_filter:
                return


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
