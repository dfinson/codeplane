"""Tests for ranking/infra/merge_ground_truth.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ranking.infra.merge_ground_truth import merge_repo, main, TASK_PATTERN


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture()
def repo_dir(tmp_path: Path) -> Path:
    """Create a fake repo data dir with ground_truth/*.json files."""
    gt = tmp_path / "ground_truth"
    gt.mkdir()
    for tid in ("N1", "N2", "N10", "M1", "M3", "W1", "W11"):
        (gt / f"{tid}.json").write_text(
            json.dumps({"task_id": f"test-repo/{tid}", "data": tid}),
            encoding="utf-8",
        )
    return tmp_path


@pytest.fixture()
def repo_dir_with_non_ok(repo_dir: Path) -> Path:
    """Add a non_ok_queries.json to an existing fixture."""
    non_ok = repo_dir / "ground_truth" / "non_ok_queries.json"
    non_ok.write_text(
        json.dumps({"type": "non_ok", "queries": [{"gate_label": "UNSAT"}]}),
        encoding="utf-8",
    )
    return repo_dir


# ── TASK_PATTERN ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,match",
    [
        ("N1.json", True),
        ("N10.json", True),
        ("N11.json", True),
        ("M1.json", True),
        ("W11.json", True),
        ("non_ok_queries.json", False),
        ("M1_coverage.xml", False),
        ("README.md", False),
    ],
)
def test_task_pattern(name: str, match: bool) -> None:
    assert bool(TASK_PATTERN.match(name)) is match


# ── merge_repo ───────────────────────────────────────────────────────


def test_merge_basic(repo_dir: Path) -> None:
    out = merge_repo(repo_dir)
    assert out == repo_dir / "ground_truth.jsonl"
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 7  # N1, N2, N10, M1, M3, W1, W11

    # Verify sort order: N before M before W, numerically within tier
    ids = [json.loads(l)["task_id"] for l in lines]
    assert ids == [
        "test-repo/N1",
        "test-repo/N2",
        "test-repo/N10",
        "test-repo/M1",
        "test-repo/M3",
        "test-repo/W1",
        "test-repo/W11",
    ]


def test_merge_with_non_ok(repo_dir_with_non_ok: Path) -> None:
    out = merge_repo(repo_dir_with_non_ok)
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 8  # 7 tasks + non_ok
    last = json.loads(lines[-1])
    assert last["type"] == "non_ok"


def test_merge_no_gt_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No ground_truth/ directory"):
        merge_repo(tmp_path)


def test_merge_empty_gt_dir(tmp_path: Path) -> None:
    (tmp_path / "ground_truth").mkdir()
    with pytest.raises(FileNotFoundError, match="No task JSON files"):
        merge_repo(tmp_path)


def test_merge_ignores_non_task_files(repo_dir: Path) -> None:
    """Coverage XML and other files should be ignored."""
    gt = repo_dir / "ground_truth"
    (gt / "M1_coverage.xml").write_text("<xml/>")
    (gt / "README.md").write_text("# notes")
    out = merge_repo(repo_dir)
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 7  # unchanged


def test_merge_idempotent(repo_dir: Path) -> None:
    """Running twice produces identical output."""
    out1 = merge_repo(repo_dir)
    content1 = out1.read_text()
    out2 = merge_repo(repo_dir)
    content2 = out2.read_text()
    assert content1 == content2


def test_each_line_is_valid_json(repo_dir: Path) -> None:
    out = merge_repo(repo_dir)
    for line in out.read_text().strip().splitlines():
        obj = json.loads(line)
        assert isinstance(obj, dict)


# ── CLI (main) ───────────────────────────────────────────────────────


def test_main_single_repo(repo_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([str(repo_dir)])
    assert rc == 0
    assert (repo_dir / "ground_truth.jsonl").exists()
    out = capsys.readouterr().out
    assert "7 records" in out


def test_main_multiple_repos(tmp_path: Path) -> None:
    dirs = []
    for name in ("repo-a", "repo-b"):
        d = tmp_path / name
        gt = d / "ground_truth"
        gt.mkdir(parents=True)
        for tid in ("N1", "M1", "W1"):
            (gt / f"{tid}.json").write_text(
                json.dumps({"task_id": f"{name}/{tid}"}),
                encoding="utf-8",
            )
        dirs.append(str(d))

    rc = main(dirs)
    assert rc == 0
    for d in dirs:
        assert (Path(d) / "ground_truth.jsonl").exists()


def test_main_no_args(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 1
    assert "Usage" in capsys.readouterr().err


def test_main_skip_bad_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # One good, one bad
    good = tmp_path / "good"
    gt = good / "ground_truth"
    gt.mkdir(parents=True)
    (gt / "N1.json").write_text(json.dumps({"task_id": "good/N1"}))

    bad = tmp_path / "bad"
    bad.mkdir()

    rc = main([str(good), str(bad)])
    assert rc == 0  # at least one succeeded
    assert (good / "ground_truth.jsonl").exists()
    err = capsys.readouterr().err
    assert "SKIP bad" in err
