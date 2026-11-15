"""Tests for ArtifactService — storage, collection, retrieval."""

from __future__ import annotations

import json
import os
import subprocess
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
from backend.services.artifacts.artifact_service import ArtifactService, _guess_mime


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
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig_base


class TestCollectFromWorkspace:
    @pytest.mark.asyncio
    async def test_collects_artifacts_from_workspace(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_skips_symlinks(
        self, artifact_service: ArtifactService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
        try:
            artifacts_dir = tmp_path / ".codeplane" / "artifacts"
            artifacts_dir.mkdir(parents=True)
            link = artifacts_dir / "link.txt"
            link.write_text("secret")
            assert link.is_file()
            original_is_symlink = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))

            result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
            assert len(result) == 0
        finally:
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_skips_large_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
        try:
            artifacts_dir = tmp_path / ".codeplane" / "artifacts"
            artifacts_dir.mkdir(parents=True)
            big_file = artifacts_dir / "huge.bin"
            # Write > 50 MB (just create a sparse-ish file by writing enough)
            big_file.write_bytes(b"\0" * (50 * 1024 * 1024 + 1))

            result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
            assert len(result) == 0
        finally:
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_no_artifacts_dir_returns_empty(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        result = await artifact_service.collect_from_workspace("job-1", str(tmp_path))
        assert result == []


class TestCollectFromSessionStorage:
    @pytest.mark.asyncio
    async def test_collects_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_ignores_non_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_ignores_other_subdirectory_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """checkpoints/ and other subdirs (except files/) must be skipped."""
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_collects_files_subdir_md_files(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """files/ subdirectory is explicitly scanned; names are prefixed with 'files/'."""
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            assert names == {"plan.md", str(Path("files") / "dummy.md"), str(Path("files") / "notes.md")}
            assert all(a.type == ArtifactType.document for a in result)
        finally:
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_files_subdir_name_collision(self, artifact_service: ArtifactService, tmp_path: Path) -> None:
        """Same filename in top-level and files/ produces two distinct artifacts."""
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            assert names == {"plan.md", str(Path("files") / "plan.md")}
        finally:
            mod.get_artifacts_base = orig_base

    @pytest.mark.asyncio
    async def test_skips_symlinks(
        self, artifact_service: ArtifactService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig_base = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
        try:
            session_dir = tmp_path / ".copilot" / "session-state" / "sess-abc"
            session_dir.mkdir(parents=True)
            link = session_dir / "link.md"
            link.write_text("# Secret")
            assert link.is_file()
            original_is_symlink = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))

            result = await artifact_service.collect_from_session_storage(
                "job-1", "sess-abc", config_dir=tmp_path / ".copilot"
            )
            assert result == []
        finally:
            mod.get_artifacts_base = orig_base

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
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
        try:
            result = await artifact_service.store_telemetry_report("job-1", {"tokens": 100}, slug="my-job")
            assert result.type == ArtifactType.telemetry_report
            mock_repo.create.assert_called_once()
            mock_repo.update_size_bytes.assert_not_called()
        finally:
            mod.get_artifacts_base = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig


class TestStoreLogArtifactUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
        try:
            events = [{"session_number": 1, "seq": 0, "level": "info", "message": "hello", "timestamp": "t1"}]
            result = await artifact_service.store_log_artifact("job-1", events, slug="my-job")
            assert result.type == ArtifactType.agent_log
            assert result.name.endswith("agent.log")
            mock_repo.create.assert_called_once()
            mock_repo.update_size_bytes.assert_not_called()
        finally:
            mod.get_artifacts_base = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig


class TestStoreAgentPlanUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
        try:
            steps = [{"step": "run tests", "status": "done"}]
            result = await artifact_service.store_agent_plan("job-1", steps, slug="my-job")
            assert result is not None
            assert result.type == ArtifactType.agent_plan
            mock_repo.create.assert_called_once()
        finally:
            mod.get_artifacts_base = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig


class TestStoreApprovalHistoryUpsert:
    @pytest.mark.asyncio
    async def test_creates_artifact_on_first_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig

    @pytest.mark.asyncio
    async def test_upserts_on_second_call(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path
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
            mod.get_artifacts_base = orig


class TestCollectFromSessionStorageUpsert:
    @pytest.mark.asyncio
    async def test_upserts_existing_document_on_second_session(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        """Second session with updated plan.md should overwrite the existing artifact, not create a new one."""
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig

    @pytest.mark.asyncio
    async def test_creates_new_document_when_no_existing(
        self, artifact_service: ArtifactService, mock_repo: AsyncMock, tmp_path: Path
    ) -> None:
        """First session: no existing artifacts, so new ones are created."""
        import backend.services.artifacts.artifact_service as mod

        orig = mod.get_artifacts_base
        mod.get_artifacts_base = lambda: tmp_path / "store"
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
            mod.get_artifacts_base = orig


_HOME_PRODUCERS = (
    "diff",
    "workspace",
    "session_document",
    "summary",
    "session_log",
    "agent_log",
    "telemetry",
    "plan",
    "approvals",
)
_HOME_UPSERTS = ("workspace", "session_document", "session_log", "agent_log", "telemetry", "plan", "approvals")


def _link_home_directory(link: Path, target: Path) -> None:
    if os.name == "nt":
        # Junctions exercise canonical escape checks without elevated symlink privileges.
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture
def artifact_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    import backend.config as config
    import backend.services.artifacts.artifact_service as mod

    home = tmp_path / "home"
    active = tmp_path / "configured" / "artifacts"
    legacy = home / ".codeplane" / "artifacts"
    legacy.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("CODEPLANE_HOME", str(active.parent))
    monkeypatch.delenv("COPILOT_HOME", raising=False)
    monkeypatch.setattr(config, "_codeplane_dir", None)
    # The unmodified module captured Path.home at import time. Red runs must
    # write only to this owned legacy stand-in, never the user's real home.
    monkeypatch.setattr(mod, "_ARTIFACTS_BASE", legacy, raising=False)
    return {"home": home, "active": active, "legacy": legacy}


async def _produce_home_artifact(
    service: ArtifactService, producer: str, inputs: Path, *, job_id: str = "job-1", revision: int = 2
) -> Artifact:
    if producer == "diff":
        return await service.store_diff_snapshot(job_id, [])
    if producer == "summary":
        return await service.store_session_summary(job_id, revision, '{"summary": "updated"}')
    if producer == "workspace":
        source = inputs / ".codeplane" / "artifacts"
        source.mkdir(parents=True, exist_ok=True)
        (source / "report.json").write_text(f'{{"revision": {revision}}}', encoding="utf-8")
        return (await service.collect_from_workspace(job_id, str(inputs)))[0]
    if producer == "session_document":
        source = inputs / "session-state" / "sdk-1"
        source.mkdir(parents=True, exist_ok=True)
        (source / "note.md").write_text(f"# Updated note {revision} — café", encoding="utf-8")
        return (await service.collect_from_session_storage(job_id, "sdk-1", config_dir=inputs))[0]
    if producer == "session_log":
        return await service.upsert_session_log(
            job_id,
            {"session_number": revision, "original_task": "new task", "changed_files": [f"file-{revision}.py"]},
            slug="safe-name",
        )
    if producer == "agent_log":
        return await service.store_log_artifact(job_id, [{"message": f"updated {revision} — café"}], slug="safe-name")
    if producer == "telemetry":
        return await service.store_telemetry_report(job_id, {"tokens": revision}, slug="safe-name")
    if producer == "plan":
        result = await service.store_agent_plan(job_id, [{"step": f"updated {revision}"}], slug="safe-name")
    elif producer == "approvals":
        result = await service.store_approval_history(job_id, [{"id": f"approval-{revision}"}], slug="safe-name")
    else:
        raise AssertionError(f"Unknown producer: {producer}")
    assert result is not None
    return result


def _existing_home_artifact(producer: str, root: Path) -> Artifact:
    kind, name = {
        "workspace": (ArtifactType.custom, "report.json"),
        "session_document": (ArtifactType.document, "note.md"),
        "session_log": (ArtifactType.session_log, "job-session-log.json"),
        "agent_log": (ArtifactType.agent_log, "job-agent.log"),
        "telemetry": (ArtifactType.telemetry_report, "job-telemetry.json"),
        "plan": (ArtifactType.agent_plan, "job-plan.json"),
        "approvals": (ArtifactType.approval_history, "job-approvals.json"),
    }[producer]
    path = root / "job-1" / f"art-existing-{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "original_task": "original task",
                "sessions": [{"session_number": 1, "changed_files": ["first.py"], "transcript_turns": ["first turn"]}],
                "all_changed_files": ["first.py"],
            }
        ),
        encoding="utf-8",
    )
    return _make_artifact(artifact_id="art-existing", job_id="job-1", name=name, art_type=kind, disk_path=str(path))


