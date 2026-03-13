"""In-process ranking model — runs the full pipeline without a daemon.

Registered as ``@model("cpl-ranking")`` for EVEE evaluation.

Loads each repo's index in-process via ``AppContext``, then runs
raw_signals → gate → ranker → cutoff.  Caches one ``AppContext`` at a
time (repos are processed sequentially via ``max_workers: 1``).
"""

from __future__ import annotations

import asyncio
import gc
import logging
import sys
from pathlib import Path

from evee import model


def _def_key(c: dict) -> str:
    """Canonical candidate key matching ground truth format."""
    return f"{c.get('path', '')}:{c.get('kind', '')}:{c.get('name', '')}:{c.get('start_line', 0)}"


@model("cpl-ranking")
class RankingModel:
    """In-process ranking pipeline for EVEE evaluation.

    Args:
        clone_dir: Root directory containing cloned eval repos.
    """

    def __init__(self, clone_dir: str = "~/.cpl-lab/clones", **kwargs: object) -> None:
        self._clone_root = Path(clone_dir).expanduser()
        self._cached_repo: str | None = None
        self._ctx = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._gate = None
        self._ranker = None
        self._cutoff = None

    def _ensure_context(self, repo_id: str) -> None:
        """Load (or re-use cached) AppContext for *repo_id*."""
        if self._cached_repo == repo_id and self._ctx is not None:
            return

        # Release previous context to free memory (tantivy, sqlite, embeddings)
        if self._ctx is not None:
            self._ctx.coordinator.close()
            self._ctx = None
            self._cached_repo = None
            gc.collect()

        # Lazy imports — keeps module importable without codeplane installed
        from codeplane.mcp.context import AppContext

        from cpl_lab.clone import REPO_MANIFEST

        info = REPO_MANIFEST.get(repo_id)
        if info is None:
            msg = f"Unknown repo_id: {repo_id}"
            raise ValueError(msg)
        clone_dir = self._clone_root / info["set"] / info["clone_name"]
        cp = clone_dir / ".codeplane"
        if not cp.exists():
            msg = f"No codeplane index at {cp}"
            raise FileNotFoundError(msg)

        # Silence noisy library loggers during eval
        logging.disable(logging.WARNING)

        self._ctx = AppContext.create(
            repo_root=clone_dir,
            db_path=cp / "index.db",
            tantivy_path=cp / "tantivy",
        )

        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

        self._loop.run_until_complete(self._ctx.coordinator.load_existing())
        self._cached_repo = repo_id

        # Cache gate/ranker/cutoff once per repo (avoids per-query warnings)
        from codeplane.ranking.cutoff import load_cutoff
        from codeplane.ranking.gate import load_gate
        from codeplane.ranking.ranker import load_ranker

        self._gate = load_gate()
        self._ranker = load_ranker()
        self._cutoff = load_cutoff()

    def infer(self, input: dict) -> dict:  # noqa: A002
        """Run the full pipeline for a single query record.

        Expects keys from the dataset: ``repo_id``, ``query_text``,
        ``seeds``, ``pins``.

        Returns ``ranked_def_uids``, ``predicted_relevances``,
        ``predicted_n``, ``predicted_gate``.
        """
        repo_id = input["repo_id"]
        query = input["query_text"]
        seeds = input.get("seeds") or None
        pins = input.get("pins") or None

        self._ensure_context(repo_id)
        assert self._ctx is not None  # noqa: S101
        assert self._loop is not None  # noqa: S101

        from codeplane.mcp.tools.recon.raw_signals import raw_signals_pipeline
        from codeplane.ranking.features import (
            extract_cutoff_features,
            extract_gate_features,
            extract_ranker_features,
        )
        from codeplane.ranking.models import GateLabel

        # 1. Raw signals
        raw = self._loop.run_until_complete(
            raw_signals_pipeline(self._ctx, query, seeds=seeds, pins=pins),
        )
        candidates = raw.get("candidates", [])
        query_features = raw.get("query_features", {})
        repo_features = raw.get("repo_features", {})

        # 2. Gate
        gate_features = extract_gate_features(candidates, query_features, repo_features)
        gate_label = self._gate.classify(gate_features)

        if gate_label != GateLabel.OK:
            return {
                "ranked_def_uids": [],
                "predicted_relevances": [],
                "predicted_n": 0,
                "predicted_gate": gate_label.value,
            }

        # 3. Rank
        ranker_features = extract_ranker_features(candidates, query_features)
        scores = self._ranker.score(ranker_features)
        scored = sorted(zip(candidates, scores), key=lambda x: -x[1])

        # 4. Cutoff
        cutoff = self._cutoff
        ranked_for_cutoff = [{**c, "ranker_score": s} for c, s in scored]
        cutoff_features = extract_cutoff_features(
            ranked_for_cutoff, query_features, repo_features,
        )
        predicted_n = cutoff.predict(cutoff_features)

        # 5. Build output in ground-truth key format
        ranked_def_uids = [_def_key(c) for c, _ in scored]
        predicted_relevances = [round(s, 4) for _, s in scored]

        return {
            "ranked_def_uids": ranked_def_uids,
            "predicted_relevances": predicted_relevances,
            "predicted_n": predicted_n,
            "predicted_gate": gate_label.value,
        }
