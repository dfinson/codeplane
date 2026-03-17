"""Pre-work naming service — generates intelligent job titles, branch names, and worktree names.

Naming is blocking: a job cannot start without valid, conflict-free names.
All names are LLM-generated (cheap fast model). No fallbacks — if the LLM
fails to produce valid names after MAX_RETRIES attempts, NamingError is raised.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger()


class NamingError(Exception):
    """Raised when the LLM fails to produce valid names after all retries."""


@runtime_checkable
class Completable(Protocol):
    """Anything with an async complete(prompt) → str method."""

    async def complete(self, prompt: str) -> str: ...


_NAMING_PROMPT = """\
You are a naming assistant for a coding task manager. Given a task description, \
output exactly one JSON object with three fields.

Field rules:
- "title": 3-8 words, sentence case, no trailing period. Example: "Add user search feature"
- "branch_name": must start with one of these prefixes followed by a slash, then a kebab-case slug.
  Prefixes: feat/ fix/ chore/ docs/ test/
  Slug: only lowercase a-z, digits 0-9, and hyphens. No underscores, no spaces.
  Total length 50 characters max.
  Good examples: "feat/add-user-search", "fix/null-pointer-login", "chore/upgrade-deps"
  Bad examples: "add-user-search" (missing prefix), "feat/Add User Search" (uppercase/spaces)
- "worktree_name": kebab-case slug only, no prefix, no slashes. 3-30 characters.
  Only lowercase a-z, digits 0-9, and hyphens.
  Good examples: "add-user-search", "fix-login-bug", "upgrade-deps"

Output format — respond with ONLY this JSON, no markdown, no explanation:
{"title": "Fix null pointer in login", "branch_name": "fix/null-pointer-login", "worktree_name": "null-pointer-login"}

Task description:
"""

_RENAME_PROMPT = """\
The {field} "{conflicting_value}" is already in use. Generate a different one that \
still describes the task below.

Rules for {field}:
- If "branch_name": prefix/kebab-slug (e.g. feat/add-search-v2). Only a-z, 0-9, hyphens, one slash. Max 50 chars.
- If "worktree_name": kebab-slug only, no slash (e.g. add-search-v2). Only a-z, 0-9, hyphens. 3-30 chars.

Respond with ONLY a JSON object containing just the changed field:
{{"branch_name": "feat/add-search-v2"}} or {{"worktree_name": "add-search-v2"}}

