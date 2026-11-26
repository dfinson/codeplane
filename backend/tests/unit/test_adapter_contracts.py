from __future__ import annotations

import pytest

from backend.services.adapters.contracts import (
    Capability,
    CapabilityDeniedError,
    ObservationAdapter,
    TextCompletionProvider,
    require_capabilities,
)


def test_grant_is_exact_intersection_and_enforces_actual_invocation() -> None:
    grant = require_capabilities(
        frozenset({Capability.read}),
        frozenset({Capability.read, Capability.write}),
        frozenset(Capability),
    )
    grant.require(frozenset({Capability.read}))
    with pytest.raises(CapabilityDeniedError):
        grant.require(frozenset({Capability.write}))


@pytest.mark.parametrize("missing_from", ["policy", "adapter"])
def test_unsupported_request_is_rejected_not_silently_reduced(missing_from: str) -> None:
    with pytest.raises(CapabilityDeniedError):
        require_capabilities(
            frozenset({Capability.read, Capability.write}),
            frozenset({Capability.read}) if missing_from == "policy" else frozenset(Capability),
            frozenset({Capability.read}) if missing_from == "adapter" else frozenset(Capability),
        )


def test_observer_needs_no_dummy_execution_or_completion_methods() -> None:
    class Observer:
        def normalize(self, record):
            return ()

    assert isinstance(Observer(), ObservationAdapter)
    assert not isinstance(Observer(), TextCompletionProvider)
