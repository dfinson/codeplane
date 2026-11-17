import pytest

from backend.services.adapters.agent_adapter import UtilityCompletionError
from backend.services.adapters.completion_context import CompletionContext, validate_completion_identity


@pytest.mark.parametrize("actual", ["", "unrequested-model"])
def test_assessment_cannot_use_missing_or_changed_served_model(actual) -> None:
    with pytest.raises(UtilityCompletionError):
        validate_completion_identity("approved-model", actual, CompletionContext(role="assessment"))


def test_provider_date_alias_preserves_explicit_requested_identity() -> None:
    validate_completion_identity("claude-haiku-4-5", "claude-haiku-4-5-20251001", CompletionContext(role="assessment"))