Task description:
{prompt}
"""

# Validates branch names: type/slug with allowed characters
_BRANCH_RE = re.compile(r"^(feat|fix|chore|docs|test)/[a-z0-9][a-z0-9-]{0,43}$")
_WORKTREE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,28}[a-z0-9]$")


def _sanitize_branch(raw: str) -> str | None:
    """Validate and sanitize a generated branch name. Returns None if invalid."""
    branch = raw.strip().lower().strip("\"' ")
    branch = re.sub(r"[_ ]+", "-", branch)
    branch = re.sub(r"[^a-z0-9/\-]", "", branch)
    branch = re.sub(r"-{2,}", "-", branch)
    if "/" in branch:
        prefix, slug = branch.split("/", 1)
        slug = slug.strip("-")
        branch = f"{prefix}/{slug}"
    if _BRANCH_RE.match(branch):
        return branch
    return None


def _sanitize_worktree(raw: str) -> str | None:
    """Validate and sanitize a generated worktree name. Returns None if invalid."""
    name = raw.strip().lower().strip("\"' ")
    name = re.sub(r"[_ /]+", "-", name)
    name = re.sub(r"[^a-z0-9-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.strip("-")
    if _WORKTREE_RE.match(name):
        return name
    return None


def _sanitize_title(raw: str) -> str | None:
    """Validate and clean a generated title. Returns None if unusable."""
    title = raw.strip().strip("\"'").strip().rstrip(".")
    if not title or len(title) < 5 or len(title) > 80:
        return None
    return title


def _extract_json(raw: str) -> dict[str, Any] | None:
    """Extract JSON object from LLM response, handling markdown fencing."""
    json_str = raw.strip()
    if "```" in json_str:
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_str, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
    start = json_str.find("{")
    end = json_str.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        result: dict[str, Any] = json.loads(json_str[start : end + 1])
        return result
    except json.JSONDecodeError:
        return None


class NamingService:
    """Generates intelligent job titles, branch names, and worktree names.

    Naming is blocking: validates against existing branches/worktrees and
    re-prompts the LLM on conflict until valid names are produced.

    There are no fallbacks. If the LLM fails to produce valid names after
    MAX_RETRIES attempts, NamingError is raised and job creation fails.
    """

    MAX_RETRIES = 3

    def __init__(self, backend: Completable) -> None:
        self._backend = backend

    async def generate(
        self,
        prompt: str,
        *,
        existing_branches: set[str] | None = None,
        existing_worktrees: set[str] | None = None,
    ) -> tuple[str, str, str]:
        """Generate a title, branch name, and worktree name.

        Retries the full LLM call up to MAX_RETRIES times if the response is
        invalid or unparseable. Raises NamingError if all attempts fail.

        Args:
            prompt: The task description.
            existing_branches: Set of branch names that already exist (local + remote).
            existing_worktrees: Set of worktree directory names that already exist.

        Returns:
            Tuple of (title, branch_name, worktree_name).
        """
        branches = existing_branches or set()
        worktrees = existing_worktrees or set()

        # Retry the full generation until the LLM produces valid output.
        last_error: Exception = NamingError("No attempts made")
        for attempt in range(self.MAX_RETRIES):
            try:
                title, branch, worktree = await self._attempt_generate(prompt)
            except Exception as exc:
                log.warning("naming_attempt_failed", attempt=attempt + 1, reason=str(exc))
                last_error = exc
                continue

            # Re-prompt for any names that conflict with existing ones.
            for _ in range(self.MAX_RETRIES):
                branch_conflict = branch in branches
                worktree_conflict = worktree in worktrees
                if not branch_conflict and not worktree_conflict:
                    break
                if branch_conflict:
                    new_branch = await self._regenerate_field("branch_name", branch, prompt)
                    if new_branch and new_branch not in branches:
                        branch = new_branch
                        log.info("naming_branch_regenerated", new_branch=branch)
                if worktree_conflict:
                    new_worktree = await self._regenerate_field("worktree_name", worktree, prompt)
                    if new_worktree and new_worktree not in worktrees:
                        worktree = new_worktree
                        log.info("naming_worktree_regenerated", new_worktree=worktree)

            log.info("naming_generated", title=title, branch=branch, worktree=worktree)
            return title, branch, worktree

        raise NamingError(
            f"Failed to generate valid names after {self.MAX_RETRIES} attempts"
        ) from last_error

    async def _attempt_generate(self, prompt: str) -> tuple[str, str, str]:
        """Single LLM call to produce title, branch, and worktree. Raises on any invalid output."""
        raw = await self._backend.complete(_NAMING_PROMPT + prompt)
        if not raw:
            raise NamingError("Empty response from LLM")

        data = _extract_json(raw)
        if data is None:
            raise NamingError(f"No valid JSON in LLM response: {raw[:200]!r}")

        title = _sanitize_title(data.get("title", ""))
        branch = _sanitize_branch(data.get("branch_name", ""))
        worktree = _sanitize_worktree(data.get("worktree_name", ""))

        invalid = [f for f, v in [("title", title), ("branch", branch), ("worktree", worktree)] if v is None]
        if invalid:
            raise NamingError(
                f"LLM returned invalid values for: {', '.join(invalid)} "
                f"(raw title={data.get('title')!r}, branch={data.get('branch_name')!r}, "
                f"worktree={data.get('worktree_name')!r})"
            )

        return title, branch, worktree  # type: ignore[return-value]

    async def _regenerate_field(self, field: str, conflicting_value: str, prompt: str) -> str | None:
        """Re-prompt the LLM for a single conflicting field."""
        try:
            rename_prompt = _RENAME_PROMPT.format(
                field=field,
                conflicting_value=conflicting_value,
                prompt=prompt[:500],
            )
            raw = await self._backend.complete(rename_prompt)
            data = _extract_json(raw)
            if data is None:
                return None

            value = data.get(field, "")
            if field == "branch_name":
                return _sanitize_branch(value)
            if field == "worktree_name":
                return _sanitize_worktree(value)
            return None
        except Exception:
            log.warning("naming_regenerate_failed", field=field, exc_info=True)
            return None

