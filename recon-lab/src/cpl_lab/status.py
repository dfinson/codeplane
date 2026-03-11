"""Pipeline status dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from cpl_lab.clone import REPO_SETS

STAGES = ["audit", "exec_n", "exec_m", "exec_w", "review"]
STAGE_LABELS = {
    "audit": "Audit",
    "exec_n": "Exec N",
    "exec_m": "Exec M",
    "exec_w": "Exec W",
    "review": "Review",
}
PIPELINE_STEPS = ["clone", "index", "generate", "collect", "merge", "train"]


def _load_gt_state(data_dir: Path) -> dict | None:
    state_file = data_dir / "gt_state.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return None


def _current_stage(state: dict) -> str | None:
    """Determine which GT stage the pipeline is currently on."""
    repos = state.get("repos", {})
    if not repos:
        return None
    for stage in STAGES:
        done = sum(
            1 for rd in repos.values()
            if rd.get(stage, {}).get("status") in ("done", "merged")
        )
        if done < len(repos):
            return stage
    return "complete"


def _stage_counts(state: dict, stage: str) -> dict[str, int]:
    """Count repos in each status for a given stage."""
    counts: dict[str, int] = {"done": 0, "active": 0, "failed": 0, "pending": 0}
    for rd in state.get("repos", {}).values():
        st = rd.get(stage, {}).get("status", "pending")
        if st in ("done", "merged"):
            counts["done"] += 1
        elif st in counts:
            counts[st] += 1
        else:
            counts["pending"] += 1
    return counts


def _pipeline_position(
    clones_dir: Path,
    data_dir: Path,
    models_dir: Path,
    gt_state: dict | None,
) -> tuple[str, str]:
    """Return (current_step, detail) for the overall pipeline."""
    # Check clones
    total_expected = sum(len(m) for m in REPO_SETS.values())
    total_cloned = 0
    for set_name, manifest in REPO_SETS.items():
        sd = clones_dir / set_name
        if sd.is_dir():
            total_cloned += sum(1 for d in sd.iterdir() if (d / ".git").is_dir())
    if total_cloned < total_expected:
        return "clone", f"{total_cloned}/{total_expected} repos cloned"

    # Check indexing
    total_indexed = 0
    for set_name in REPO_SETS:
        sd = clones_dir / set_name
        if sd.is_dir():
            total_indexed += sum(1 for d in sd.iterdir() if (d / ".codeplane").is_dir())
    if total_indexed < total_expected:
        return "index", f"{total_indexed}/{total_expected} repos indexed"

    # Check GT generation
    if gt_state is None:
        return "generate", "gt_state.json not found — run 'cpl-lab generate run'"
    current = _current_stage(gt_state)
    if current and current != "complete":
        label = STAGE_LABELS.get(current, current)
        counts = _stage_counts(gt_state, current)
        total = sum(counts.values())
        return "generate", f"stage {label}: {counts['done']}/{total} done, {counts['active']} active, {counts['failed']} failed"
    if current == "complete":
        pass  # fall through to signals

    # Check signals
    sig_repos = 0
    if data_dir.is_dir():
        for rd in data_dir.iterdir():
            if rd.is_dir() and rd.name != "merged" and (rd / "signals" / "candidates_rank.jsonl").exists():
                sig_repos += 1
    gt_repos = len(gt_state.get("repos", {})) if gt_state else 0
    if sig_repos < gt_repos:
        return "collect", f"{sig_repos}/{gt_repos} repos have signals"

    # Check merge
    merged = data_dir / "merged"
    if not merged.is_dir() or not list(merged.glob("*.parquet")):
        return "merge", "no merged parquets yet"

    # Check training
    if not models_dir.is_dir() or not list(models_dir.glob("*.lgbm")):
        return "train", "no trained models yet"
    trained = sorted(m.stem for m in models_dir.glob("*.lgbm"))
    expected = {"ranker", "cutoff", "gate"}
    missing = expected - set(trained)
    if missing:
        return "train", f"missing models: {', '.join(sorted(missing))}"

    return "done", "pipeline complete — ready for eval"


def run_status(config: dict[str, Any], verbose: bool = False) -> None:
    """Show pipeline state across all stages."""
    clones_dir: Path = config["clones_dir"]
    data_dir: Path = config["data_dir"]
    models_dir: Path = config["models_dir"]

    click.echo(f"Workspace: {config['workspace']}")
    click.echo()

    # ── Pipeline Position ────────────────────────────────────────
    gt_state = _load_gt_state(data_dir)
    step, detail = _pipeline_position(clones_dir, data_dir, models_dir, gt_state)
    step_idx = PIPELINE_STEPS.index(step) if step in PIPELINE_STEPS else len(PIPELINE_STEPS)

    click.echo("=== Pipeline Position ===")
    for i, s in enumerate(PIPELINE_STEPS):
        if i < step_idx:
            click.echo(f"  ✓ {s}")
        elif i == step_idx:
            click.echo(f"  ▸ {s}  ← {detail}")
        else:
            click.echo(f"    {s}")
    if step == "done":
        click.echo(f"  ✓ {detail}")

    # ── GT Stage Breakdown ───────────────────────────────────────
    if gt_state and gt_state.get("repos"):
        click.echo("\n=== Ground Truth Stages ===")
        total = len(gt_state["repos"])
        for stage in STAGES:
            counts = _stage_counts(gt_state, stage)
            done = counts["done"]
            pct = done * 100 // total if total else 0
            filled = pct // 4
            bar = f"{'█' * filled}{'░' * (25 - filled)}"
            label = STAGE_LABELS.get(stage, stage)
            parts = [f"{done}/{total}"]
            if counts["active"]:
                parts.append(f"{counts['active']} active")
            if counts["failed"]:
                parts.append(f"{counts['failed']} failed")
            click.echo(f"  {label:8s} {bar} {' | '.join(parts)}")

    # ── Clones ───────────────────────────────────────────────────
    click.echo("\n=== Clones ===")
    for set_name, manifest in REPO_SETS.items():
        set_dir = clones_dir / set_name
        expected = len(manifest)
        cloned = sum(1 for d in set_dir.iterdir() if (d / ".git").is_dir()) if set_dir.is_dir() else 0
        indexed = sum(1 for d in set_dir.iterdir() if (d / ".codeplane").is_dir()) if set_dir.is_dir() else 0
        click.echo(f"  {set_name:14s}  cloned: {cloned:2d}/{expected:2d}  indexed: {indexed:2d}/{expected:2d}")

    # ── Ground Truth ─────────────────────────────────────────────
    click.echo("\n=== Ground Truth ===")
    gt_repos = 0
    gt_tasks = 0
    if data_dir.is_dir():
        for rd in sorted(data_dir.iterdir()):
            if not rd.is_dir() or rd.name == "merged":
                continue
            gt_dir = rd / "ground_truth"
            if gt_dir.is_dir():
                tasks = list(gt_dir.glob("*.json"))
                if tasks:
                    gt_repos += 1
                    gt_tasks += len(tasks)
                    if verbose:
                        click.echo(f"  {rd.name}: {len(tasks)} tasks")
    click.echo(f"  Repos with GT: {gt_repos}  Total tasks: {gt_tasks}")

    # ── Signals ──────────────────────────────────────────────────
    click.echo("\n=== Signals ===")
    sig_repos = 0
    if data_dir.is_dir():
        for rd in sorted(data_dir.iterdir()):
            if not rd.is_dir() or rd.name == "merged":
                continue
            if (rd / "signals" / "candidates_rank.jsonl").exists():
                sig_repos += 1
    click.echo(f"  Repos with signals: {sig_repos}")

    # ── Merged ───────────────────────────────────────────────────
    click.echo("\n=== Merged Data ===")
    merged = data_dir / "merged"
    if merged.is_dir():
        for f in sorted(merged.glob("*.parquet")):
            size_mb = f.stat().st_size / (1024 * 1024)
            click.echo(f"  {f.name}: {size_mb:.1f} MB")
    else:
        click.echo("  (not yet merged)")

    # ── Models ───────────────────────────────────────────────────
    click.echo("\n=== Models ===")
    if models_dir.is_dir():
        models = list(models_dir.glob("*.lgbm"))
        if models:
            for m in sorted(models):
                size_kb = m.stat().st_size / 1024
                click.echo(f"  {m.name}: {size_kb:.0f} KB")
        else:
            click.echo("  (no trained models)")
    else:
        click.echo("  (models directory not found)")
