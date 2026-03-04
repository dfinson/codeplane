"""Type definitions for the ranking system.

GateLabel : OK / UNSAT / BROAD / AMBIG classification.
RankingResult : Ranked DefFact list + predicted cutoff + gate label.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GateLabel(StrEnum):
    """Gate classification for a (query, repo) pair."""

    OK = "OK"
    UNSAT = "UNSAT"
    BROAD = "BROAD"
    AMBIG = "AMBIG"


@dataclass(frozen=True)
class ScoredCandidate:
    """A DefFact candidate with a ranker score."""

    def_uid: str
    path: str
    kind: str
    name: str
    start_line: int
    end_line: int
    score: float


@dataclass(frozen=True)
class RankingResult:
    """Output of the full ranking pipeline."""

    gate_label: GateLabel
    candidates: list[ScoredCandidate]
    predicted_n: int | None
    """Predicted cutoff N (None when gate is non-OK)."""
