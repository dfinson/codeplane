"""Evaluate models via EVEE — in-process pipeline evaluation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click


def run_eval(
    config: dict[str, Any],
    experiment: str | None = None,
    model_dir: Path | None = None,
    verbose: bool = False,
) -> None:
    """Run EVEE evaluation using the in-process eval project.

    Uses cpl_lab.eval components (model, dataset, metrics) registered
    via EVEE decorators.  Defaults to the built-in experiment YAML.
    """
    from cpl_lab.eval.run import run

    # Resolve experiment file — accept a bare filename or full path
    if experiment is not None:
        exp_path = Path(experiment)
        if not exp_path.is_file():
            # Try as a name under the eval/experiments dir
            pkg_dir = Path(__file__).resolve().parent / "eval" / "experiments"
            exp_path = pkg_dir / experiment
        if not exp_path.is_file():
            raise click.ClickException(f"Experiment not found: {experiment}")
        config_path = str(exp_path)
    else:
        config_path = None  # run() defaults to eval_pipeline.yaml

    click.echo(f"Running EVEE evaluation: {config_path or 'eval_pipeline.yaml'}")
    run(config_path)
