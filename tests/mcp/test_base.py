"""Tests for MCP base params."""

import pytest
from pydantic import ValidationError

from codeplane.mcp.tools.base import BaseParams


class TestBaseParams:
    """Tests for BaseParams base class."""

    def test_session_id_optional(self):
        """session_id defaults to None."""
        params = BaseParams()
        assert params.session_id is None

    def test_session_id_accepts_string(self):
        """session_id accepts string value."""
        params = BaseParams(session_id="sess_123")
        assert params.session_id == "sess_123"

    def test_session_id_accepts_none(self):
        """session_id explicitly accepts None."""
        params = BaseParams(session_id=None)
        assert params.session_id is None

    def test_extra_forbid_rejects_unknown_fields(self):
        """Extra fields are rejected."""
        with pytest.raises(ValidationError) as exc_info:
            BaseParams(unknown_field="value")
        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "extra_forbidden"

    def test_subclass_inherits_session_id(self):
        """Subclass automatically has session_id."""

        class ChildParams(BaseParams):
            name: str

        params = ChildParams(name="test", session_id="sess_456")
        assert params.name == "test"
        assert params.session_id == "sess_456"

    def test_subclass_inherits_extra_forbid(self):
        """Subclass also rejects extra fields."""

        class ChildParams(BaseParams):
            name: str

        with pytest.raises(ValidationError) as exc_info:
            ChildParams(name="test", extra="nope")
        errors = exc_info.value.errors()
        assert any(e["type"] == "extra_forbidden" for e in errors)

    def test_model_dump(self):
        """model_dump works correctly."""
        params = BaseParams(session_id="sess_789")
        data = params.model_dump()
        assert data == {"session_id": "sess_789"}

    def test_model_dump_exclude_none(self):
        """model_dump can exclude None values."""
        params = BaseParams()
        data = params.model_dump(exclude_none=True)
        assert data == {}
