"""Tests for ArtifactService — storage, collection, retrieval."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.models.api_schemas import (
    ArtifactType,
    DiffFileModel,
    DiffFileStatus,
    ExecutionPhase,
)
from backend.models.domain import Artifact
from backend.services.artifact_service import ArtifactService, _guess_mime


@pytest.fixture
def mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.create = AsyncMock(side_effect=lambda a: a)
    repo.list_for_job = AsyncMock(return_value=[])
    repo.get = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def artifact_service(mock_repo: AsyncMock) -> ArtifactService:
    return ArtifactService(mock_repo)


class TestStoreDiffSnapshot:
    @pytest.mark.asyncio
    async def test_stores_diff_snapshot_to_disk(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            diff_file = DiffFileModel(
                path="test.py",
                status=DiffFileStatus.modified,
                additions=1,
                deletions=0,
                hunks=[],
            )
            artifact = await artifact_service.store_diff_snapshot("job-1", [diff_file])
            assert artifact.job_id == "job-1"
            assert artifact.type == ArtifactType.diff_snapshot
            assert artifact.name == "diff-snapshot.json"
            # Verify disk file exists
            disk_path = Path(artifact.disk_path)
            assert disk_path.exists()
            content = json.loads(disk_path.read_text())
            assert isinstance(content, list)
            assert len(content) == 1
        finally:
            mod._ARTIFACTS_BASE = orig_base


class TestCollectFromWorkspace:
    @pytest.mark.asyncio
    async def test_collects_artifacts_from_workspace(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            # Create a .codeplane/artifacts/ directory with a file
            artifacts_dir = tmp_path / ".codeplane" / "artifacts"
            artifacts_dir.mkdir(parents=True)
            (artifacts_dir / "report.json").write_text('{"ok": true}')

            result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
            assert len(result) == 1
            assert result[0].name == "report.json"
            assert result[0].type == ArtifactType.custom
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_skips_symlinks(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            artifacts_dir = tmp_path / ".codeplane" / "artifacts"
            artifacts_dir.mkdir(parents=True)
            target = tmp_path / "secret.txt"
            target.write_text("secret")
            (artifacts_dir / "link.txt").symlink_to(target)

            result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
            assert len(result) == 0
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_skips_large_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            artifacts_dir = tmp_path / ".codeplane" / "artifacts"
            artifacts_dir.mkdir(parents=True)
            big_file = artifacts_dir / "huge.bin"
            # Write > 50 MB (just create a sparse-ish file by writing enough)
            big_file.write_bytes(b"\0" * (50 * 1024 * 1024 + 1))

            result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
            assert len(result) == 0
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_no_artifacts_dir_returns_empty(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
        assert result == []


class TestCollectFromSessionStorage:
    @pytest.mark.asyncio
    async def test_collects_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            session_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Plan\nDo things")
            (session_dir / "notes.md").write_text("# Notes")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert len(result) == 2
            names = {a.name for a in result}
            assert names == {"plan.md", "notes.md"}
            assert all(a.type == ArtifactType.document for a in result)
            assert all(a.mime_type == "text/markdown" for a in result)
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_ignores_non_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            session_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Plan")
            (session_dir / "data.json").write_text('{"x": 1}')
            (session_dir / "notes.txt").write_text("notes")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert len(result) == 1
            assert result[0].name == "plan.md"
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_ignores_subdirectory_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            (session_dir / "checkpoints").mkdir(parents=True)
            (session_dir / "checkpoints" / "index.md").write_text("# Index")
            (session_dir / "plan.md").write_text("# Plan")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert len(result) == 1
            assert result[0].name == "plan.md"
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_skips_symlinks(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            session_dir.mkdir(parents=True)
            target = tmp_path / "secret.md"
            target.write_text("# Secret")
            (session_dir / "link.md").symlink_to(target)

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert result == []
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_missing_session_dir_returns_empty(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        result = await artifact_service.collect_from_session_storage(
            "job-1", "nonexistent-session", config_dir=tmp_path / ".copilot"
        )
        assert result == []


class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_for_job(self, artifact_service: ArtifactService, mock_repo: AsyncMock) -> None:
        mock_repo.list_for_job.return_value = [
            Artifact(
                id="art-1",
                job_id="job-1",
                name="test.json",
                type=ArtifactType.custom,
                mime_type="application/json",
                size_bytes=100,
                disk_path="/tmp/art-1.json",
                phase=ExecutionPhase.post_completion,
                created_at=datetime.now(UTC),
            )
        ]
        result = await artifact_service.list_for_job("job-1")
        assert len(result) == 1
        assert result[0].id == "art-1"

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, artifact_service: ArtifactService) -> None:
        result = await artifact_service.get("nonexistent")
        assert result is None


class TestGuessMime:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("report.json", "application/json"),
            ("readme.md", "text/markdown"),
            ("log.txt", "text/plain"),
            ("data.csv", "text/csv"),
            ("image.png", "image/png"),
            ("photo.jpg", "image/jpeg"),
            ("unknown.xyz", "application/octet-stream"),
            ("config.yaml", "text/yaml"),
            ("config.yml", "text/yaml"),
        ],
    )
    def test_mime_guessing(self, filename: str, expected: str) -> None:
        assert _guess_mime(filename) == expected
