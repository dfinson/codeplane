import sys

from backend.config import CPLConfig
from backend.services.adapters.adapter_registry import AdapterRegistry
from backend.services.adapters.native_gates import selection


def test_http_common_readiness_does_not_require_unselected_frameworks(monkeypatch):
    before = set(sys.modules)
    original = selection.version

    def missing(package):
        if package in {"flask", "django"}:
            raise selection.PackageNotFoundError(package)
        if package == "agent-governance-toolkit-core":
            return "5.0.0"
        return original(package)

    monkeypatch.setattr(selection, "version", missing)
    row = next(
        item for item in AdapterRegistry(config=CPLConfig()).list_profiles().profiles if item.profile_id == "http"
    )
    assert not row.admission_enabled and row.usage_units == []
    assert set(row.supported_versions) == {
        "agent-governance-toolkit-core==5.0.0",
    }
    assert {operation.id for operation in row.operations} == {"observe", "invoke", "gate", "stop-waiting"}
    assert all(operation.implemented and operation.dependency.ready for operation in row.operations)
    assert not {"flask", "django"} & (set(sys.modules) - before)
