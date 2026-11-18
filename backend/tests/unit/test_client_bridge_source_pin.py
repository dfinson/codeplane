from types import SimpleNamespace

import pytest

from backend.services.adapters.native_gates import selection
from backend.services.adapters.native_gates.readiness import UnsupportedNativeHookError
from backend.services.runtime.contract_catalog import _pin


@pytest.mark.parametrize(
    "profile", ["openai-client", "anthropic-client", "gemini-client", "mistral-client", "bedrock-client"]
)
def test_five_client_source_pins_include_the_private_helper(profile, tmp_path, monkeypatch):
    source = tmp_path / "client.py"
    mapping = source.with_suffix(".yaml")
    helper = source.with_name("_client_calls.py")
    source.write_text("original")
    mapping.write_text("mapping")
    helper.write_text("first helper")
    monkeypatch.setattr(selection, "find_spec", lambda _: SimpleNamespace(origin=str(source)))
    selected = selection.NativeInstallation(
        profile, "fixed.client", (("codeplane", "0.1.0"),), frozenset({"request"}), True
    )
    original, _ = selected.observation_source()
    helper.write_text("changed helper")
    changed, _ = selected.observation_source()
    assert changed != original
    helper.unlink()
    assert not selected.readiness().ready
    with pytest.raises(UnsupportedNativeHookError, match="helper"):
        selected.observation_source()
    other = selection.NativeInstallation("flowise", "fixed.client", (), frozenset({"request"}), True)
    expected = _pin("flowise", [("fixed.client", source), ("flowise", mapping)])
    assert other.observation_source() == (expected, mapping)
