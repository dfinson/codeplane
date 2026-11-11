"""Tests for spec-kit tasks.md parsers (Story 4.2, CAP-9/AD-9)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.services.recipe.parsers import parse_spec_kit_tasks

if TYPE_CHECKING:
    from pathlib import Path




class TestParseSpecKitTasks:
    def test_missing_tasks_md_returns_empty(self, tmp_path: Path) -> None:
        assert parse_spec_kit_tasks(str(tmp_path)) == []

    def test_parses_root_tasks_md(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.md").write_text(
            "- [ ] T001 Setup project structure\n"
            "- [ ] T002 Implement model (depends on: T001)\n"
            "- [x] T003 Done task, depends on T001, T002\n",
            encoding="utf-8",
        )

        tasks = parse_spec_kit_tasks(str(tmp_path))

        by_id = {t.story_node_id: t for t in tasks}
        assert set(by_id) == {"T001", "T002", "T003"}
        assert by_id["T001"].depends_on == []
        assert by_id["T001"].epic_id is None
        assert by_id["T002"].depends_on == ["T001"]
        assert by_id["T003"].depends_on == ["T001", "T002"]

    def test_parses_cross_repo_dependency_reference(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.md").write_text(
            "- [ ] T010 Frontend task (depends on: codeplane-backend/T002)\n",
            encoding="utf-8",
        )

        tasks = parse_spec_kit_tasks(str(tmp_path))

        assert tasks[0].depends_on == ["codeplane-backend/T002"]

    def test_parses_nested_specs_tasks_md(self, tmp_path: Path) -> None:
        nested = tmp_path / "specs" / "001-feature" / "tasks.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("- [ ] T001 A task\n", encoding="utf-8")

        tasks = parse_spec_kit_tasks(str(tmp_path))

        assert len(tasks) == 1
        assert tasks[0].story_node_id == "T001"

    def test_non_task_lines_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "tasks.md").write_text(
            "# Tasks\n\nSome prose that is not a checkbox line.\n- [ ] T001 Real task\n",
            encoding="utf-8",
        )

        tasks = parse_spec_kit_tasks(str(tmp_path))

        assert len(tasks) == 1
        assert tasks[0].story_node_id == "T001"
