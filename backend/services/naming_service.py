"""Pre-work naming service — generates intelligent job titles and branch names."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from backend.services.agent_adapter import AgentAdapterInterface

log = structlog.get_logger()

_NAMING_PROMPT = """\
You are a naming assistant for a coding task manager. Given a task description,
generate a concise title and a conventional git branch name.

Rules:
- **title**: 3-8 words, sentence case, no trailing period. Captures the essence of the task.
- **branch_name**: lowercase, kebab-case, prefixed with a conventional type:
  - `feat/` for new features or additions
  - `fix/` for bug fixes
  - `chore/` for maintenance, upgrades, refactoring
  - `docs/` for documentation changes
  - `test/` for test additions/fixes
  - Maximum 50 characters total. Use only `a-z`, `0-9`, `-`, `/`.

Respond with ONLY a JSON object, no markdown fencing, no explanation:
{"title": "...", "branch_name": "..."}

Task description:
"""

# Validates branch names: type/slug with allowed characters
_BRANCH_RE = re.compile(r"^(feat|fix|chore|docs|test)/[a-z0-9][a-z0-9-]{1,43}$")


def _sanitize_branch(raw: str) -> str | None:
    """Validate and sanitize a generated branch name. Returns None if invalid."""
    branch = raw.strip().lower()
    # Strip any quotes or whitespace
    branch = branch.strip("\"' ")
    # Replace underscores/spaces with hyphens
    branch = re.sub(r"[_ ]+", "-", branch)
    # Remove any disallowed characters
    branch = re.sub(r"[^a-z0-9/\-]", "", branch)
    # Collapse multiple hyphens
    branch = re.sub(r"-{2,}", "-", branch)
    # Strip trailing hyphens from slug
    if "/" in branch:
        prefix, slug = branch.split("/", 1)
        slug = slug.strip("-")
        branch = f"{prefix}/{slug}"

    if _BRANCH_RE.match(branch):
        return branch
    return None


def _sanitize_title(raw: str) -> str | None:
    """Validate and clean a generated title. Returns None if unusable."""
    title = raw.strip().strip("\"'").strip()
    # Remove trailing period
    title = title.rstrip(".")
    if not title or len(title) < 5 or len(title) > 80:
        return None
    return title


def _fallback_title(prompt: str) -> str:
    """Create a simple title by truncating the prompt."""
    clean = prompt.strip().replace("\n", " ")
    if len(clean) <= 60:
        return clean
    return clean[:57] + "..."


class NamingService:
    """Generates intelligent job titles and branch names from task prompts."""

    def __init__(self, adapter: AgentAdapterInterface) -> None:
        self._adapter = adapter

    async def generate(self, prompt: str) -> tuple[str, str | None]:
        """Generate a title and branch name from a task prompt.

        Returns:
            Tuple of (title, branch_name). branch_name may be None if
            generation fails, signaling the caller to use the default.
        """
        try:
            raw = await self._adapter.complete(_NAMING_PROMPT + prompt)
            if not raw:
                log.warning("naming_empty_response")
                return _fallback_title(prompt), None

            return self._parse_response(raw, prompt)
        except Exception:
            log.warning("naming_generation_failed", exc_info=True)
            return _fallback_title(prompt), None

    def _parse_response(self, raw: str, prompt: str) -> tuple[str, str | None]:
        """Parse the LLM response into title and branch name."""
        # Try to extract JSON from the response (handle markdown fencing)
        json_str = raw.strip()
        if "```" in json_str:
            # Extract content between fences
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", json_str, re.DOTALL)
            if match:
                json_str = match.group(1).strip()

        # Find JSON object boundaries
        start = json_str.find("{")
        end = json_str.rfind("}")
        if start == -1 or end == -1:
            log.warning("naming_no_json", raw=raw[:200])
            return _fallback_title(prompt), None

        json_str = json_str[start : end + 1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            log.warning("naming_json_parse_failed", raw=raw[:200])
            return _fallback_title(prompt), None

        title = _sanitize_title(data.get("title", ""))
        branch = _sanitize_branch(data.get("branch_name", ""))

        if title is None:
            title = _fallback_title(prompt)

        log.info("naming_generated", title=title, branch=branch)
        return title, branch
