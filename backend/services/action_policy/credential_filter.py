"""Deterministic credential patterns only. This is not a PII/NER detector."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

REPLACEMENT = "[REDACTED]"
_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "apikey",
        "accesstoken",
        "refreshtoken",
        "clientsecret",
        "authorization",
        "awssecretaccesskey",
        "privatekey",
    }
)
_PEM = re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|\Z)", re.DOTALL)
_AUTH = re.compile(r"\bauthorization\s*:\s*((?:Bearer|Basic)\s+[^\r\n]+)", re.IGNORECASE)
_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255}"
    r"|(?:AKIA|ASIA)[A-Z0-9]{16})(?![A-Za-z0-9_])"
)
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")
_FIELD = re.compile(r"""(?<![\w-])(?P<key>["']?[A-Za-z_][\w-]*["']?)\s*(?P<separator>[:=])\s*""")
_OPTION = re.compile(r"(?<!\S)--?(?P<key>[A-Za-z][\w-]*)(?:=|\s+)")


@dataclass(frozen=True, slots=True)
class Redaction:
    raw_start: int
    raw_end: int
    sanitized_start: int
    sanitized_end: int
    rules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FilteredText:
    text: str
    raw_sha256: str
    sanitized_sha256: str
    redactions: tuple[Redaction, ...]

    @property
    def redacted_semantics(self) -> bool:
        return bool(self.redactions)


def _secret_name(name: str) -> bool:
    return re.sub(r"[^a-z0-9]", "", name.lower()) in _NAMES


def _value_end(text: str, start: int, *, json_value: bool) -> int:
    if start == len(text):
        return start
    if json_value:
        try:
            _, length = json.JSONDecoder().raw_decode(text[start:])
            return start + length
        except json.JSONDecodeError:
            # Malformed structured secrets are withheld through the end of input.
            return len(text)
    quote = text[start] if text[start] in "\"'" else None
    if quote is not None:
        cursor = start + 1
        while cursor < len(text):
            if text[cursor] == "\\":
                cursor += 2
                continue
            if text[cursor] == quote:
                if cursor + 1 < len(text) and text[cursor + 1] == quote:
                    cursor += 2
                    continue
                return cursor + 1
            cursor += 1
        return len(text)
    match = re.search(r"[\s,;)}]", text[start:])
    return start + match.start() if match else len(text)


def filter_credentials(text: str) -> FilteredText:
    if len(text.encode("utf-8")) > 1024 * 1024:
        raise ValueError("Credential filtering input exceeds 1 MiB")
    spans: list[tuple[int, int, str]] = []
    for rule, pattern in (("private_key", _PEM), ("token_shape", _TOKEN)):
        spans.extend((match.start(), match.end(), rule) for match in pattern.finditer(text))
    spans.extend((match.start(1), match.end(1), "authorization") for match in _AUTH.finditer(text))
    for match in _URL.finditer(text):
        try:
            parsed = urlsplit(match.group())
            secret = parsed.username is not None or parsed.password is not None
            secret = secret or any(_secret_name(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True))
            # A second decode catches encoded key names without treating arbitrary
            # URL substrings as credentials.
            secret = secret or any(
                _secret_name(unquote(key)) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            )
        except ValueError:
            secret = True
        if secret:
            spans.append((match.start(), match.end(), "url_credentials"))
    for match in _FIELD.finditer(text):
        key = match.group("key")
        if not _secret_name(key):
            continue
        is_json = key.startswith('"') and key.endswith('"') and match.group("separator") == ":"
        end = _value_end(text, match.end(), json_value=is_json)
        if not is_json and match.end() < len(text) and text[match.end()] not in "\"'":
            # Unquoted configuration values may contain spaces. Withhold the
            # entire value line rather than interpreting it as shell words.
            newline = text.find("\n", match.end())
            end = newline if newline >= 0 else len(text)
            if text[match.end() : end].strip() in {"|", "|-", "|+", ">", ">-", ">+"}:
                line_start = text.rfind("\n", 0, match.start()) + 1
                prefix = text[line_start : match.start()]
                indent = len(prefix) - len(prefix.lstrip())
                cursor = end + 1
                for line in text[cursor:].splitlines(keepends=True):
                    if line.strip() and len(line) - len(line.lstrip(" \t")) <= indent:
                        break
                    end = cursor + len(line)
                    cursor = end
        if end > match.end():
            spans.append((match.end(), end, "named_field"))
    for match in _OPTION.finditer(text):
        if _secret_name(match.group("key")):
            end = _value_end(text, match.end(), json_value=False)
            if end > match.end():
                spans.append((match.end(), end, "command_option"))
    merged: list[tuple[int, int, set[str]]] = []
    for start, end, rule in sorted(spans):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end, rules = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), rules | {rule})
        else:
            merged.append((start, end, {rule}))
    output: list[str] = []
    redactions: list[Redaction] = []
    cursor = 0
    length = 0
    for start, end, rules in merged:
        output.append(text[cursor:start])
        length += start - cursor
        output.append(REPLACEMENT)
        redactions.append(Redaction(start, end, length, length + len(REPLACEMENT), tuple(sorted(rules))))
        length += len(REPLACEMENT)
        cursor = end
    output.append(text[cursor:])
    sanitized = "".join(output)
    return FilteredText(
        sanitized,
        hashlib.sha256(text.encode("utf-8")).hexdigest(),
        hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
        tuple(redactions),
    )
