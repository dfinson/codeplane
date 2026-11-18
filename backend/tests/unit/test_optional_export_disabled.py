import socket
import sys

from backend.config import ExportConfig
from backend.services.events.event_bus import EventBus
from backend.services.events.export_status import ExportDropReason
from backend.services.events.optional_export import OptionalExportOwner


async def test_disabled_export_never_loads_sdk_reads_credentials_or_connects(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("disabled export attempted network access")

    def no_database():
        raise AssertionError("disabled export accessed credentials")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setenv("OTEL_EXPORTER_ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    before = set(sys.modules)
    owner = OptionalExportOwner(ExportConfig(credential_id="unavailable"), no_database)
    bus = EventBus()
    await owner.start(bus)
    await owner.close()
    assert not owner.snapshot()["enabled"] and owner._pipeline is None
    assert not any(name.startswith("opentelemetry") for name in set(sys.modules) - before)
    assert bus._subscribers == []


async def test_invalid_optional_configuration_is_health_not_a_local_startup_failure(monkeypatch):
    from backend.services.events import optional_export

    def broken(protocol):
        raise ValueError("private malformed configuration")

    monkeypatch.setattr(optional_export, "export_readiness", broken)
    owner = OptionalExportOwner(ExportConfig(enabled=True), lambda: None)
    bus = EventBus()
    await owner.start(bus)
    assert owner.snapshot()["last_reason"] == ExportDropReason.configuration
    assert not owner.snapshot()["enabled"] and bus._subscribers == []
