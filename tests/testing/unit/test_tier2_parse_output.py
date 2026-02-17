"""Unit tests for tier2.py parse_output methods."""

from __future__ import annotations

from pathlib import Path

import pytest

from codeplane.testing.packs.tier2 import (
    CabalTestPack,
    DuneTestPack,
    JuliaPkgTestPack,
    MixTestPack,
)


class TestMixTestPackParseOutput:
    """Tests for MixTestPack.parse_output()."""

    @pytest.fixture
    def pack(self) -> MixTestPack:
        return MixTestPack()

    def test_plural_tests_plural_failures(self, pack: MixTestPack) -> None:
        """Parse '5 tests, 2 failures'."""
        stdout = "Compiling 3 files (.ex)\n5 tests, 2 failures\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 5
        assert result.failed == 2

    def test_singular_test_zero_failures(self, pack: MixTestPack) -> None:
        """Parse '1 test, 0 failures'."""
        stdout = "Compiling 1 file (.ex)\n1 test, 0 failures\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_singular_test_one_failure(self, pack: MixTestPack) -> None:
        """Parse '1 test, 1 failure'."""
        stdout = "1 test, 1 failure\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 1

    def test_no_match_returns_empty(self, pack: MixTestPack) -> None:
        """No summary line returns empty suite."""
        stdout = "Compiling project...\nDone.\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 0
        assert result.failed == 0


class TestCabalTestPackParseOutput:
    """Tests for CabalTestPack.parse_output()."""

    @pytest.fixture
    def pack(self) -> CabalTestPack:
        return CabalTestPack()

    def test_cases_failures_format(self, pack: CabalTestPack) -> None:
        """Parse 'Test Cases: 10  Tried: 10  Errors: 0  Failures: 2'."""
        stdout = "Test Cases: 10  Tried: 10  Errors: 0  Failures: 2\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 10
        assert result.failed == 2

    def test_cases_no_prefix(self, pack: CabalTestPack) -> None:
        """Parse 'Cases: 15  Tried: 15  Errors: 0  Failures: 3'."""
        stdout = "Cases: 15  Tried: 15  Errors: 0  Failures: 3\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 15
        assert result.failed == 3

    def test_suites_passed_format(self, pack: CabalTestPack) -> None:
        """Parse '1 of 1 test suites (1 of 1 test cases) passed.'."""
        stdout = "1 of 1 test suites (1 of 1 test cases) passed.\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_partial_suites_passed(self, pack: CabalTestPack) -> None:
        """Parse '2 of 3 test suites passed.'."""
        stdout = "2 of 3 test suites passed.\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 3
        assert result.failed == 1

    def test_fallback_pass_detection(self, pack: CabalTestPack) -> None:
        """Fallback: detect pass from 'test suite' + 'pass' keywords."""
        stdout = "Running test suite main-test\nAll tests pass\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_fallback_fail_detection(self, pack: CabalTestPack) -> None:
        """Fallback: detect fail from 'fail' keyword."""
        stdout = "Running test suite main-test\nSome tests fail\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 1


class TestJuliaPkgTestPackParseOutput:
    """Tests for JuliaPkgTestPack.parse_output()."""

    @pytest.fixture
    def pack(self) -> JuliaPkgTestPack:
        return JuliaPkgTestPack()

    def test_summary_table_pass_fail_total(self, pack: JuliaPkgTestPack) -> None:
        """Parse 'Test Summary: | Pass  Fail  Total' table."""
        stdout = """Test Summary: | Pass  Fail  Total
  MyModule    |    5     2      7
"""
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 7
        assert result.failed == 2

    def test_summary_table_pass_total_only(self, pack: JuliaPkgTestPack) -> None:
        """Parse table with pass and total only."""
        stdout = "Test Summary: | Pass  Total\n  Tests      |    8      8\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 8
        assert result.failed == 0

    def test_fallback_passed_detection(self, pack: JuliaPkgTestPack) -> None:
        """Fallback: detect passed from 'testing' + 'passed' keywords."""
        stdout = "Testing MyPackage\nAll tests passed\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_fallback_fail_detection(self, pack: JuliaPkgTestPack) -> None:
        """Fallback: detect fail from 'fail' or 'error' keywords."""
        stdout = "Running tests...\nSome tests failed\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 1


class TestDuneTestPackParseOutput:
    """Tests for DuneTestPack.parse_output()."""

    @pytest.fixture
    def pack(self) -> DuneTestPack:
        return DuneTestPack()

    def test_pass_word_boundary(self, pack: DuneTestPack) -> None:
        """Counts PASS as whole word, not substring."""
        stdout = "test1: PASS\ntest2: PASS\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 2
        assert result.failed == 0

    def test_fail_word_boundary(self, pack: DuneTestPack) -> None:
        """Counts FAIL as whole word, not substring."""
        stdout = "test1: PASS\ntest2: FAIL\ntest3: FAIL\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 3
        assert result.failed == 2

    def test_no_false_positives_password(self, pack: DuneTestPack) -> None:
        """Does not match 'password' as PASS."""
        stdout = "checking password validation\ntest1: PASS\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_no_false_positives_bypass(self, pack: DuneTestPack) -> None:
        """Does not match 'bypass' as PASS."""
        stdout = "testing bypass logic\nactual: PASS\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 1
        assert result.failed == 0

    def test_case_insensitive(self, pack: DuneTestPack) -> None:
        """Matches pass/PASS/Pass."""
        stdout = "test1: pass\ntest2: PASS\ntest3: Pass\n"
        result = pack.parse_output(Path("/out"), stdout)
        assert result.total == 3
        assert result.failed == 0

    def test_empty_returns_zero(self, pack: DuneTestPack) -> None:
        """Empty output returns zero counts."""
        result = pack.parse_output(Path("/out"), "")
        assert result.total == 0
        assert result.failed == 0
