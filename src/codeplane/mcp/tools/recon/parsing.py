"""Task parsing — extract structured signals from free-text task descriptions.

Single Responsibility: Text analysis and query construction.
No I/O, no database access, no async.
"""

from __future__ import annotations

import re

from codeplane.mcp.tools.recon.models import (
    _STOP_WORDS,
    ParsedTask,
    TaskIntent,
    _extract_intent,
)

# Regex for file paths in task text
_PATH_REGEX = re.compile(
    r"(?:^|[\s`\"'(,;])("
    r"(?:[\w./-]+/)?[\w.-]+"
    r"\.(?:py|js|ts|jsx|tsx|java|go|rs|c|cpp|h|hpp|rb|php|cs|swift|kt|scala"
    r"|lua|r|m|mm|sh|bash|zsh|yaml|yml|json|toml|cfg|ini|xml)"
    r")"
    r"(?:[\s`\"'),;:.]|$)",
    re.IGNORECASE,
)

# Regex for symbol-like identifiers (PascalCase or snake_case, 3+ chars)
_SYMBOL_REGEX = re.compile(
    r"\b([A-Z][a-zA-Z0-9]{2,}(?:[A-Z][a-z]+)*"  # PascalCase
    r"|[a-z][a-z0-9]*(?:_[a-z0-9]+)+)"  # snake_case
    r"\b"
)

# Negative mention patterns — "not X", "exclude Y", "except Z", "without Y"
_NEGATIVE_REGEX = re.compile(
    r"\b(?:not|exclude|except|without|ignore|skip|excluding|except for)\s+"
    r"(\S+)",
    re.IGNORECASE,
)

# Stacktrace / error indicators
_STACKTRACE_TOKENS = frozenset(
    {
        "traceback",
        "stacktrace",
        "stack trace",
        "exception",
        "error",
        "raise",
        "raised",
        "traceback:",
        "errno",
        "oserror",
        "typeerror",
        "valueerror",
        "keyerror",
        "attributeerror",
        "importerror",
        "runtimeerror",
        "indexerror",
        "filenotfounderror",
        "nameerror",
        "zerodivisionerror",
        "assertionerror",
        "notimplementederror",
        "connectionerror",
        "timeouterror",
        "permissionerror",
    }
)

# Test-driven task indicators — the task itself is about tests
_TEST_DRIVEN_TOKENS = frozenset(
    {
        "write tests",
        "add tests",
        "test coverage",
        "missing tests",
        "increase coverage",
        "unit tests",
        "test cases",
        "integration tests",
        "parametrize",
        "pytest",
        "fixture",
        "mock",
        "assert",
    }
)


def _extract_negative_mentions(task: str) -> list[str]:
    """Extract terms that the user explicitly wants excluded.

    Patterns: "not X", "exclude Y", "except Z", "without Y"
    """
    mentions: list[str] = []
    seen: set[str] = set()
    for match in _NEGATIVE_REGEX.finditer(task):
        term = match.group(1).lower().strip(".,;:!?")
        if term and term not in seen and len(term) >= 2:
            seen.add(term)
            mentions.append(term)
    return mentions


def _detect_stacktrace_driven(task: str) -> bool:
    """Detect if the task involves error/stacktrace investigation."""
    lower = task.lower()
    # Check for multi-word tokens first
    for token in ("stack trace", "traceback:"):
        if token in lower:
            return True
    # Check for single-word tokens
    words = set(re.split(r"[^a-zA-Z]+", lower))
    hits = words & _STACKTRACE_TOKENS
    return len(hits) >= 2  # Require 2+ indicators to avoid false positives


def _detect_test_driven(task: str, intent: TaskIntent) -> bool:
    """Detect if the task is primarily about writing/fixing tests."""
    if intent == TaskIntent.test:
        return True
    lower = task.lower()
    return any(phrase in lower for phrase in _TEST_DRIVEN_TOKENS)


