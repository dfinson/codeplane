"""Provider-independent optional export health, separate from local acceptance."""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable


class ExportDropReason(StrEnum):
    metadata_withheld = "metadata_withheld"
    sink_failure = "sink_failure"
    queue_full = "queue_full"
    retry_exhausted = "retry_exhausted"
    authentication = "authentication"
    configuration = "configuration"
    disabled = "disabled"
    shutdown = "shutdown"


class ExportHealthSink(Protocol):
    def record_drop(self, reason: ExportDropReason, count: int = 1, *, disable: bool = False) -> None: ...


@dataclass(frozen=True, slots=True)
class ExportHealthSnapshot:
    enabled: bool
    last_reason: ExportDropReason | None
    dropped_spans: int
    dropped_by_reason: tuple[tuple[str, int], ...]
    queued_spans: int = 0
    exported_spans: int = 0


class ExportHealth:
    def __init__(self, *, enabled: bool = False) -> None:
        self._lock = threading.Lock()
        self._enabled = enabled
        self._last_reason: ExportDropReason | None = None
        self._dropped: Counter[str] = Counter()
        self._queue_probe: Callable[[], int] = lambda: 0
        self._exported = 0

    def set_queue_probe(self, probe: Callable[[], int]) -> None:
        self._queue_probe = probe

    def record_exported(self, count: int) -> None:
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Invalid exported-span count")
        with self._lock:
            self._exported += count

    def record_drop(self, reason: ExportDropReason, count: int = 1, *, disable: bool = False) -> None:
        if (
            not isinstance(reason, ExportDropReason)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ValueError("Invalid optional-export health update")
        with self._lock:
            self._dropped[reason.value] += count
            self._last_reason = reason
            if disable:
                self._enabled = False

    def snapshot(self) -> ExportHealthSnapshot:
        with self._lock:
            return ExportHealthSnapshot(
                self._enabled,
                self._last_reason,
                sum(self._dropped.values()),
                tuple(sorted(self._dropped.items())),
                self._queue_probe(),
                self._exported,
            )
