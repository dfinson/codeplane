"""Tests for mcp/tools/base.py module.

Covers:
- BaseParams class
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.base import BaseParams


class TestBaseParams:
    """Tests for BaseParams class."""

    def test_default_session_id_is_none(self) -> None:
        """Default session_id is None."""
        params = BaseParams()
        assert params.session_id is None

    def test_session_id_can_be_set(self) -> None:
        """session_id can be explicitly set."""
        params = BaseParams(session_id="my-session")
        assert params.session_id == "my-session"

    def test_forbids_extra_fields(self) -> None:
        """Rejects unknown fields."""
        with pytest.raises(ValidationError, match="extra"):
            BaseParams(unknown_field="value")  # type: ignore[call-arg]

    def test_subclass_inherits_session_id(self) -> None:
        """Subclasses inherit session_id."""

        class MyParams(BaseParams):
            value: int

        params = MyParams(value=42, session_id="sess-123")
        assert params.value == 42
        assert params.session_id == "sess-123"

    def test_subclass_forbids_extra(self) -> None:
        """Subclasses also forbid extra fields."""

        class MyParams(BaseParams):
            value: int

        with pytest.raises(ValidationError, match="extra"):
            MyParams(value=1, extra="bad")  # type: ignore[call-arg]

    def test_model_dump(self) -> None:
        """Can serialize to dict."""
        params = BaseParams(session_id="test")
        data = params.model_dump()
        assert data == {"session_id": "test", "gate_token": None, "gate_reason": None}

    def test_model_dump_excludes_none(self) -> None:
        """Can exclude None values."""
        params = BaseParams()
        data = params.model_dump(exclude_none=True)
        assert data == {}
