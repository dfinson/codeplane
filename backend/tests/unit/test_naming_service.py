"""Tests for NamingService — title, branch, and worktree name generation."""

from __future__ import annotations

import json

import pytest

from backend.services.naming_service import NamingError, NamingService


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

    @property
    def call_count(self) -> int:
        return self._index


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

    @pytest.mark.asyncio
    async def test_single_llm_call_on_success(self):
        backend = FakeBackend([_json("Add user search", "feat/add-user-search", "add-user-search")])
        svc = NamingService(backend)
        await svc.generate("Add a user search feature")
        assert backend.call_count == 1


class TestNamingServiceRetry:
    """Bad LLM output triggers a full retry, not a fallback."""

    @pytest.mark.asyncio
    async def test_retries_on_invalid_output_and_succeeds(self):
        bad = json.dumps({"title": "Fix bug", "branch_name": "NO SLASH", "worktree_name": "ok-name"})
        good = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        backend = FakeBackend([bad, good])
        svc = NamingService(backend)
        title, branch, worktree = await svc.generate("Fix the login bug")
        assert title == "Fix login bug"
        assert branch == "fix/fix-login-bug"
        assert backend.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_empty_response(self):
        good = _json("Add feature", "feat/add-feature", "add-feature")
        backend = FakeBackend(["", good])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Add a feature")
        assert title == "Add feature"
        assert backend.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_on_non_json_response(self):
        good = _json("Refactor auth", "chore/refactor-auth", "refactor-auth")
        backend = FakeBackend(["I'm sorry, I can't help with that.", good])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Refactor the auth module")
        assert title == "Refactor auth"

    @pytest.mark.asyncio
    async def test_retries_on_llm_exception(self):
        good = _json("Fix bug", "fix/fix-bug", "fix-bug")
        backend = FakeBackend([RuntimeError("timeout"), good])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Fix a bug")
        assert title == "Fix bug"


class TestNamingServiceExhaustedRetries:
    """After MAX_RETRIES attempts, NamingError is raised — no fallback, ever."""

    @pytest.mark.asyncio
    async def test_raises_naming_error_after_all_retries_fail(self):
        backend = FakeBackend([RuntimeError("LLM unavailable")] * 3)
        svc = NamingService(backend)
        with pytest.raises(NamingError):
            await svc.generate("Fix the critical null pointer bug in production")

    @pytest.mark.asyncio
    async def test_raises_naming_error_on_persistent_invalid_output(self):
        # All responses have an invalid branch — should exhaust retries and raise
        bad = json.dumps({"title": "Fix bug", "branch_name": "NO SLASH", "worktree_name": "fix-bug"})
        backend = FakeBackend([bad] * 3)
        svc = NamingService(backend)
        with pytest.raises(NamingError):
            await svc.generate("Fix a bug")

    @pytest.mark.asyncio
    async def test_never_returns_truncated_description_as_title(self):
        """The truncated task description must never appear as a job title."""
        prompt = "Add a comprehensive new user search feature with autocomplete"
        backend = FakeBackend([RuntimeError("LLM unavailable")] * 3)
        svc = NamingService(backend)
        with pytest.raises(NamingError):
            await svc.generate(prompt)
        # If we get here (no exception), the title must not be a truncation of the prompt
        # This assertion is the contract: either a meaningful LLM title or an exception.


class TestNamingServiceConflictResolution:
    @pytest.mark.asyncio
    async def test_regenerates_branch_on_conflict(self):
        initial = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        retry = json.dumps({"branch_name": "fix/login-bug-v2"})
        backend = FakeBackend([initial, retry])
        svc = NamingService(backend)
        _, branch, _ = await svc.generate("Fix login", existing_branches={"fix/fix-login-bug"})
        assert branch == "fix/login-bug-v2"

    @pytest.mark.asyncio
    async def test_regenerates_worktree_on_conflict(self):
        initial = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        retry = json.dumps({"worktree_name": "fix-login-v2"})
        backend = FakeBackend([initial, retry])
        svc = NamingService(backend)
        _, _, worktree = await svc.generate("Fix login", existing_worktrees={"fix-login-bug"})
        assert worktree == "fix-login-v2"

    @pytest.mark.asyncio
    async def test_title_is_not_affected_by_conflict_resolution(self):
        """Conflict resolution for branch/worktree must not change the title."""
        initial = _json("Fix login bug", "fix/fix-login-bug", "fix-login-bug")
        retry = json.dumps({"branch_name": "fix/login-bug-v2"})
        backend = FakeBackend([initial, retry])
        svc = NamingService(backend)
        title, _, _ = await svc.generate("Fix login", existing_branches={"fix/fix-login-bug"})
        assert title == "Fix login bug"
