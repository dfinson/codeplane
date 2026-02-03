"""Tests for MCP testing tools.

Tests parameter validation for the split test tools:
- discover_test_targets
- run_test_targets
- get_test_run_status
- cancel_test_run
"""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.testing import (
    CancelTestRunParams,
    DiscoverTestTargetsParams,
    GetTestRunStatusParams,
    RunTestTargetsParams,
)


class TestDiscoverTestTargetsParams:
    """Tests for DiscoverTestTargetsParams."""

    def test_no_params_required(self):
        """All params are optional."""
        params = DiscoverTestTargetsParams()
        assert params.paths is None

    def test_paths_provided(self):
        """paths can be provided."""
        params = DiscoverTestTargetsParams(paths=["tests/", "src/"])
        assert params.paths == ["tests/", "src/"]

    def test_session_id_inherited(self):
        """session_id is inherited from BaseParams."""
        params = DiscoverTestTargetsParams(session_id="sess_123")
        assert params.session_id == "sess_123"

    def test_extra_fields_forbidden(self):
        """Extra fields are forbidden."""
        with pytest.raises(ValidationError):
            DiscoverTestTargetsParams(unknown_field="value")


class TestRunTestTargetsParams:
    """Tests for RunTestTargetsParams."""

    def test_no_params_required(self):
        """All params are optional."""
        params = RunTestTargetsParams()
        assert params.targets is None

    def test_targets_provided(self):
        """targets can be provided."""
        params = RunTestTargetsParams(targets=["test:tests/test_main.py"])
        assert params.targets == ["test:tests/test_main.py"]

    def test_pattern_provided(self):
        """pattern can be provided."""
        params = RunTestTargetsParams(pattern="test_*.py")
        assert params.pattern == "test_*.py"

    def test_tags_provided(self):
        """tags can be provided."""
        params = RunTestTargetsParams(tags=["slow", "integration"])
        assert params.tags == ["slow", "integration"]

    def test_coverage_default(self):
        """coverage defaults to False."""
        params = RunTestTargetsParams()
        assert params.coverage is False

    def test_coverage_true(self):
        """coverage can be True."""
        params = RunTestTargetsParams(coverage=True)
        assert params.coverage is True

    def test_fail_fast_default(self):
        """fail_fast defaults to False."""
        params = RunTestTargetsParams()
        assert params.fail_fast is False

    def test_fail_fast_true(self):
        """fail_fast can be True."""
        params = RunTestTargetsParams(fail_fast=True)
        assert params.fail_fast is True

    def test_failed_only_default(self):
        """failed_only defaults to False."""
        params = RunTestTargetsParams()
        assert params.failed_only is False

    def test_parallelism_optional(self):
        """parallelism is optional."""
        params = RunTestTargetsParams()
        assert params.parallelism is None

    def test_parallelism_provided(self):
        """parallelism can be provided."""
        params = RunTestTargetsParams(parallelism=4)
        assert params.parallelism == 4

    def test_timeout_optional(self):
        """timeout_sec is optional."""
        params = RunTestTargetsParams()
        assert params.timeout_sec is None

    def test_timeout_provided(self):
        """timeout_sec can be provided."""
        params = RunTestTargetsParams(timeout_sec=300)
        assert params.timeout_sec == 300

    def test_session_id_inherited(self):
        """session_id is inherited from BaseParams."""
        params = RunTestTargetsParams(session_id="sess_123")
        assert params.session_id == "sess_123"

    def test_extra_fields_forbidden(self):
        """Extra fields are forbidden."""
        with pytest.raises(ValidationError):
            RunTestTargetsParams(unknown_field="value")


class TestGetTestRunStatusParams:
    """Tests for GetTestRunStatusParams."""

    def test_run_id_required(self):
        """run_id is required."""
        with pytest.raises(ValidationError):
            GetTestRunStatusParams()

    def test_run_id_provided(self):
        """run_id can be provided."""
        params = GetTestRunStatusParams(run_id="run_123")
        assert params.run_id == "run_123"

    def test_session_id_inherited(self):
        """session_id is inherited from BaseParams."""
        params = GetTestRunStatusParams(run_id="run_123", session_id="sess_456")
        assert params.session_id == "sess_456"

    def test_extra_fields_forbidden(self):
        """Extra fields are forbidden."""
        with pytest.raises(ValidationError):
            GetTestRunStatusParams(run_id="run_123", unknown_field="value")


class TestCancelTestRunParams:
    """Tests for CancelTestRunParams."""

    def test_run_id_required(self):
        """run_id is required."""
        with pytest.raises(ValidationError):
            CancelTestRunParams()

    def test_run_id_provided(self):
        """run_id can be provided."""
        params = CancelTestRunParams(run_id="run_123")
        assert params.run_id == "run_123"

    def test_session_id_inherited(self):
        """session_id is inherited from BaseParams."""
        params = CancelTestRunParams(run_id="run_123", session_id="sess_456")
        assert params.session_id == "sess_456"

    def test_extra_fields_forbidden(self):
        """Extra fields are forbidden."""
        with pytest.raises(ValidationError):
            CancelTestRunParams(run_id="run_123", unknown_field="value")
