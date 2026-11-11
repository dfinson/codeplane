"""Read-only parser for spec-kit ``tasks.md`` (Story 4.2, CAP-9/AD-9).

The parser is pure, stateless functions: given a repo path, they read
whatever source files exist (tolerating their absence) and return a list of
:class:`ParsedTask` describing the tasks found. Nothing here ever writes to
the source repo, and nothing here resolves cross-repo ``depends_on``
references — that composite-key resolution is owned by
``RecipeService.ingest_project``, since a parser only knows about the one
repo it was pointed at.

Dependency reference convention (documented, not upstream-standardized):

* A bare id (e.g. ``4-1-widen-the-task-recipe-vocabulary`` or ``T001``) means
  "the task with this id in the same repo".
* An id containing a ``/`` (e.g. ``codeplane-frontend/2-1-create-edit-a-project``)
  means "the task with this id in the sibling member repo whose folder name
  is the part before the ``/``".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SPEC_KIT_TASK_RE = re.compile(r"^-\s*\[.\]\s*(?P<task_id>T\d+)\b\s*(?P<rest>.*)$")
_SPEC_KIT_DEPENDS_RE = re.compile(r"depends on:?\s*([\w./,\s-]+?)(?:\)|$)", re.IGNORECASE)


@dataclass
class ParsedTask:
    """A single task/story node parsed from a source repo, pre-cross-repo-resolution.

    ``depends_on`` entries are raw ids as written in the source file — either
    bare (same-repo) or ``sibling-repo-folder/id`` (cross-repo) — not yet the
    composite ``repo_path::story_node_id`` keys ``RecipeService`` stores.
    """

    story_node_id: str
    depends_on: list[str] = field(default_factory=list)
    epic_id: str | None = None


def _split_dependency_ids(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", raw) if part.strip()]




def _find_tasks_md_files(repo_path: str) -> list[Path]:
    root = Path(repo_path)
    found: list[Path] = []
    root_tasks = root / "tasks.md"
    if root_tasks.is_file():
        found.append(root_tasks)
    specs_dir = root / "specs"
    if specs_dir.is_dir():
        found.extend(sorted(specs_dir.glob("**/tasks.md")))
    return found


def parse_spec_kit_tasks(repo_path: str) -> list[ParsedTask]:
    """Parse spec-kit ``tasks.md`` file(s) in ``repo_path`` (root and/or ``specs/**``).

    ``story_node_id`` is the leading ``T\\d+`` task id on each checkbox line.
    ``depends_on`` is read from a ``depends on: ...`` annotation on the same
    line (comma/space separated ids); absent when no such annotation exists.
    spec-kit tasks never have Epic membership, so ``epic_id`` is always null
    for tasks sourced this way — never guessed.

    Tolerates the total absence of any ``tasks.md`` by returning an empty
    list rather than raising.
    """
    tasks: list[ParsedTask] = []
    for tasks_file in _find_tasks_md_files(repo_path):
        text = tasks_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            match = _SPEC_KIT_TASK_RE.match(line)
            if not match:
                continue
            task_id = match.group("task_id")
            rest = match.group("rest")
            depends_on: list[str] = []
            depends_match = _SPEC_KIT_DEPENDS_RE.search(rest)
            if depends_match:
                depends_on = _split_dependency_ids(depends_match.group(1))
            tasks.append(ParsedTask(story_node_id=task_id, depends_on=depends_on, epic_id=None))
    return tasks