class TestArtifactHomeIsolation:
    @pytest.mark.parametrize("producer", _HOME_PRODUCERS)
    @pytest.mark.parametrize("configured", [True, False], ids=["configured", "default"])
    async def test_all_new_producers_use_selected_home(
        self, producer, configured, artifact_homes, artifact_service, tmp_path, monkeypatch
    ):
        if not configured:
            monkeypatch.delenv("CODEPLANE_HOME")
        expected = artifact_homes["active" if configured else "legacy"]
        artifact = await _produce_home_artifact(artifact_service, producer, tmp_path / "inputs")
        path = Path(artifact.disk_path)
        assert path.resolve().is_relative_to(expected.resolve())
        assert path.parent.name == "job-1"
        assert path.name.startswith(f"{artifact.id}-")
        assert artifact.size_bytes == path.stat().st_size
        if configured:
            assert list(artifact_homes["legacy"].rglob("*")) == []

    @pytest.mark.parametrize("producer", _HOME_UPSERTS)
    @pytest.mark.parametrize("configured", [True, False], ids=["configured", "default"])
    async def test_in_root_updates_keep_id_and_path(
        self, producer, configured, artifact_homes, artifact_service, mock_repo, tmp_path, monkeypatch
    ):
        if not configured:
            monkeypatch.delenv("CODEPLANE_HOME")
        root = artifact_homes["active" if configured else "legacy"]
        existing = _existing_home_artifact(producer, root)
        old_path = existing.disk_path
        before = Path(old_path).read_bytes()
        mock_repo.list_for_job.return_value = [existing]
        result = await _produce_home_artifact(artifact_service, producer, tmp_path / "inputs")
        assert result.id == "art-existing"
        assert Path(result.disk_path) == Path(old_path)
        assert Path(old_path).read_bytes() != before
        assert result.size_bytes == Path(old_path).stat().st_size
        mock_repo.create.assert_not_awaited()

    @pytest.mark.parametrize("producer", _HOME_UPSERTS)
    async def test_explicit_legacy_upsert_writes_new_content_without_touching_original(
        self, producer, artifact_homes, artifact_service, mock_repo, tmp_path
    ):
        existing = _existing_home_artifact(producer, artifact_homes["legacy"])
        original = Path(existing.disk_path)
        before = original.read_bytes()
        mock_repo.list_for_job.return_value = [existing]
        result = await _produce_home_artifact(artifact_service, producer, tmp_path / "inputs")
        assert original.read_bytes() == before, "Explicit update must leave the legacy original untouched"
        assert result.id == "art-existing"
        assert Path(result.disk_path).resolve().is_relative_to(artifact_homes["active"].resolve())
        assert Path(result.disk_path).read_bytes() != before
        assert result.size_bytes == Path(result.disk_path).stat().st_size
        mock_repo.create.assert_not_awaited()
        if producer == "session_log":
            content = json.loads(Path(result.disk_path).read_text(encoding="utf-8"))
            assert [s["session_number"] for s in content["sessions"]] == [1, 2]
            assert content["sessions"][0]["transcript_turns"] == ["first turn"]
            assert content["original_task"] == "original task"
            assert content["all_changed_files"] == ["file-2.py", "first.py"]

    @pytest.mark.parametrize("producer", _HOME_UPSERTS)
    @pytest.mark.parametrize("bad_path", ["arbitrary", "traversal", "symlink", "wrong_job", "wrong_artifact"])
    async def test_rejects_out_of_policy_existing_paths_before_any_write(
        self, producer, bad_path, artifact_homes, artifact_service, mock_repo, tmp_path
    ):
        existing = _existing_home_artifact(producer, artifact_homes["legacy"])
        outside = tmp_path / "outside" / Path(existing.disk_path).name
        outside.parent.mkdir()
        outside.write_bytes(Path(existing.disk_path).read_bytes())
        if bad_path == "arbitrary":
            existing.disk_path = str(outside)
        elif bad_path == "traversal":
            existing.disk_path = str(artifact_homes["legacy"] / ".." / ".." / ".." / "outside" / outside.name)
        elif bad_path == "symlink":
            link = Path(existing.disk_path)
            link.unlink()
            link.parent.rmdir()
            _link_home_directory(link.parent, outside.parent)
        elif bad_path == "wrong_job":
            existing.job_id = "other-job"
        else:
            existing.id = "art-other"
        mock_repo.list_for_job.return_value = [existing]
        before = outside.read_bytes()
        with pytest.raises(ValueError):
            await _produce_home_artifact(artifact_service, producer, tmp_path / "inputs")
        assert outside.read_bytes() == before
        assert not artifact_homes["active"].exists()
        mock_repo.create.assert_not_awaited()
        mock_repo.update_size_bytes.assert_not_awaited()

    @pytest.mark.parametrize("producer", _HOME_PRODUCERS)
    @pytest.mark.parametrize(
        "bad_job", ["../escape", "absolute", "symlink"] + ([r"..\escape"] if os.name == "nt" else [])
    )
    async def test_new_destinations_reject_job_traversal_and_symlink_escape(
        self, producer, bad_job, artifact_homes, artifact_service, mock_repo, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        job_id = bad_job
        if bad_job == "absolute":
            job_id = str(outside)
        elif bad_job == "symlink":
            artifact_homes["active"].mkdir(parents=True)
            _link_home_directory(artifact_homes["active"] / "job-link", outside)
            # Also keep old-product red runs wholly inside owned test storage.
            _link_home_directory(artifact_homes["legacy"] / "job-link", outside)
            job_id = "job-link"
        with pytest.raises(ValueError):
            await _produce_home_artifact(artifact_service, producer, tmp_path / "inputs", job_id=job_id)
        assert list(outside.iterdir()) == []
        mock_repo.create.assert_not_awaited()

    @pytest.mark.parametrize(
        "filename", ["report.json"] + (["report:detail.json", r"report\detail.json"] if os.name != "nt" else [])
    )
    async def test_workspace_preserves_valid_host_basenames(self, filename, artifact_homes, artifact_service, tmp_path):
        source = tmp_path / "workspace" / ".codeplane" / "artifacts"
        source.mkdir(parents=True)
        (source / filename).write_text('{"valid": true}', encoding="utf-8")
        artifacts = await artifact_service.collect_from_workspace("job-1", str(tmp_path / "workspace"))
        assert len(artifacts) == 1
        artifact = artifacts[0]
        assert artifact.name == filename
        path = Path(artifact.disk_path)
        assert path.name == f"{artifact.id}-{filename}"
        assert path.resolve().is_relative_to(artifact_homes["active"].resolve())
        assert path.read_text(encoding="utf-8") == '{"valid": true}'

    async def test_artifacts_root_symlink_cannot_escape_configured_home(
        self, artifact_homes, artifact_service, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        artifact_homes["active"].parent.mkdir(parents=True)
        _link_home_directory(artifact_homes["active"], outside)
        with pytest.raises(ValueError):
            await artifact_service.store_diff_snapshot("job-1", [])
        assert list(outside.iterdir()) == []

    @pytest.mark.parametrize("selection", ["explicit", "environment", "default"])
    async def test_session_note_source_precedence(
        self, selection, artifact_homes, artifact_service, tmp_path, monkeypatch
    ):
        roots = {
            "explicit": tmp_path / "explicit-copilot",
            "environment": tmp_path / "environment-copilot",
            "default": artifact_homes["home"] / ".copilot",
        }
        for key, root in roots.items():
            folder = root / "session-state" / "sdk-1"
            (folder / "files").mkdir(parents=True)
            (folder / "note.md").write_text(key, encoding="utf-8")
            (folder / "files" / "note.md").write_text(f"{key} nested", encoding="utf-8")
        if selection != "default":
            monkeypatch.setenv("COPILOT_HOME", str(roots["environment"]))
        result = await artifact_service.collect_from_session_storage(
            "job-1", "sdk-1", config_dir=roots["explicit"] if selection == "explicit" else None
        )
        assert len(result) == 2
        assert {Path(a.disk_path).read_text(encoding="utf-8") for a in result} == {selection, f"{selection} nested"}

    async def test_listing_legacy_metadata_never_rehomes_files(self, artifact_homes, artifact_service, mock_repo):
        existing = _existing_home_artifact("telemetry", artifact_homes["legacy"])
        before = Path(existing.disk_path).read_bytes()
        mock_repo.list_for_job.return_value = [existing]
        assert await artifact_service.list_for_job("job-1") == [existing]
        assert Path(existing.disk_path).read_bytes() == before
        assert not artifact_homes["active"].exists()
        mock_repo.update_size_bytes.assert_not_awaited()

    async def test_session_log_merges_only_bounded_historical_snapshots(
        self, artifact_homes, artifact_service, mock_repo
    ):
        existing = _existing_home_artifact("session_log", artifact_homes["legacy"])
        existing.type = ArtifactType.session_snapshot
        path = Path(existing.disk_path)
        path.write_text('{"session_number": 1, "changed_files": ["old.py"], "original_task": "old"}')
        before = path.read_bytes()
        mock_repo.list_for_job.return_value = [existing]
        result = await artifact_service.upsert_session_log("job-1", {"session_number": 2})
        assert Path(result.disk_path).resolve().is_relative_to(artifact_homes["active"].resolve())
        assert path.read_bytes() == before
        content = json.loads(Path(result.disk_path).read_text())
        assert [s["session_number"] for s in content["sessions"]] == [1, 2]
        assert content["all_changed_files"] == ["old.py"]
        assert content["original_task"] == "old"

    async def test_session_log_rejects_unbounded_snapshot_before_reading(
        self, artifact_homes, artifact_service, mock_repo, tmp_path, monkeypatch
    ):
        existing = _existing_home_artifact("session_log", tmp_path / "arbitrary")
        existing.type = ArtifactType.session_snapshot
        mock_repo.list_for_job.return_value = [existing]
        original_read = Path.read_text
        reads = []

        def record_read(path, *args, **kwargs):
            reads.append(path)
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", record_read)
        with pytest.raises(ValueError):
            await artifact_service.upsert_session_log("job-1", {"session_number": 2})
        assert Path(existing.disk_path) not in reads
        mock_repo.create.assert_not_awaited()
