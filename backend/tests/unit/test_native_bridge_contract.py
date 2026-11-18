import pytest

from backend.services.adapters.native_gates.bridge_contract import BridgeSpec


@pytest.mark.parametrize(
    "changes",
    [
        {"operations": frozenset()},
        {"module": "untrusted.plugin"},
        {"id": "langchain"},
        {"dependencies": (("httpx", ">=0.28"),)},
        {"dependencies": ()},
    ],
)
def test_bridge_registration_rejects_non_runnable_or_unqualified_entries(changes):
    values = {
        "id": "flowise",
        "module": "backend.services.adapters.profile_bridges.flowise",
        "dependencies": (("httpx", "0.28.1"),),
        "operations": frozenset({"predict", "upsert"}),
    }
    with pytest.raises(ValueError):
        BridgeSpec(**(values | changes))
