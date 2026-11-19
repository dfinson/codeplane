import sys

import pytest

from backend.config import CPLConfig
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.native_gates import selection

CLIENTS = ("openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client")


@pytest.mark.parametrize("profile", CLIENTS)
def test_client_catalog_remains_disabled_and_truthful_without_optional_sdk(profile, monkeypatch):
    before = set(sys.modules)

    def missing(package):
        raise selection.PackageNotFoundError(package)

    monkeypatch.setattr(selection, "version", missing)
    registry = AdapterRegistry(config=CPLConfig())
    row = next(item for item in registry.list_profiles().profiles if item.profile_id == profile)
    assert not row.admission_enabled
    assert row.supported_versions
    assert row.native_formats == {} and not row.utility_completion
    operations = {item.id: item for item in row.operations}
    for operation in ("invoke", "gate", "observe", "cancel", "steer"):
        assert operations[operation].implemented and not operations[operation].dependency.ready
        assert any("remote" in limit for limit in operations[operation].limitations)
    assert any("new admitted native request/turn" in limit for limit in operations["steer"].limitations)
    assert not {"openai", "anthropic", "google.genai", "mistralai.client", "boto3"} & (set(sys.modules) - before)
