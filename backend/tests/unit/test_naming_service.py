"""Tests for NamingService — title, branch, and worktree name generation."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from backend.services.naming_service import NamingService, _fallback_title


class FakeBackend:
    """Controllable fake LLM backend."""

    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def complete(self, prompt: str) -> str:
        if self._index >= len(self._responses):
            return ""
        response = self._responses[self._index]
        self._index += 1
        if isinstance(response, Exception):
            raise response
        return response


def _json(title: str, branch: str = "feat/add-feature", worktree: str = "add-feature") -> str:
    return json.dumps({"title": title, "branch_name": branch, "worktree_name": worktree})


class TestNamingServiceHappyPath:
    @pytest.mark.asyncio
    async def test_generates_all_fields(self):
        backend = FakeBackend([_json("Add user search", "feat/add-user-search", "add-user-search")])
        svc = NamingService(backend)
        title, branch, worktree = await svc.generate("Add a user search feature")
        assert title == "Add user search"
        assert branch == "feat/add-user-search"
        assert worktree == "add-user-search"

    @pytest.mark.asyncio
    async def test_strips_trailing_period_from_title(self):
        backend = FakeBackend([_json("Fix login bug.", "fix/fix-login-bug", "fix-login-bug")])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Fix the login bug")
        assert not title.endswith(".")


class TestNamingServicePartialFailure:
    """LLM returns valid title but invalid branch or worktree — the title must be preserved."""

    @pytest.mark.asyncio
    async def test_preserves_llm_title_when_branch_invalid(self):
        """A bad branch name must NOT cause the title to fall back to the raw prompt."""
        raw = json.dumps({
            "title": "Refactor auth module",
            "branch_name": "INVALID BRANCH!!",  # will fail _sanitize_branch
            "worktree_name": "auth-refactor",
        })
        backend = FakeBackend([raw])
        svc = NamingService(backend)
        title, branch, worktree = await svc.generate("Refactor the authentication module")
        assert title == "Refactor auth module", f"Expected LLM title, got: {title!r}"
        # Branch should be a deterministic fallback, not a garbage value
        assert branch.startswith("chore/task-")
        assert worktree == "auth-refactor"

    @pytest.mark.asyncio
    async def test_preserves_llm_title_when_worktree_invalid(self):
        raw = json.dumps({
            "title": "Add search API",
            "branch_name": "feat/add-search-api",
            "worktree_name": "!!",  # sanitizes to "" — fails _sanitize_worktree
        })
        backend = FakeBackend([raw])
        svc = NamingService(backend)
        title, branch, worktree = await svc.generate("Add a search API endpoint")
        assert title == "Add search API", f"Expected LLM title, got: {title!r}"
        assert branch == "feat/add-search-api"
        assert worktree.startswith("task-")

    @pytest.mark.asyncio
    async def test_preserves_llm_title_when_both_branch_and_worktree_invalid(self):
        raw = json.dumps({
            "title": "Upgrade dependencies",
            "branch_name": "bad branch",   # no type/ prefix — fails _sanitize_branch
            "worktree_name": "!!",          # sanitizes to "" — fails _sanitize_worktree
        })
        backend = FakeBackend([raw])
        svc = NamingService(backend)
        title, branch, worktree = await svc.generate("Upgrade all dependencies to latest")
        assert title == "Upgrade dependencies"
        assert branch.startswith("chore/task-")
        assert worktree.startswith("task-")


class TestNamingServiceTitleRetry:
    """When the LLM returns an unusable title, it should retry rather than fall back to the raw prompt."""

    @pytest.mark.asyncio
    async def test_retries_title_when_initial_title_invalid(self):
        # First response: good branch/worktree but missing/invalid title
        first = json.dumps({
            "title": "",  # empty — fails _sanitize_title
            "branch_name": "feat/add-feature",
            "worktree_name": "add-feature",
        })
        # Second response: retry prompt returns a good title
        second = json.dumps({"title": "Add new feature"})
        backend = FakeBackend([first, second])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Add a new feature to the app")
        assert title == "Add new feature"

    @pytest.mark.asyncio
    async def test_uses_fallback_title_only_when_retry_also_fails(self):
        first = json.dumps({
            "title": "",
            "branch_name": "feat/add-feature",
            "worktree_name": "add-feature",
        })
        # Retry also returns garbage
        second = "{}"
        backend = FakeBackend([first, second])
        svc = NamingService(backend)
        prompt = "Add a new feature to the app"
        title, _, _ = await svc.generate(prompt)
        # Should fall back to truncated prompt — but only as a last resort
        assert title == _fallback_title(prompt)


class TestNamingServiceFullFailure:
    """LLM completely unavailable — should use deterministic fallbacks."""

    @pytest.mark.asyncio
    async def test_full_exception_uses_fallback_names(self):
        backend = FakeBackend([RuntimeError("LLM unavailable")])
        svc = NamingService(backend)
        prompt = "Fix the critical null pointer bug in production"
        title, branch, worktree = await svc.generate(prompt)
        assert branch.startswith("chore/task-")
        assert worktree.startswith("task-")
        # Fallback title should be the truncated prompt (last resort)
        assert title == _fallback_title(prompt)

    @pytest.mark.asyncio
    async def test_empty_response_uses_fallback_names(self):
        backend = FakeBackend([""])
        svc = NamingService(backend)
        _, branch, worktree = await svc.generate("Some task")
        assert branch.startswith("chore/task-")
        assert worktree.startswith("task-")


class TestNamingServiceConflictResolution:
    @pytest.mark.asyncio
    async def test_regenerates_branch_on_conflict(self):
        initial = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        retry = json.dumps({"branch_name": "fix/login-bug-v2"})
        backend = FakeBackend([initial, retry])
        svc = NamingService(backend)
        _, branch, _ = await svc.generate(
            "Fix login", existing_branches={"fix/fix-login-bug"}
        )
        assert branch == "fix/login-bug-v2"

    @pytest.mark.asyncio
    async def test_regenerates_worktree_on_conflict(self):
        initial = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        retry = json.dumps({"worktree_name": "fix-login-v2"})
        backend = FakeBackend([initial, retry])
        svc = NamingService(backend)
        _, _, worktree = await svc.generate(
            "Fix login", existing_worktrees={"fix-login-bug"}
        )
        assert worktree == "fix-login-v2"
