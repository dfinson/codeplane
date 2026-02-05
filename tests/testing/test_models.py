"""Tests for testing models."""

from codeplane.testing.models import (
    ParsedTestRun,
    ParsedTestSuite,
    TargetProgress,
    TestCaseProgress,
    TestProgress,
    TestTarget,
)


class TestTestTarget:
    """Tests for TestTarget model."""

    def test_given_target_when_access_path_then_returns_selector(self) -> None:
        target = TestTarget(
            target_id="test:foo",
            selector="tests/test_foo.py",
            kind="file",
            language="python",
            runner_pack_id="python.pytest",
            workspace_root="/repo",
        )

        assert target.path == "tests/test_foo.py"
        assert target.selector == target.path

    def test_given_target_when_access_runner_then_extracts_from_pack_id(self) -> None:
        target = TestTarget(
            target_id="test:foo",
            selector=".",
            kind="package",
            language="go",
            runner_pack_id="go.gotest",
            workspace_root="/repo",
        )

        assert target.runner == "gotest"


class TestTestProgress:
    """Tests for progress tracking."""

    def test_given_progress_when_access_legacy_fields_then_returns_values(self) -> None:
        progress = TestProgress(
            targets=TargetProgress(total=10, completed=5, running=2, failed=1),
            cases=TestCaseProgress(total=50, passed=40, failed=5, skipped=3, errors=2),
        )

        # Legacy compatibility
        assert progress.total == 10  # targets.total
        assert progress.completed == 5  # targets.completed
        assert progress.passed == 40  # cases.passed
        assert progress.failed == 5  # cases.failed
        assert progress.skipped == 3  # cases.skipped


class TestParsedTestRun:
    """Tests for aggregated test run results."""

    def test_given_run_when_add_suites_then_aggregates_counts(self) -> None:
        run = ParsedTestRun(run_id="abc123")

        run.add_suite(
            ParsedTestSuite(
                name="suite1",
                total=10,
                passed=8,
                failed=1,
                skipped=1,
                duration_seconds=1.5,
            )
        )
        run.add_suite(
            ParsedTestSuite(
                name="suite2",
                total=5,
                passed=4,
                failed=1,
                errors=0,
                duration_seconds=0.5,
            )
        )

        assert run.total == 15
        assert run.passed == 12
        assert run.failed == 2
        assert run.skipped == 1
        assert run.duration_seconds == 2.0
        assert len(run.suites) == 2


class TestTestRunStatusPollHint:
    """Tests for TestRunStatus.compute_poll_hint() method."""

    def test_completed_status_returns_none(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(run_id="test", status="completed")
        assert status.compute_poll_hint() is None

    def test_cancelled_status_returns_none(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(run_id="test", status="cancelled")
        assert status.compute_poll_hint() is None

    def test_failed_status_returns_none(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(run_id="test", status="failed")
        assert status.compute_poll_hint() is None

    def test_not_found_status_returns_none(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(run_id="test", status="not_found")
        assert status.compute_poll_hint() is None

    def test_running_no_progress_returns_2_seconds(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(run_id="test", status="running")
        assert status.compute_poll_hint() == 2.0

    def test_running_zero_targets_returns_2_seconds(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(
            run_id="test",
            status="running",
            progress=TestProgress(targets=TargetProgress(total=0)),
        )
        assert status.compute_poll_hint() == 2.0

    def test_running_no_completed_returns_3_seconds(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(
            run_id="test",
            status="running",
            progress=TestProgress(targets=TargetProgress(total=10, completed=0)),
        )
        assert status.compute_poll_hint() == 3.0

    def test_running_almost_done_returns_short_interval(self) -> None:
        from codeplane.testing.models import TestRunStatus

        status = TestRunStatus(
            run_id="test",
            status="running",
            progress=TestProgress(targets=TargetProgress(total=10, completed=10)),
        )
        assert status.compute_poll_hint() == 0.5

    def test_running_with_progress_estimates_interval(self) -> None:
        from codeplane.testing.models import TestRunStatus

        # 10 targets, 5 completed in 10 seconds = 2s/target
        # 5 remaining = 10s estimated, poll at 20% = 2.0s
        status = TestRunStatus(
            run_id="test",
            status="running",
            duration_seconds=10.0,
            progress=TestProgress(targets=TargetProgress(total=10, completed=5)),
        )
        hint = status.compute_poll_hint()
        assert hint is not None
        assert 1.0 <= hint <= 10.0  # Within bounds
