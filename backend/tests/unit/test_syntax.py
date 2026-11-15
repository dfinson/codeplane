"""Real-grammar regression cases for complete, bounded command evidence."""

from __future__ import annotations

import base64

import pytest

from backend.services.action_policy.syntax import MAX_BYTES, analyze_syntax


def test_nested_substitution_keeps_all_executable_children() -> None:
    facts = analyze_syntax('echo "$(printf "%s" "$(curl https://example.test)")" > out', "bash")
    assert facts.complete, facts.incomplete
    assert {command.name for command in facts.commands} == {"echo", "printf", "curl"}
    assert {"substitution", "redirection"} <= facts.structures
    for node in facts.nodes:
        assert 0 <= node.start <= node.end <= len(facts.documents[node.document].raw)


def test_function_body_is_deferred_and_conditional_branches_are_retained() -> None:
    facts = analyze_syntax("f() { curl example.test; }; if true; then f; else rm file; fi", "bash")
    commands = {command.name: command for command in facts.commands}
    assert facts.complete, facts.incomplete
    assert commands["curl"].scope == "deferred"
    assert commands["curl"].function == "f"
    assert commands["rm"].scope == "conditional"
    assert commands["f"].scope == "conditional"


def test_literal_interpreter_script_has_original_and_derived_sources() -> None:
    raw = b"bash -c 'printf hello | cat'"
    facts = analyze_syntax(raw, "bash")
    assert facts.complete, facts.incomplete
    assert len(facts.documents) == 2
    derived = facts.documents[1]
    assert derived.raw == b"printf hello | cat"
    assert derived.origin is not None
    assert raw[derived.origin.start : derived.origin.end] == b"'printf hello | cat'"
    assert {"script", "pipeline"} <= facts.structures


def test_encoded_powershell_is_decoded_as_data_never_executed() -> None:
    script = "Get-Content file.txt | Write-Output"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode()
    facts = analyze_syntax(f"pwsh -EncodedCommand {encoded}", "bash")
    assert facts.complete, facts.incomplete
    assert facts.documents[1].raw.decode() == script
    assert facts.documents[1].origin.encoding == "base64-utf16le"
    assert {"encoded", "script", "pipeline"} <= facts.structures


def test_powershell_scriptblock_and_subexpression_are_not_skipped() -> None:
    facts = analyze_syntax("& { Get-Content file }; Write-Output $(Get-Date)", "powershell")
    assert {command.name for command in facts.commands} >= {"Get-Content", "Write-Output", "Get-Date"}
    assert any(command.scope == "deferred" for command in facts.commands if command.name == "Get-Content")


@pytest.mark.parametrize(
    ("raw", "dialect", "reason"),
    [
        (b"echo \xff", "bash", "invalid_encoding"),
        (b"echo okay", "cmd", "syntax_unsupported"),
        (b"echo okay", None, "syntax_unsupported"),
        (b"echo '", "bash", "syntax_error"),
        (b"bash script.sh", "bash", "opaque_script"),
        (b"echo $UNRESOLVED", "bash", "unresolved_expansion"),
        (b"x" * (MAX_BYTES + 1), "bash", "byte_limit"),
        (b"pwsh -EncodedCommand '!!!'", "bash", "invalid_encoded_script"),
    ],
)
def test_incomplete_input_preserves_raw_and_requires_human(raw, dialect, reason) -> None:
    facts = analyze_syntax(raw, dialect)
    assert not facts.complete
    assert reason in facts.incomplete
    assert facts.documents[0].raw == raw


def test_deep_syntax_stops_with_visible_limit() -> None:
    facts = analyze_syntax("echo " + "$(" * 40 + "pwd" + ")" * 40, "bash")
    assert not facts.complete
    assert "depth_limit" in facts.incomplete
