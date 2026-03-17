"""Pre-work naming service — generates intelligent job titles, branch names, and worktree names.

Naming is blocking: a job cannot start without valid, conflict-free names.
All names are LLM-generated (cheap fast model). No SQLite increment fallback.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

import structlog

log = structlog.get_logger()


@runtime_checkable
class Completable(Protocol):
    """Anything with an async complete(prompt) → str method."""

    async def complete(self, prompt: str) -> str: ...


_NAMING_PROMPT = """\
You are a naming assistant for a coding task manager. Given a task description,
generate a concise title, a conventional git branch name, and a short worktree name.

Rules:
- **title**: 3-8 words, sentence case, no trailing period. Captures the essence of the task.
- **branch_name**: lowercase, kebab-case, prefixed with a conventional type:
  - `feat/` for new features or additions
  - `fix/` for bug fixes
  - `chore/` for maintenance, upgrades, refactoring
  - `docs/` for documentation changes
  - `test/` for test additions/fixes
  - Maximum 50 characters total. Use only `a-z`, `0-9`, `-`, `/`.
- **worktree_name**: lowercase, kebab-case, 3-30 characters, descriptive slug.
  - Use only `a-z`, `0-9`, `-`. No slashes.
  - Example: "auth-refactor", "fix-login-bug", "add-search-api"

Respond with ONLY a JSON object, no markdown fencing, no explanation:
{"title": "...", "branch_name": "...", "worktree_name": "..."}

Task description:
"""

_RENAME_PROMPT = """\
The following {field} is already taken: "{conflicting_value}"

Generate a NEW unique {field} that is different but still describes this task.
Follow the same rules as before.

Respond with ONLY a JSON object with the field that needs to change:
{{"{field}": "..."}}

Task description:
{prompt}
"""

# Validates branch names: type/slug with allowed characters
_BRANCH_RE = re.compile(r"^(feat|fix|chore|docs|test)/[a-z0-9][a-z0-9-]{1,43}$")
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


def _fallback_title(prompt: str) -> str:
    """Create a simple title by truncating the prompt."""
    clean = prompt.strip().replace("\n", " ")
    return clean[:57] + "..." if len(clean) > 60 else clean


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

        Args:
            prompt: The task description.
            existing_branches: Set of branch names that already exist (local + remote).
            existing_worktrees: Set of worktree directory names that already exist.

        Returns:
            Tuple of (title, branch_name, worktree_name).

        The function re-prompts the LLM for conflicting fields, up to MAX_RETRIES times.
        """
        branches = existing_branches or set()
        worktrees = existing_worktrees or set()

        # Initial generation
        title, branch, worktree = await self._initial_generate(prompt)

        # Validate and re-prompt for conflicts
        for _attempt in range(self.MAX_RETRIES):
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

    async def _initial_generate(self, prompt: str) -> tuple[str, str, str]:
        """Generate initial title, branch, and worktree from prompt."""
        try:
            raw = await self._backend.complete(_NAMING_PROMPT + prompt)
            if not raw:
                log.warning("naming_empty_response")
                return self._fallback(prompt)

            data = _extract_json(raw)
            if data is None:
                log.warning("naming_no_json", raw=raw[:200])
                return self._fallback(prompt)

            title = _sanitize_title(data.get("title", ""))
            branch = _sanitize_branch(data.get("branch_name", ""))
            worktree = _sanitize_worktree(data.get("worktree_name", ""))

            # Retry title generation if LLM returned an unusable title
            if title is None:
                title = await self._regenerate_title(prompt)

            # If branch or worktree failed validation, fall back only those fields
            # rather than discarding the LLM-generated title.
            if branch is None or worktree is None:
                import hashlib

                log.warning("naming_partial_failure", branch=branch, worktree=worktree)
                h = hashlib.sha256(prompt.encode()).hexdigest()[:8]
                if branch is None:
                    branch = f"chore/task-{h}"
                if worktree is None:
                    worktree = f"task-{h}"

            return title, branch, worktree

        except Exception:
            log.warning("naming_generation_failed", exc_info=True)
            return self._fallback(prompt)

    async def _regenerate_title(self, prompt: str) -> str:
        """Ask the LLM for a title on its own when the initial response had none."""
        _TITLE_ONLY_PROMPT = (
            "You are a naming assistant for a coding task manager.\n"
            "Generate a concise title for the following task: 3-8 words, sentence case, no trailing period.\n"
            'Respond with ONLY a JSON object: {"title": "..."}\n\n'
            "Task description:\n"
        )
        try:
            raw = await self._backend.complete(_TITLE_ONLY_PROMPT + prompt)
            data = _extract_json(raw) if raw else None
            if data:
                title = _sanitize_title(data.get("title", ""))
                if title:
                    log.info("naming_title_regenerated", title=title)
                    return title
        except Exception:
            log.warning("naming_title_regeneration_failed", exc_info=True)
        return _fallback_title(prompt)

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

    @staticmethod
    def _fallback(prompt: str) -> tuple[str, str, str]:
        """Generate deterministic fallback names from the prompt."""
        import hashlib

        h = hashlib.sha256(prompt.encode()).hexdigest()[:8]
        title = _fallback_title(prompt)
        branch = f"chore/task-{h}"
        worktree = f"task-{h}"
        return title, branch, worktree
