"""Centralized error mapping for pygit2 exceptions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager

import pygit2

from codeplane.git.errors import AuthenticationError, GitError, RemoteError


class ErrorMapper:
    """Maps pygit2 exceptions to domain errors."""

    @staticmethod
    @contextmanager
    def guard(operation: str, *, remote: str | None = None) -> Iterator[None]:
        """Context manager for consistent exception translation."""
        try:
            yield
        except pygit2.GitError as e:
            msg = str(e).lower()
            if ("authentication" in msg or "credential" in msg) and remote:
                raise AuthenticationError(remote, operation) from e
            if remote:
                raise RemoteError(remote, str(e)) from e
            raise GitError(f"{operation} failed: {e}") from e


def git_operation(operation: str, *, remote: str | None = None) -> AbstractContextManager[None]:
    """Decorator for consistent exception translation."""
    return ErrorMapper.guard(operation, remote=remote)