def parse_task(task: str) -> ParsedTask:
    """Parse a free-text task description into structured fields.

    Extraction pipeline:
    1. Extract quoted strings as high-priority exact terms.
    2. Extract file paths (``src/foo/bar.py``).
    3. Extract symbol-like identifiers (PascalCase, snake_case).
    4. Tokenize remaining text into primary (>=4 chars) and secondary (2-3 chars).
    5. Build a synthesized query for embedding similarity search.
    """
    if not task or not task.strip():
        return ParsedTask(raw=task, intent=TaskIntent.unknown)

    working = task

    # --- Step 1: Extract quoted strings ---
    quoted: list[str] = []
    for match in re.finditer(r"['\"]([^'\"]+)['\"]", working):
        quoted.append(match.group(1))
    for q in quoted:
        working = working.replace(f'"{q}"', " ").replace(f"'{q}'", " ")

    # --- Step 2: Extract file paths ---
    explicit_paths: list[str] = []
    path_seen: set[str] = set()
    for match in _PATH_REGEX.finditer(task):  # Use original task
        p = match.group(1).lstrip("./")
        if p and p not in path_seen:
            path_seen.add(p)
            explicit_paths.append(p)

    # --- Step 3: Extract symbol-like identifiers ---
    explicit_symbols: list[str] = []
    symbol_seen: set[str] = set()
    for match in _SYMBOL_REGEX.finditer(task):
        sym = match.group(1)
        if sym not in symbol_seen and sym.lower() not in _STOP_WORDS:
            symbol_seen.add(sym)
            explicit_symbols.append(sym)
    for q in quoted:
        if q not in symbol_seen and _SYMBOL_REGEX.match(q):
            symbol_seen.add(q)
            explicit_symbols.append(q)

    # --- Step 4: Tokenize into terms ---
    primary_terms: list[str] = []
    secondary_terms: list[str] = []
    seen_terms: set[str] = set()

    for q in quoted:
        low = q.lower()
        if low not in seen_terms and low not in _STOP_WORDS:
            seen_terms.add(low)
            primary_terms.append(low)

    words = re.split(r"[^a-zA-Z0-9_]+", working)
    for word in words:
        if not word:
            continue
        low = word.lower()
        if low not in seen_terms and low not in _STOP_WORDS and len(low) >= 2:
            seen_terms.add(low)
            if len(low) >= 4:
                primary_terms.append(low)
            else:
                secondary_terms.append(low)

        # Split camelCase
        camel_parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)", word)
        for part in camel_parts:
            p = part.lower()
            if p not in seen_terms and p not in _STOP_WORDS and len(p) >= 3:
                seen_terms.add(p)
                if len(p) >= 4:
                    primary_terms.append(p)
                else:
                    secondary_terms.append(p)

        # Split snake_case
        if "_" in word:
            for part in word.split("_"):
                p = part.lower()
                if p and p not in seen_terms and p not in _STOP_WORDS and len(p) >= 2:
                    seen_terms.add(p)
                    if len(p) >= 4:
                        primary_terms.append(p)
                    else:
                        secondary_terms.append(p)

    primary_terms.sort(key=lambda x: -len(x))
    secondary_terms.sort(key=lambda x: -len(x))

    query_text = task.strip()
    keywords = primary_terms + secondary_terms
    intent = _extract_intent(task)

    # --- Step 5: Extract negative mentions ---
    negative_mentions = _extract_negative_mentions(task)

    # --- Step 6: Detect stacktrace-driven and test-driven ---
    is_stacktrace = _detect_stacktrace_driven(task)
    is_test = _detect_test_driven(task, intent)

    return ParsedTask(
        raw=task,
        intent=intent,
        primary_terms=primary_terms,
        secondary_terms=secondary_terms,
        explicit_paths=explicit_paths,
        explicit_symbols=explicit_symbols,
        keywords=keywords,
        query_text=query_text,
        negative_mentions=negative_mentions,
        is_stacktrace_driven=is_stacktrace,
        is_test_driven=is_test,
    )


# ===================================================================
# Multi-view query builders
# ===================================================================


def _build_query_views(parsed: ParsedTask) -> list[str]:
    """Build multiple embedding query texts (views) from a parsed task.

    Multi-view retrieval embeds several reformulations of the same task
    and merges results, improving recall over a single query.

    Views:
      1. **Natural-language** — raw task text (broad semantic match).
      2. **Code-style** — symbols + paths formatted as pseudo-code
         (matches embedding space of definitions).
      3. **Keyword-focused** — high-signal terms concatenated
         (targets exact-concept matches without noise).

    All views are batched into a single ``model.embed()`` call by
    :meth:`EmbeddingIndex.query_batch`, so there is no per-view
    latency overhead.
    """
    views: list[str] = [parsed.query_text]  # V1: NL view (always present)

    # V2: Code-style view — looks like the text format used at index time
    #     "kind qualified_name\nsignature\ndocstring"
    code_parts: list[str] = []
    for sym in parsed.explicit_symbols:
        code_parts.append(sym)
    for p in parsed.explicit_paths:
        code_parts.append(p)
    if parsed.primary_terms:
        code_parts.extend(parsed.primary_terms[:6])
    if code_parts:
        views.append(" ".join(code_parts))

    # V3: Keyword-focused view — only high-signal terms, no noise
    kw_parts = parsed.primary_terms[:10]
    if kw_parts and len(kw_parts) >= 2:
        views.append(" ".join(kw_parts))

    return views


def _merge_multi_view_results(
    per_view: list[list[tuple[str, float]]],
) -> list[tuple[str, float]]:
    """Merge results from multiple embedding views by max-similarity.

    For each def_uid that appears in any view's results, keeps the
    highest similarity score across views.  Returns the merged list
    sorted descending by score.
    """
    best: dict[str, float] = {}
    for view_results in per_view:
        for uid, sim in view_results:
            if uid not in best or sim > best[uid]:
                best[uid] = sim
    merged = sorted(best.items(), key=lambda x: (-x[1], x[0]))
    return merged
