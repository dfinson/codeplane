#!/usr/bin/env python3
"""Migrate loose backend/services/*.py files into sub-packages.

Moves files, creates __init__.py, and rewrites ALL imports across the codebase.
No shims, no backward compat — direct import path updates.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SERVICES = BACKEND / "services"

# ── Package definitions: package_name → list of files to move ──────────────
PACKAGES: dict[str, list[str]] = {
    "analytics": [
        "analytics_service.py",
        "cost_attribution.py",
        "latency_attribution.py",
        "statistical_analysis.py",
        "telemetry.py",
        "telemetry_query_service.py",
    ],
    "adapters": [
        "adapter_registry.py",
        "agent_adapter.py",
        "base_adapter.py",
        "platform_adapter.py",
        "sdk_event_mapping.py",
    ],
    "events": [
        "event_bus.py",
        "event_enricher.py",
        "event_processor.py",
        "sse_manager.py",
        "ingest_service.py",
    ],
    "completers": [
        "conversation_ledger.py",
        "lightweight_completer.py",
        "naming_service.py",
        "narrator_completer.py",
        "summarization_service.py",
        "copilot_steer.py",
        "voice_service.py",
    ],
    "sharing": [
        "share_service.py",
        "tunnel_service.py",
        "push_service.py",
        "vapid_keys.py",
    ],
    "auth": [
        "auth.py",
        "cf_access.py",
        "permission_policy.py",
    ],
    "artifacts": [
        "artifact_service.py",
        "diff_service.py",
        "snapshot_helpers.py",
    ],
    "job": [
        "job_service.py",
        "approval_service.py",
        "retry_tracker.py",
        "retention_service.py",
    ],
    "tools": [
        "tool_classifier.py",
        "preflight_curator.py",
        "parsing_utils.py",
    ],
    "terminal": [
        "terminal_service.py",
    ],
    "git": [
        "git_service.py",
    ],
}

# Dead file to remove
DEAD_FILES = ["_verify_diff.py"]


def build_import_map() -> dict[str, str]:
    """Build old_module → new_module mapping for import rewriting.

    e.g. 'backend.services.git_service' → 'backend.services.git.git_service'
    """
    mapping: dict[str, str] = {}
    for pkg, files in PACKAGES.items():
        for fname in files:
            module = fname.removesuffix(".py")
            old = f"backend.services.{module}"
            new = f"backend.services.{pkg}.{module}"
            mapping[old] = new
    return mapping


def move_files() -> None:
    """git mv files into their sub-package directories."""
    for pkg, files in PACKAGES.items():
        pkg_dir = SERVICES / pkg
        if not pkg_dir.exists():
            pkg_dir.mkdir(parents=True)
            print(f"  Created {pkg_dir.relative_to(ROOT)}/")

        for fname in files:
            src = SERVICES / fname
            dst = pkg_dir / fname
            if src.exists():
                subprocess.run(
                    ["git", "mv", str(src), str(dst)],
                    check=True,
                    cwd=ROOT,
                )
                print(f"  git mv {fname} → {pkg}/{fname}")
            else:
                print(f"  SKIP {fname} (not found)")


def remove_dead_files() -> None:
    """Remove dead files."""
    for fname in DEAD_FILES:
        fpath = SERVICES / fname
        if fpath.exists():
            subprocess.run(
                ["git", "rm", str(fpath)],
                check=True,
                cwd=ROOT,
            )
            print(f"  git rm {fname}")


def create_init_files() -> None:
    """Create __init__.py for each new sub-package with docstring only."""
    for pkg in PACKAGES:
        init = SERVICES / pkg / "__init__.py"
        if init.exists():
            print(f"  SKIP {pkg}/__init__.py (already exists)")
            continue

        # Simple docstring-only __init__.py
        desc = {
            "analytics": "Analytics, cost attribution, and telemetry services.",
            "adapters": "Agent adapter abstraction layer.",
            "events": "Event bus, SSE, and event processing pipeline.",
            "completers": "LLM completion, summarization, and naming services.",
            "sharing": "Sharing, tunnels, and push notification services.",
            "auth": "Authentication and permission services.",
            "artifacts": "Artifact storage, diffs, and snapshot services.",
            "job": "Job lifecycle, approval, and retention services.",
            "tools": "Tool classification, preflight curation, and parsing utilities.",
            "terminal": "Terminal session management.",
            "git": "Git operations service.",
        }
        content = f'"""{desc.get(pkg, pkg.title() + " services.")}"""\n'
        init.write_text(content)
        subprocess.run(["git", "add", str(init)], check=True, cwd=ROOT)
        print(f"  Created {pkg}/__init__.py")


def rewrite_imports(import_map: dict[str, str]) -> int:
    """Rewrite all imports across the codebase. Returns count of files changed."""

    # Sort by length descending so longer paths match first
    # (e.g. backend.services.analytics_service before backend.services)
    sorted_old = sorted(import_map.keys(), key=len, reverse=True)

    # Build regex that matches any of the old module paths
    # We need word boundaries to avoid partial matches
    patterns: list[tuple[re.Pattern[str], str]] = []
    for old in sorted_old:
        new = import_map[old]
        # Match the old path as a complete dotted path segment
        pat = re.compile(re.escape(old) + r"(?=[\s.,;:)\]\n]|$)")
        patterns.append((pat, new))

    changed_count = 0
    py_files = list(BACKEND.rglob("*.py"))

    for fpath in py_files:
        if "__pycache__" in str(fpath):
            continue

        text = fpath.read_text()
        new_text = text

        for pat, replacement in patterns:
            new_text = pat.sub(replacement, new_text)

        if new_text != text:
            fpath.write_text(new_text)
            changed_count += 1
            rel = fpath.relative_to(ROOT)
            print(f"  Rewrote imports in {rel}")

    return changed_count


def update_services_init() -> None:
    """Update backend/services/__init__.py to reflect new structure."""
    init_path = SERVICES / "__init__.py"
    if not init_path.exists():
        return

    text = init_path.read_text()
    # The __init__.py likely has docstring references to flat modules.
    # We need to update those references too.
    import_map = build_import_map()

    sorted_old = sorted(import_map.keys(), key=len, reverse=True)
    new_text = text
    for old in sorted_old:
        new = import_map[old]
        # Also handle plain module names in comments/docstrings
        old_module = old.split(".")[-1]
        new_pkg = new.split(".")[-2]
        # Replace references like "``analytics_service``" with "``analytics/analytics_service``"
        new_text = new_text.replace(
            f"``{old_module}``", f"``{new_pkg}/{old_module}``"
        )

    if new_text != text:
        init_path.write_text(new_text)
        print(f"  Updated services/__init__.py docstring")


def main() -> None:
    print("=== Step 1: Move files into sub-packages ===")
    move_files()

    print("\n=== Step 2: Remove dead files ===")
    remove_dead_files()

    print("\n=== Step 3: Create __init__.py files ===")
    create_init_files()

    print("\n=== Step 4: Rewrite imports ===")
    import_map = build_import_map()
    n = rewrite_imports(import_map)
    print(f"  → {n} files updated")

    print("\n=== Step 5: Update services/__init__.py docstring ===")
    update_services_init()

    print("\n=== Done! Run these to verify: ===")
    print("  uv run ruff check backend/")
    print("  uv run mypy backend/")
    print("  uv run pytest -x -q")


if __name__ == "__main__":
    main()
