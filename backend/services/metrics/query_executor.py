"""SQL query executor for the metrics chat system.

Validates, rewrites, and executes read-only SQL queries against the
CodePlane telemetry tables with a configurable timeout.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import structlog

from backend.persistence.currency_projection import currency_query

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = structlog.get_logger()

# Tables that user-generated queries may reference.
ALLOWED_TABLES = frozenset(
    {
        "job_telemetry_spans",
        "job_telemetry_summary",
        "job_cost_attribution",
        "jobs",
    }
)

# Defense-in-depth: table names that must never appear anywhere in a query.
# Catches bypass techniques (comma joins, quoted identifiers, subqueries)
# that the structural regex might miss.
_DENIED_TABLES = frozenset(
    {
        "sqlite_master",
        "sqlite_schema",
        "sqlite_temp_master",
        "sqlite_temp_schema",
        "sqlite_sequence",
        "custom_metrics",
        "metrics_chat_messages",
        "cost_observations",
        "approvals",
        "alembic_version",
        "sidecar_templates",
    }
)

# SQL keywords that must not appear in user queries.  Checked against the
# normalised (upper-cased, single-spaced) query text.
_FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(kw, re.IGNORECASE)
    for kw in [
        r"\bINSERT\b",
        r"\bUPDATE\b",
        r"\bDELETE\b",
        r"\bDROP\b",
        r"\bALTER\b",
        r"\bCREATE\b",
        r"\bATTACH\b",
        r"\bDETACH\b",
        r"\bPRAGMA\b",
        r"\bREINDEX\b",
        r"\bVACUUM\b",
        r"\bGRANT\b",
        r"\bREVOKE\b",
        r"\bBEGIN\b",
        r"\bCOMMIT\b",
        r"\bROLLBACK\b",
        r"\bSAVEPOINT\b",
        r"\bRELEASE\b",
    ]
]

# Match table references after FROM/JOIN — handles unquoted, double-quoted,
# backtick-quoted, and bracket-quoted identifiers.
_TABLE_REF_RE = re.compile(
    r"(?:FROM|JOIN)\s+"
    r'(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([a-zA-Z_]\w*))',
    re.IGNORECASE,
)

# Match additional comma-separated table references in FROM clauses.
# Applied to the substring following each FROM match.
_COMMA_TABLE_RE = re.compile(
    r',\s*(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([a-zA-Z_]\w*))',
    re.IGNORECASE,
)

# Extract CTE alias names from WITH clauses so they don't trigger
# the allowed-table check.  Handles optional column lists:
#   WITH cte AS (...)  and  WITH cte(a, b) AS (...)
_CTE_ALIAS_RE = re.compile(
    r"(?:\bWITH\b(?:\s+RECURSIVE)?\s+|,\s*)([a-zA-Z_]\w*)(?:\s*\([^)]*\))?\s+AS\s*\(",
    re.IGNORECASE,
)


class QueryValidationError(Exception):
    """Raised when a query fails validation."""


# Unicode characters that LLMs sometimes emit instead of ASCII SQL operators.
_UNICODE_REPLACEMENTS: dict[str, str] = {
    "\u2265": ">=",  # ≥
    "\u2264": "<=",  # ≤
    "\u2260": "!=",  # ≠
    "\u00d7": "*",  # ×
    "\u00f7": "/",  # ÷
    "\u2212": "-",  # − (minus sign)
    "\u2018": "'",  # '
    "\u2019": "'",  # '
    "\u201c": '"',  # “
    "\u201d": '"',  # ”
}


def _normalize_unicode(sql: str) -> str:
    """Replace common Unicode operators with their ASCII equivalents."""
    for uc, ascii_eq in _UNICODE_REPLACEMENTS.items():
        sql = sql.replace(uc, ascii_eq)
    return sql


def _extract_table_names(match: re.Match[str]) -> str:
    """Return the table name from a regex match with multiple groups."""
    return next((g for g in match.groups() if g is not None), "")


def validate_query(sql: str) -> str:
    """Validate and return a cleaned SQL string.

    Raises ``QueryValidationError`` for disallowed operations.
    """
    cleaned = _normalize_unicode(sql.strip().rstrip(";"))
    if not cleaned:
        raise QueryValidationError("Empty query")

    # Must start with SELECT or WITH
    first_word = cleaned.split()[0].upper()
    if first_word not in ("SELECT", "WITH"):
        raise QueryValidationError(f"Only SELECT/WITH queries are allowed, got {first_word}")

    # Forbidden keywords
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(cleaned):
            raise QueryValidationError(f"Forbidden SQL keyword detected: {pattern.pattern}")

    # Defense in depth: denied table names as substring check.
    # Catches comma-join bypasses, quoted identifiers, and any other
    # creative table reference technique.
    # Strip string literals first to avoid false positives on values
    # like WHERE name = 'approvals needed'.
    lower_sql = cleaned.lower()
    stripped_sql = re.sub(r"'[^']*'", "''", lower_sql)
    for denied in _DENIED_TABLES:
        if denied in stripped_sql:
            raise QueryValidationError(f"Reference to '{denied}' is not allowed")

    # Extract CTE aliases so they pass the allowed-table check
    cte_aliases = {m.group(1).lower() for m in _CTE_ALIAS_RE.finditer(cleaned)}
    allowed_with_ctes = ALLOWED_TABLES | cte_aliases

    # Check structural table references (FROM/JOIN and comma-separated)
    for match in _TABLE_REF_RE.finditer(cleaned):
        table = _extract_table_names(match).lower()
        if table and table not in allowed_with_ctes:
            raise QueryValidationError(f"Table '{table}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_TABLES))}")
        # Check for comma-separated tables following this FROM/JOIN
        rest = cleaned[match.end() :]
        for cmatch in _COMMA_TABLE_RE.finditer(rest):
            ctable = _extract_table_names(cmatch).lower()
            if ctable and ctable not in allowed_with_ctes:
                raise QueryValidationError(
                    f"Table '{ctable}' is not allowed. Allowed: {', '.join(sorted(ALLOWED_TABLES))}"
                )
            # Stop at the first non-comma token (avoid matching commas in
            # SELECT lists further down)
            if cmatch.start() > 0:
                between = rest[: cmatch.start()].strip()
                if between and not between.endswith(","):
                    break

    return cleaned


async def execute_query(
    session_factory: async_sessionmaker[AsyncSession],
    sql: str,
    *,
    timeout_seconds: float = 30.0,
) -> list[dict[str, Any]]:
    """Execute a validated read-only query and return rows as dicts.

    Uses a fresh session (not the request session) so the read-only query
    doesn't interfere with the request lifecycle.
    """
    import asyncio

    validated = validate_query(sql)

    async def _run() -> list[dict[str, Any]]:
        async with session_factory() as session:
            result = await session.execute(currency_query(validated))
            columns = list(result.keys())
            rows = result.fetchall()
            return [dict(zip(columns, row, strict=False)) for row in rows]

    try:
        return await asyncio.wait_for(_run(), timeout=timeout_seconds)
    except TimeoutError:
        log.warning("metrics_query_timeout", sql=validated[:200])
        raise QueryValidationError(f"Query timed out after {timeout_seconds}s. Try simplifying the query.") from None
    except Exception as exc:
        if isinstance(exc, QueryValidationError):
            raise
        log.warning("metrics_query_error", error=str(exc), sql=validated[:200])
        raise QueryValidationError(f"Query execution error: {exc}") from exc
