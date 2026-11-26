import pytest

from backend.services.action_policy.credential_filter import REPLACEMENT, filter_credentials


@pytest.mark.parametrize(
    ("source", "secret"),
    [
        ('{"api_key": "multi word secret", "name": "retained"}', '"multi word secret"'),
        ('{"private_key": {"nested": ["secret", "data"]}}', '{"nested": ["secret", "data"]}'),
        ("PASSWORD='two words'; printf okay", "'two words'"),
        ('run --client-secret "value with spaces" next', '"value with spaces"'),
        ("Authorization: Bearer a-long-secret\nAccept: application/json", "Bearer a-long-secret"),
        ("Authorization: Basic YTpi\nAccept: text/plain", "Basic YTpi"),
        ("https://user:password@example.test/path", "https://user:password@example.test/path"),
        ("https://example.test/path?%61pi_key=secret", "https://example.test/path?%61pi_key=secret"),
        ("-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----", "secret"),
        ("prefix ghp_" + "a" * 36 + " suffix", "ghp_" + "a" * 36),
        ("AKIA" + "A" * 16, "AKIA" + "A" * 16),
    ],
)
def test_documented_credentials_are_redacted_with_original_spans(source, secret) -> None:
    result = filter_credentials(source)
    assert secret not in result.text
    assert REPLACEMENT in result.text
    assert result.redacted_semantics
    assert result.raw_sha256 != result.sanitized_sha256
    for span in result.redactions:
        assert result.text[span.sanitized_start : span.sanitized_end] == REPLACEMENT
        assert source[span.raw_start : span.raw_end]


def test_noncredential_text_is_unchanged_not_a_pii_promise() -> None:
    source = "Alice alice@example.test https://example.test/path?limit=0 count=0"
    result = filter_credentials(source)
    assert result.text == source
    assert not result.redacted_semantics
    assert result.raw_sha256 == result.sanitized_sha256


def test_overlapping_patterns_do_not_leave_secret_suffixes() -> None:
    result = filter_credentials('{"token": "ghp_' + "a" * 36 + ' extra secret"}')
    assert result.text == '{"token": [REDACTED]}'


def test_incomplete_quoted_secret_withholds_remaining_value() -> None:
    assert filter_credentials('token="unclosed secret with spaces').text == "token=[REDACTED]"


def test_unquoted_config_and_yaml_block_values_are_fully_withheld() -> None:
    assert filter_credentials("password: unquoted secret words\nname: retained").text == (
        "password: [REDACTED]\nname: retained"
    )
    assert filter_credentials("private_key: |\n  first secret line\n  second secret line\nname: retained").text == (
        "private_key: [REDACTED]name: retained"
    )
