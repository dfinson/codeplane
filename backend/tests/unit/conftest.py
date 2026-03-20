"""Shared test fixtures for unit tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.models.domain import Job


def make_job(**overrides: Any) -> Job:
    """Create a Job domain object with reasonable defaults.

    Any field can be overridden via keyword arguments.
    """
    now = datetime.now(UTC)
    defaults: dict[str, Any] = {
        "id": "job-1",
        "repo": "/repos/test",
        "prompt": "Fix the bug",
        "state": "running",
        "base_ref": "main",
        "branch": "fix/bug",
        "worktree_path": None,
        "session_id": None,
        "created_at": now,
        "updated_at": now,
    }
    defaults.update(overrides)
    return Job(**defaults)
