"""Vendor only the licensed, non-ML TF 0.1.5 format compatibility assets.

Run explicitly with `uv run --no-sync python tools/vendor_traceformats.py` in an
environment containing the released traceforge-toolkit==0.1.5 wheel. This does
not import or initialize TraceForge and never fetches model assets.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import distribution
from pathlib import Path

FORMATS = frozenset(
    [
        "aider",
        "aider_markdown",
        "amazonq",
        "antigravity",
        "claude",
        "cline",
        "codex",
        "continue_dev",
        "copilot",
        "copilot_markdown",
        "copilot_vscode",
        "crewai",
        "goose",
        "langchain",
        "langgraph",
        "maf",
        "maf_transcript",
        "openai_agents",
        "opencode",
        "openhands",
        "pydantic_ai",
        "semantic_kernel",
        "smolagents",
        "sweagent",
    ]
)


def main() -> None:
    dist = distribution("traceforge-toolkit")
    if dist.version != "0.1.5":
        raise RuntimeError("Compatibility assets must come from the released 0.1.5 distribution")
    source = Path(dist.locate_file("traceforge"))
    destination = Path(__file__).resolve().parents[1] / "backend" / "compat" / "traceformats"
    mapping_names = {path.stem for path in (source / "mappings").glob("*.yaml")}
    if mapping_names != FORMATS:
        raise RuntimeError(f"Released mapping set changed: {mapping_names ^ FORMATS}")
    files = sorted(
        [
            *(source / "mappings").glob("*.yaml"),
            *(source / "preprocessors").glob("*.py"),
            *(source / "parsers").glob("*.py"),
        ]
    )
    hashes = {}
    for path in files:
        relative = path.relative_to(source)
        raw = path.read_bytes()
        hashes[str(relative)] = hashlib.sha256(raw).hexdigest()
        target = destination / relative
        if target.exists():
            raise RuntimeError(f"Refusing to overwrite existing compatibility asset: {target}")
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            content = (
                "# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.\n"
                + content.replace("traceforge.preprocessors", "backend.compat.traceformats.preprocessors").replace(
                    "traceforge.parsers", "backend.compat.traceformats.parsers"
                )
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (destination / "provenance.json").write_text(
        json.dumps(
            {
                "distribution": "traceforge-toolkit",
                "version": dist.version,
                "source_revision": "46e0340",
                "license": "../traceforge.LICENSE",
                "original_sha256": hashes,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Vendored {len(FORMATS)} mappings and {len(files) - len(FORMATS)} parser/preprocessor modules")


if __name__ == "__main__":
    main()
