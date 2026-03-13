"""Configuration resolution for cpl-lab.

Priority (highest wins):
  1. CLI flags (--workspace, etc.)
  2. lab.toml in the recon-lab project root
  3. Built-in defaults
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.version_info >= (3, 12):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

# Repo-local paths (versioned source).
LAB_ROOT = Path(__file__).resolve().parent.parent.parent  # recon-lab/
REPOS_DIR = LAB_ROOT / "repos"
ROLES_DIR = LAB_ROOT / "roles"
INFRA_DIR = LAB_ROOT / "infra"

# Default lab.toml location.
_DEFAULT_CONFIG = LAB_ROOT / "lab.toml"


def _load_toml(path: Path) -> dict:
    """Load a TOML file, returning {} if it doesn't exist."""
    if not path.is_file():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def resolve_workspace(cli_override: str | None = None) -> Path:
    """Resolve the workspace directory.

    Priority: CLI flag > lab.toml > default (~/.cpl-lab).
    """
    if cli_override:
        return Path(cli_override).expanduser().resolve()

    cfg = _load_toml(_DEFAULT_CONFIG)
    toml_path = cfg.get("workspace", {}).get("path")
    if toml_path:
        return Path(toml_path).expanduser().resolve()

    return Path.home() / ".cpl-lab"


def get_config(cli_override: str | None = None) -> dict:
    """Return merged config dict with resolved workspace paths."""
    cfg = _load_toml(_DEFAULT_CONFIG)
    ws = resolve_workspace(cli_override)

    return {
        "workspace": ws,
        "clones_dir": ws / "clones",
        "data_dir": ws / "data",
        "models_dir": ws / "models",
        "repos_dir": REPOS_DIR,
        "roles_dir": ROLES_DIR,
        "infra_dir": INFRA_DIR,
        "lab_root": LAB_ROOT,
        "clone": {
            "jobs": cfg.get("clone", {}).get("jobs", 4),
            "depth": cfg.get("clone", {}).get("depth", 1),
        },
        "index": {
            "timeout": cfg.get("index", {}).get("timeout", 1800),
        },
        "generate": {
            "concurrency": cfg.get("generate", {}).get("concurrency", 5),
            "model": cfg.get("generate", {}).get("model", "claude-opus-4"),
        },
        "eval": {
            "default_experiment": cfg.get("eval", {}).get(
                "default_experiment", "recon_ranking.yaml"
            ),
        },
    }
