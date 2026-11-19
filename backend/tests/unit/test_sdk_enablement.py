"""Provider restriction applies independently of model selection."""

import pytest

from backend.config import CPLConfig
from backend.models.domain import SDKModelMismatchError
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.agent_adapter import validate_sdk_model


class TestRuntimeContract:
    def test_unknown_sdk_without_model_fails(self):
        error = None
        try:
            validate_sdk_model("unknown-provider", None)
        except SDKModelMismatchError as exc:
            error = exc
        assert error is not None, "Unknown providers must fail even without a model"

    def test_disabled_provider_never_constructs_adapter(self, monkeypatch):
        registry = AdapterRegistry()
        constructed = []
        monkeypatch.setattr(registry, "_create", lambda sdk: constructed.append(sdk))
        with pytest.raises(SDKModelMismatchError):
            registry.get_adapter("claude")
        assert constructed == []


def test_copilot_keeps_cross_provider_models():
    validate_sdk_model("copilot", "claude-sonnet-5")
    assert CPLConfig().runtime.default_sdk == "copilot"
