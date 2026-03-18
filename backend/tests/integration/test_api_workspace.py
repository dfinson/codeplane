"""Integration tests for workspace endpoints.

- GET /api/jobs/{job_id}/workspace       (list directory)
- GET /api/jobs/{job_id}/workspace/file  (read file)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient

    from backend.tests.integration.conftest import SeedJobFn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace_dir():
    """Create a temporary workspace with a known file layout.

    Layout::

        README.md          ("# Hello")
        setup.py           ("from setuptools import setup")
        src/
            main.py        ("print('hello')")
            utils.py       ("def helper(): pass")
        docs/
            guide.md       ("# Guide")
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "src"))
        os.makedirs(os.path.join(tmpdir, "docs"))
        Path(os.path.join(tmpdir, "README.md")).write_text("# Hello")
        Path(os.path.join(tmpdir, "setup.py")).write_text("from setuptools import setup")
        Path(os.path.join(tmpdir, "src", "main.py")).write_text("print('hello')")
        Path(os.path.join(tmpdir, "src", "utils.py")).write_text("def helper(): pass")
        Path(os.path.join(tmpdir, "docs", "guide.md")).write_text("# Guide")
        yield tmpdir


# ---------------------------------------------------------------------------
# List workspace
# ---------------------------------------------------------------------------


class TestListWorkspace:
    """GET /api/jobs/{job_id}/workspace"""

    @pytest.mark.asyncio
    async def test_returns_files_and_directories(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace")
        assert resp.status_code == 200
        body = resp.json()
        names = {e["path"] for e in body["items"]}
        assert "README.md" in names
        assert "src" in names
        assert "docs" in names

    @pytest.mark.asyncio
    async def test_entry_types(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Directories have type 'directory', files have type 'file'."""
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace")
        items = {e["path"]: e["type"] for e in resp.json()["items"]}
        assert items["src"] == "directory"
        assert items["README.md"] == "file"

    @pytest.mark.asyncio
    async def test_subdirectory_listing(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Passing ?path=src lists contents of src/."""
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace", params={"path": "src"})
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["items"]}
        assert "src/main.py" in paths
        assert "src/utils.py" in paths

    @pytest.mark.asyncio
    async def test_pagination_with_limit(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Passing limit=2 returns at most 2 entries with has_more=True."""
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace", params={"limit": 2})
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["hasMore"] is True
        assert body["cursor"] is not None

    @pytest.mark.asyncio
    async def test_pagination_cursor_continues(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Using cursor from first page returns subsequent entries."""
        job_id = await seed_job(worktree_path=workspace_dir)
        page1 = (await client.get(f"/api/jobs/{job_id}/workspace", params={"limit": 2})).json()
        cursor = page1["cursor"]

        page2 = (
            await client.get(
                f"/api/jobs/{job_id}/workspace",
                params={"limit": 2, "cursor": cursor},
            )
        ).json()
        page1_paths = {e["path"] for e in page1["items"]}
        page2_paths = {e["path"] for e in page2["items"]}
        assert page1_paths.isdisjoint(page2_paths), "Pages should not overlap"

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/no-such-job/workspace")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_path_traversal_returns_400(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Attempting directory traversal is rejected."""
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace", params={"path": "../../etc"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_hidden_files_excluded(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """Dot-prefixed files and directories are not listed."""
        Path(os.path.join(workspace_dir, ".hidden")).write_text("secret")
        os.makedirs(os.path.join(workspace_dir, ".git"))
        job_id = await seed_job(worktree_path=workspace_dir)

        resp = await client.get(f"/api/jobs/{job_id}/workspace")
        paths = {e["path"] for e in resp.json()["items"]}
        assert ".hidden" not in paths
        assert ".git" not in paths


# ---------------------------------------------------------------------------
# Get file contents
# ---------------------------------------------------------------------------


class TestGetWorkspaceFile:
    """GET /api/jobs/{job_id}/workspace/file"""

    @pytest.mark.asyncio
    async def test_read_file_contents(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace/file", params={"path": "README.md"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["path"] == "README.md"
        assert body["content"] == "# Hello"

    @pytest.mark.asyncio
    async def test_read_nested_file(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace/file", params={"path": "src/main.py"})
        assert resp.status_code == 200
        assert resp.json()["content"] == "print('hello')"

    @pytest.mark.asyncio
    async def test_nonexistent_file_returns_404(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace/file", params={"path": "nope.txt"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_file_path_traversal_returns_400(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(
            f"/api/jobs/{job_id}/workspace/file",
            params={"path": "../../etc/passwd"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_file_too_large_returns_413(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
    ) -> None:
        """Files exceeding 5 MB are rejected with 413."""
        with tempfile.TemporaryDirectory() as tmpdir:
            big_file = Path(tmpdir) / "big.bin"
            # Write just over 5 MB
            big_file.write_bytes(b"x" * (5 * 1024 * 1024 + 1))

            job_id = await seed_job(worktree_path=tmpdir)
            resp = await client.get(f"/api/jobs/{job_id}/workspace/file", params={"path": "big.bin"})
            assert resp.status_code == 413

    @pytest.mark.asyncio
    async def test_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        resp = await client.get("/api/jobs/no-such-job/workspace/file", params={"path": "any.txt"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_file_size_bytes_in_listing(
        self,
        client: AsyncClient,
        seed_job: SeedJobFn,
        workspace_dir: str,
    ) -> None:
        """File entries include sizeBytes; directory entries have null."""
        job_id = await seed_job(worktree_path=workspace_dir)
        resp = await client.get(f"/api/jobs/{job_id}/workspace")
        for item in resp.json()["items"]:
            if item["type"] == "file":
                assert isinstance(item["sizeBytes"], int)
            else:
                assert item["sizeBytes"] is None
