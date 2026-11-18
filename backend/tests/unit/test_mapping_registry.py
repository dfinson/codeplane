"""Local declarative definitions, not evidence of real vendor compatibility."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.config import NativeMappingsConfig, load_config, save_config
from backend.models.domain import StateConflictError
from backend.services.ingest.mapping import NativeMappingDecoder
from backend.services.ingest.mapping_registry import MappingRegistry

_DEFINITION = """\
framework: local-test
framework_version: "1"
ingestion_mode: replay
events:
  answer:
    kind: message.assistant
    payload:
      content: text
"""


@pytest.fixture
def mapping_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def _write(directory, name="local", definition=_DEFINITION):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(definition, encoding="utf-8")
    return path


def test_mapping_configuration_round_trips_search_order(mapping_home):
    path = mapping_home / "config.yaml"
    path.write_text(
        "native_mappings:\n  extra_dirs: [/first, /second]\n  migrated_user_dir: /migrated\n"
        "telemetry:\n  instance_id: retained\n"
    )
    config = load_config(path)
    assert config.native_mappings == NativeMappingsConfig(["/first", "/second"], "/migrated")
    save_config(config, path)
    assert load_config(path).native_mappings == config.native_mappings


@pytest.mark.parametrize("section", ["[]", "{extra_dirs: /not-a-list}", "{migrated_user_dir: false}"])
def test_mapping_configuration_rejects_malformed_paths(mapping_home, section):
    path = mapping_home / "config.yaml"
    path.write_text(f"native_mappings: {section}")
    with pytest.raises(ValueError, match="native_mappings"):
        load_config(path)


def test_registry_uses_existing_precedence_and_migrates_only_selected_legacy_file(mapping_home):
    explicit = [mapping_home / "first", mapping_home / "second"]
    migrated = mapping_home / "migrated"
    legacy = mapping_home / ".traceforge" / "mappings"
    paths = [
        _write(directory, "copilot", _DEFINITION.replace('"1"', f'"{index}"'))
        for index, directory in enumerate([*explicit, migrated, legacy])
    ]
    unselected = _write(legacy, "unselected", "x" * (1024 * 1024 + 1))
    registry = MappingRegistry(NativeMappingsConfig([str(path) for path in explicit], str(migrated)))
    for index, path in enumerate(paths[:3]):
        assert registry.pin("copilot")["framework_version"] == str(index)
        path.unlink()
    pin = registry.pin("copilot")
    assert pin["framework_version"] == "3"
    assert (migrated / "copilot.yaml").read_bytes() == paths[3].read_bytes()
    assert list(migrated.iterdir()) == [migrated / "copilot.yaml"]
    assert unselected.stat().st_size == 1024 * 1024 + 1
    (migrated / "copilot.yaml").unlink()
    paths[3].unlink()
    assert registry.pin("copilot")["framework"] == "copilot"


@pytest.mark.parametrize(
    "definition",
    [
        "events: [",
        _DEFINITION + "unsupported: true\n",
        _DEFINITION + "preprocessor: nonexistent\n",
        "x" * (1024 * 1024 + 1),
    ],
)
def test_invalid_selected_definition_never_falls_back_to_bundled(mapping_home, definition):
    directory = mapping_home / "explicit"
    _write(directory, "copilot", definition)
    registry = MappingRegistry(NativeMappingsConfig(extra_dirs=[str(directory)]))
    with pytest.raises(ValueError):
        registry.pin("copilot")


def test_legacy_migration_resolves_and_validates_concurrent_winner(mapping_home, monkeypatch):
    import os

    legacy = _write(mapping_home / ".traceforge" / "mappings")
    winner = _DEFINITION.replace('"1"', '"2"')
    link = os.link

    def publish_after_winner(candidate, destination):
        Path(destination).write_text(winner)
        link(candidate, destination)

    monkeypatch.setattr(os, "link", publish_after_winner)
    pin = MappingRegistry().pin("local")
    assert pin["content_sha256"] == hashlib.sha256(winner.encode()).hexdigest()
    assert pin["framework_version"] == "2"
    assert legacy.read_text() == _DEFINITION
    assert list((mapping_home / ".codeplane" / "mappings").iterdir()) == [
        mapping_home / ".codeplane" / "mappings" / "local.yaml"
    ]


def test_registry_decoder_validates_the_same_bounded_bytes_it_pins(mapping_home, monkeypatch):
    directory = mapping_home / "explicit"
    path = _write(directory)
    registry = MappingRegistry(NativeMappingsConfig(extra_dirs=[str(directory)]))
    original = NativeMappingDecoder.__init__

    def replace_selected_file(decoder, snapshot, **kwargs):
        path.write_text(_DEFINITION.replace('"1"', '"2"'))
        original(decoder, snapshot, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(NativeMappingDecoder, "__init__", replace_selected_file)
        pin = registry.pin("local")
    assert pin["content_sha256"] == hashlib.sha256(_DEFINITION.encode()).hexdigest()
    assert pin["framework_version"] == "1"
    with pytest.raises(StateConflictError, match="reconciliation"):
        registry.decoder(
            "local", pin=pin, job_id="job", external_session_id="source", anchor=datetime(2026, 1, 1, tzinfo=UTC)
        )
