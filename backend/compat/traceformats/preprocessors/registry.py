# Derived from traceforge-toolkit 0.1.5. MIT license: backend/compat/traceforge.LICENSE.
"""Preprocessor registry — central registration and lookup."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

PreprocessorFn = Callable[[dict[str, Any]], list[dict[str, Any]]]


class StatefulPreprocessor(ABC):
    @abstractmethod
    def __call__(self, obj: dict[str, Any]) -> list[dict[str, Any]]: ...

    @abstractmethod
    def checkpoint(self) -> dict[str, Any]: ...

    @abstractmethod
    def restore(self, state: dict[str, Any]) -> None: ...


_PREPROCESSORS: dict[str, PreprocessorFn | type[StatefulPreprocessor]] = {}


def register_preprocessor[T: PreprocessorFn | type[StatefulPreprocessor]](name: str) -> Callable[[T], T]:
    """Register a stateless function or a decoder-owned preprocessor factory."""

    def decorator(fn: T) -> T:
        _PREPROCESSORS[name] = fn
        return fn

    return decorator


def get_preprocessor(name: str) -> PreprocessorFn | StatefulPreprocessor | None:
    """Create fresh state for each decoder, while sharing stateless functions."""
    registered = _PREPROCESSORS.get(name)
    return registered() if isinstance(registered, type) else registered
