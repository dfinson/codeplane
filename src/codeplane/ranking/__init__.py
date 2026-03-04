"""Ranking system runtime inference.

Loads serialized LightGBM models (ranker, cutoff, gate) and scores
candidate DefFacts from raw retrieval signals.  Ships as package data
with codeplane — model artifacts live in ``ranking/data/``.

Public API
----------
rank_candidates : Score and cut a raw-signal candidate pool.
classify_gate   : Classify a (query, repo) pair before ranking.
"""

from __future__ import annotations
