"""Agent traces dataset — loads pre-processed chatreplay traces.

Registered as ``@dataset("cpl-agent-traces")`` for EVEE evaluation.

Each record contains:
    session_name:   Canonical name (repo_issue_model_variant)
    issue:          GitHub issue number
    model:          Agent model (e.g. claude-opus-4.6-fast)
    variant:        "codeplane" or "native"
    events:         List of trace events
    outcome:        Pre-scored outcome dict (may be empty)
"""

from __future__ import annotations

import json
from pathlib import Path

from evee import dataset


@dataset("cpl-agent-traces")
class AgentTracesDataset:
    """Loads pre-processed agent traces from a directory of trace JSON files.

    Expects ``*_trace.json`` files produced by ``preprocessing/chatreplay_to_traces.py``
    or the existing ``benchmarking/extract_trace.py``.

    Config args:
        traces_dir: Directory containing *_trace.json files
    """

    def __init__(self, traces_dir: str = "data/traces", **kwargs: object) -> None:
        traces_path = Path(traces_dir)
        if not traces_path.exists():
            msg = (
                f"Traces directory not found: {traces_path.resolve()}\n"
                "Copy trace files or run: python -m preprocessing.chatreplay_to_traces"
            )
            raise FileNotFoundError(msg)

        self.records = self._load_traces(traces_path)

    def _load_traces(self, traces_dir: Path) -> list[dict]:
        """Load and normalize all *_trace.json files."""
        records: list[dict] = []

        for trace_file in sorted(traces_dir.glob("*_trace.json")):
            with open(trace_file) as f:
                trace = json.load(f)

            records.append(
                {
                    "session_name": trace.get("session_name", trace_file.stem),
                    "issue": str(trace.get("issue", "unknown")),
                    "model": trace.get("model", "unknown"),
                    "variant": "codeplane" if trace.get("codeplane", False) else "native",
                    "events": trace.get("events", []),
                    "outcome": trace.get("outcome", {}),
                }
            )

        return records

    def __iter__(self):
        yield from self.records

    def __len__(self) -> int:
        return len(self.records)
