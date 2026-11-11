"""Tests for backend.services.action_policy.monitor — MonitorSession helpers."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from backend.services.action_policy.monitor import (
    _HOST_FROM_URL_RE,
    _INSTALL_PACKAGE_RE,
    MonitorSession,
    MonitorVerdict,
)


@dataclass
class FakeAction:
    kind: str = "shell"
    command: str | None = None
    path: str | None = None
    tool_name: str | None = None
    mcp_tool: str | None = None


# ── MonitorVerdict enum ──


class TestMonitorVerdict:
    def test_values(self):
        assert MonitorVerdict.approve == "approve"
        assert MonitorVerdict.reject == "reject"
        assert MonitorVerdict.escalate == "escalate"


# ── _HOST_FROM_URL_RE ──


class TestHostRegex:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("https://example.com/api", "example.com"),
            ("http://api.stripe.com:443/v1", "api.stripe.com"),
            ("//cdn.example.org/lib.js", "cdn.example.org"),
            ("no url here", None),
        ],
    )
    def test_host_extraction(self, text, expected):
        m = _HOST_FROM_URL_RE.search(text)
        if expected is None:
            assert m is None
        else:
            assert m.group(1) == expected


# ── _INSTALL_PACKAGE_RE ──


class TestInstallPackageRegex:
    @pytest.mark.parametrize(
        "text,expected_pkg",
        [
            ("npm install lodash", "lodash"),
            ("pip install requests", "requests"),
            ("uv add httpx", "httpx"),
            ("cargo add serde", "serde"),
            ("gem install rails", "rails"),
            ("composer require laravel/framework", "laravel/framework"),
            ("echo hello", None),
        ],
    )
    def test_package_extraction(self, text, expected_pkg):
        m = _INSTALL_PACKAGE_RE.search(text)
        if expected_pkg is None:
            assert m is None
        else:
            assert m.group(1) == expected_pkg


# ── MonitorSession._extract_host ──


class TestExtractHost:
    def test_command_with_url(self):
        action = FakeAction(command="curl https://api.example.com/data")
        result = MonitorSession._extract_host(action)
        assert result == "api.example.com"

    def test_command_no_url(self):
        action = FakeAction(command="ls -la")
        result = MonitorSession._extract_host(action)
        assert result is None

    def test_path_with_url(self):
        action = FakeAction(command=None, path="https://cdn.example.org/asset.js")
        result = MonitorSession._extract_host(action)
        assert result == "cdn.example.org"

    def test_path_no_url(self):
        action = FakeAction(command=None, path="/home/user/file.txt")
        result = MonitorSession._extract_host(action)
        assert result is None

    def test_no_command_no_path(self):
        action = FakeAction(command=None, path=None)
        result = MonitorSession._extract_host(action)
        assert result is None


# ── MonitorSession._extract_packages ──


class TestExtractPackages:
    def test_single_package(self):
        action = FakeAction(command="pip install requests")
        result = MonitorSession._extract_packages(action)
        assert result == ["requests"]

    def test_multiple_packages(self):
        action = FakeAction(command="pip install requests httpx aiohttp")
        result = MonitorSession._extract_packages(action)
        assert result == ["requests", "httpx", "aiohttp"]

    def test_packages_with_versions(self):
        action = FakeAction(command="pip install requests>=2.0 httpx~=0.24")
        result = MonitorSession._extract_packages(action)
        assert result == ["requests", "httpx"]

    def test_scoped_npm_package(self):
        action = FakeAction(command="npm install @types/node")
        result = MonitorSession._extract_packages(action)
        assert result == ["node"]

    def test_packages_stop_at_flag(self):
        action = FakeAction(command="pip install requests -r requirements.txt")
        result = MonitorSession._extract_packages(action)
        assert result == ["requests"]

    def test_no_install_command(self):
        action = FakeAction(command="echo hello")
        result = MonitorSession._extract_packages(action)
        assert result == []

    def test_no_command(self):
        action = FakeAction(command=None)
        result = MonitorSession._extract_packages(action)
        assert result == []

    def test_uv_add(self):
        action = FakeAction(command="uv add pytest")
        result = MonitorSession._extract_packages(action)
        assert result == ["pytest"]


# ── MonitorSession._structural_check ──


class _FakeProjectContext:
    """Minimal stand-in for ProjectContext with controllable data."""

    def __init__(
        self,
        *,
        hosts: frozenset[str] | None = None,
        deps: frozenset[str] | None = None,
        services: frozenset[str] | None = None,
    ) -> None:
        self._initial_hosts = hosts or frozenset()
        self._initial_deps = deps or frozenset()
        self._initial_services = services or frozenset()
        self.built = True

    def has_initial_host(self, host: str) -> bool:
        return host.lower() in self._initial_hosts

    def has_initial_dependency(self, name: str) -> bool:
        return name.lower() in self._initial_deps

    @property
    def initial_services(self) -> frozenset[str]:
        return self._initial_services


def _make_monitor(
    *,
    hosts: frozenset[str] | None = None,
    deps: frozenset[str] | None = None,
    services: frozenset[str] | None = None,
) -> MonitorSession:
    """Build a MonitorSession with a fake project context (no LLM, no DB)."""
    from unittest.mock import MagicMock

    session = MonitorSession.__new__(MonitorSession)
    session._job_id = "j1"
    session._job_prompt = "test"
    session._completer = MagicMock()
    session._trail_repo = MagicMock()
    session._context = _FakeProjectContext(hosts=hosts, deps=deps, services=services)  # type: ignore[assignment]
    session._context_lock = MagicMock()
    session._llm_call_times = []
    session._invalidation_hwm = 0
    return session


class TestStructuralCheckHost:
    def test_exact_host_match(self) -> None:
        mon = _make_monitor(hosts=frozenset({"api.stripe.com"}))
        action = FakeAction(command="curl https://api.stripe.com/v1/charges")
        verdict, evidence = mon._structural_check(action)
        assert verdict == MonitorVerdict.approve
        assert "api.stripe.com" in evidence

    def test_sld_match_via_dependency(self) -> None:
        mon = _make_monitor(deps=frozenset({"stripe"}))
        action = FakeAction(command="curl https://api.stripe.com/v1/charges")
        verdict, evidence = mon._structural_check(action)
        assert verdict == MonitorVerdict.approve
        assert "stripe" in evidence

    def test_unknown_host_falls_through(self) -> None:
        mon = _make_monitor()
        action = FakeAction(command="curl https://evil.example.com")
        verdict, _ = mon._structural_check(action)
        assert verdict is None

    def test_short_sld_not_matched(self) -> None:
        """SLD ≤3 chars (e.g. 'io') should NOT auto-match dependencies."""
        mon = _make_monitor(deps=frozenset({"io"}))
        action = FakeAction(command="curl https://example.io/api")
        verdict, _ = mon._structural_check(action)
        assert verdict is None


class TestStructuralCheckPackage:
    def test_all_packages_known(self) -> None:
        mon = _make_monitor(deps=frozenset({"requests", "httpx"}))
        action = FakeAction(command="pip install requests httpx")
        verdict, evidence = mon._structural_check(action)
        assert verdict == MonitorVerdict.approve
        assert "existing" in evidence.lower()

    def test_one_unknown_package_falls_through(self) -> None:
        mon = _make_monitor(deps=frozenset({"requests"}))
        action = FakeAction(command="pip install requests evil-pkg")
        verdict, _ = mon._structural_check(action)
        assert verdict is None

    def test_no_packages_in_command(self) -> None:
        mon = _make_monitor(deps=frozenset({"requests"}))
        action = FakeAction(command="ls -la")
        verdict, _ = mon._structural_check(action)
        assert verdict is None


class TestStructuralCheckService:
    def test_service_name_match(self) -> None:
        mon = _make_monitor(services=frozenset({"postgres"}))
        action = FakeAction(command="docker exec postgres pg_dump")
        verdict, evidence = mon._structural_check(action)
        assert verdict == MonitorVerdict.approve
        assert "postgres" in evidence

    def test_short_service_name_skipped(self) -> None:
        """Service names ≤3 chars (e.g. 'db') should be ignored to avoid false positives."""
        mon = _make_monitor(services=frozenset({"db"}))
        action = FakeAction(command="docker exec db psql")
        verdict, _ = mon._structural_check(action)
        assert verdict is None

    def test_hostname_poisoning_rejected(self) -> None:
        """'postgres' in 'postgres.evil.com' must NOT match as a service."""
        mon = _make_monitor(services=frozenset({"postgres"}))
        action = FakeAction(command="curl https://postgres.evil.com/data")
        verdict, _ = mon._structural_check(action)
        assert verdict is None

    def test_no_services_configured(self) -> None:
        mon = _make_monitor(services=frozenset())
        action = FakeAction(command="docker exec postgres pg_dump")
        verdict, _ = mon._structural_check(action)
        assert verdict is None


# ── MonitorSession.evaluate error wrapper ──


@pytest.mark.asyncio
class TestEvaluateWrapper:
    async def test_exception_escalates(self) -> None:
        """Any error in _evaluate_impl should escalate to human."""
        from unittest.mock import AsyncMock

        mon = _make_monitor()
        mon._evaluate_impl = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        action = FakeAction(command="rm -rf /")
        verdict, evidence = await mon.evaluate(action, classification=None)  # type: ignore[arg-type]
        assert verdict == MonitorVerdict.escalate
        assert "error" in evidence.lower()


# ── MonitorSession._llm_evaluate rate limit ──


@pytest.mark.asyncio
class TestLlmEvaluateRateLimit:
    async def test_rate_limited_escalates(self) -> None:
        """When too many LLM calls in the window, auto-escalate."""
        import time

        mon = _make_monitor()
        # Fill the call times to the max
        now = time.monotonic()
        mon._llm_call_times = [now - i for i in range(MonitorSession._LLM_RATE_MAX_CALLS)]

        action = FakeAction(command="suspicious command")
        # We need a minimal Classification mock
        from unittest.mock import MagicMock

        classification = MagicMock()
        classification.reason = "test"

        verdict, evidence = await mon._llm_evaluate(action, classification)
        assert verdict == MonitorVerdict.escalate
        assert "rate limited" in evidence.lower()
