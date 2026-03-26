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


def _make_artifact(
    *,
    artifact_id: str,
    job_id: str,
    name: str,
    art_type: ArtifactType,
    disk_path: str,
) -> Artifact:
    return Artifact(
        id=artifact_id,
        job_id=job_id,
        name=name,
        type=art_type,
        mime_type="application/json",
        size_bytes=10,
        disk_path=disk_path,
        phase=ExecutionPhase.post_completion,
        created_at=datetime.now(UTC),
    )


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
    async def test_ignores_other_subdirectory_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """checkpoints/ and other subdirs (except files/) must be skipped."""
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
    async def test_collects_files_subdir_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """files/ subdirectory is explicitly scanned; names are prefixed with 'files/'."""
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            files_dir = session_dir / "files"
            files_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Plan")
            (files_dir / "dummy.md").write_text("# Dummy")
            (files_dir / "notes.md").write_text("# Notes")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert len(result) == 3
            names = {a.name for a in result}
            assert names == {"plan.md", "files/dummy.md", "files/notes.md"}
            assert all(a.type == ArtifactType.document for a in result)
        finally:
            mod._ARTIFACTS_BASE = orig_base

    @pytest.mark.asyncio
    async def test_files_subdir_name_collision(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """Same filename in top-level and files/ produces two distinct artifacts."""
        import backend.services.artifact_service as mod

        orig_base = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            files_dir = session_dir / "files"
            files_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Top-level plan")
            (files_dir / "plan.md").write_text("# Files plan")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert len(result) == 2
            names = {a.name for a in result}
            assert names == {"plan.md", "files/plan.md"}
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


class TestStoreTelemetryReportUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            result = await artifact_service.store_telemetry_report("job-1", {"tokens": 100}, slug="my-job")
            assert result.type == ArtifactType.telemetry_report
            mock_repo.create.assert_called_once()
            mock_repo.update_size_bytes.assert_not_called()
        finally:
            mod._ARTIFACTS_BASE = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            # Create the file that the existing artifact points to
            disk_file = tmp_path / "art-existing-telemetry.json"
            disk_file.write_text('{"tokens": 100}')
            existing = _make_artifact(
                artifact_id="art-existing",
                job_id="job-1",
                name="my-job-telemetry.json",
                art_type=ArtifactType.telemetry_report,
                disk_path=str(disk_file),
            )
            mock_repo.list_for_job.return_value = [existing]

            result = await artifact_service.store_telemetry_report("job-1", {"tokens": 200}, slug="my-job")

            # Should NOT create a new artifact
            mock_repo.create.assert_not_called()
            # Should update the existing one
            mock_repo.update_size_bytes.assert_called_once_with("art-existing", disk_file.stat().st_size)
            # Disk file should contain updated content
            assert json.loads(disk_file.read_text())["tokens"] == 200
            assert result.id == "art-existing"
        finally:
            mod._ARTIFACTS_BASE = orig


class TestStoreLogArtifactUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            events = [{"session_number": 1, "seq": 0, "level": "info", "message": "hello", "timestamp": "t1"}]
            result = await artifact_service.store_log_artifact("job-1", events, slug="my-job")
            assert result.type == ArtifactType.document
            assert result.name.endswith("agent.log")
            mock_repo.create.assert_called_once()
            mock_repo.update_size_bytes.assert_not_called()
        finally:
            mod._ARTIFACTS_BASE = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            disk_file = tmp_path / "art-existing-agent.log"
            disk_file.write_text("old log content")
            existing = _make_artifact(
                artifact_id="art-log",
                job_id="job-1",
                name="my-job-agent.log",
                art_type=ArtifactType.document,
                disk_path=str(disk_file),
            )
            mock_repo.list_for_job.return_value = [existing]

            events = [
                {"session_number": 2, "seq": 0, "level": "info", "message": "session 2 started", "timestamp": "t2"}
            ]
            result = await artifact_service.store_log_artifact("job-1", events, slug="my-job")

            mock_repo.create.assert_not_called()
            mock_repo.update_size_bytes.assert_called_once()
            assert "session 2 started" in disk_file.read_text()
            assert result.id == "art-log"
        finally:
            mod._ARTIFACTS_BASE = orig


class TestStoreAgentPlanUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            steps = [{"step": "run tests", "status": "done"}]
            result = await artifact_service.store_agent_plan("job-1", steps, slug="my-job")
            assert result is not None
            assert result.type == ArtifactType.agent_plan
            mock_repo.create.assert_called_once()
        finally:
            mod._ARTIFACTS_BASE = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            disk_file = tmp_path / "art-existing-plan.json"
            disk_file.write_text('{"steps": []}')
            existing = _make_artifact(
                artifact_id="art-plan",
                job_id="job-1",
                name="my-job-plan.json",
                art_type=ArtifactType.agent_plan,
                disk_path=str(disk_file),
            )
            mock_repo.list_for_job.return_value = [existing]

            new_steps = [{"step": "deploy", "status": "pending"}]
            result = await artifact_service.store_agent_plan("job-1", new_steps, slug="my-job")

            mock_repo.create.assert_not_called()
            mock_repo.update_size_bytes.assert_called_once()
            on_disk = json.loads(disk_file.read_text())
            assert on_disk["steps"][0]["step"] == "deploy"
            assert result is not None
            assert result.id == "art-plan"
        finally:
            mod._ARTIFACTS_BASE = orig


class TestStoreApprovalHistoryUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            approvals = [
                {
                    "id": "appr-1",
                    "description": "ok",
                    "proposed_action": "x",
                    "requested_at": "t1",
                    "resolved_at": None,
                    "resolution": "approved",
                }
            ]
            result = await artifact_service.store_approval_history("job-1", approvals, slug="my-job")
            assert result is not None
            assert result.type == ArtifactType.approval_history
            mock_repo.create.assert_called_once()
        finally:
            mod._ARTIFACTS_BASE = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path
        try:
            disk_file = tmp_path / "art-existing-approvals.json"
            disk_file.write_text('{"approvals": []}')
            existing = _make_artifact(
                artifact_id="art-approvals",
                job_id="job-1",
                name="my-job-approvals.json",
                art_type=ArtifactType.approval_history,
                disk_path=str(disk_file),
            )
            mock_repo.list_for_job.return_value = [existing]

            approvals = [
                {
                    "id": "appr-2",
                    "description": "check",
                    "proposed_action": "y",
                    "requested_at": "t2",
                    "resolved_at": "t3",
                    "resolution": "approved",
                }
            ]
            result = await artifact_service.store_approval_history("job-1", approvals, slug="my-job")

            mock_repo.create.assert_not_called()
            mock_repo.update_size_bytes.assert_called_once()
            on_disk = json.loads(disk_file.read_text())
            assert on_disk["approvals"][0]["id"] == "appr-2"
            assert result is not None
            assert result.id == "art-approvals"
        finally:
            mod._ARTIFACTS_BASE = orig


class TestCollectFromSessionStorageUpsert:
    @pytest.mark.asyncio
    async def test_upserts_existing_document_on_second_session(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        """Second session with updated plan.md should overwrite the existing artifact, not create a new one."""
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            # Simulate existing plan.md artifact from session 1
            existing_disk = tmp_path / "store" / "plan-v1.md"
            existing_disk.parent.mkdir(parents=True, exist_ok=True)
            existing_disk.write_text("# Plan v1")
            existing_doc = _make_artifact(
                artifact_id="art-doc-1",
                job_id="job-1",
                name="plan.md",
                art_type=ArtifactType.document,
                disk_path=str(existing_disk),
            )
            mock_repo.list_for_job.return_value = [existing_doc]

            # Session 2 writes an updated plan.md
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-v2"
            session_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Plan v2 — updated")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-v2", config_dir=tmp_path / ".copilot"
            )

            # Should return the existing artifact (updated), not create a new one
            assert len(result) == 1
            assert result[0].id == "art-doc-1"
            mock_repo.create.assert_not_called()
            mock_repo.update_size_bytes.assert_called_once_with("art-doc-1", existing_disk.stat().st_size)
            # Disk content should now be session 2's version
            assert "Plan v2" in existing_disk.read_text()
        finally:
            mod._ARTIFACTS_BASE = orig

    @pytest.mark.asyncio
    async def test_creates_new_document_when_no_existing(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        """First session: no existing artifacts, so new ones are created."""
        import backend.services.artifact_service as mod

        orig = mod._ARTIFACTS_BASE
        mod._ARTIFACTS_BASE = tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-new"
            session_dir.mkdir(parents=True)
            (session_dir / "plan.md").write_text("# Fresh plan")

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-new", config_dir=tmp_path / ".copilot"
            )

            assert len(result) == 1
            assert result[0].name == "plan.md"
            mock_repo.create.assert_called_once()
            mock_repo.update_size_bytes.assert_not_called()
        finally:
            mod._ARTIFACTS_BASE = orig
