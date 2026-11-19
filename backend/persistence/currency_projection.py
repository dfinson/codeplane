"""Read-only currency views over existing canonical evidence and telemetry projections.

Legacy numeric columns are never proof of a currency. No rows are rewritten and
no accounting ledger is created. SQLite JSON checks mirror UsageAmount's USD
invariant before an amount participates in a monetary rollup.
"""

import re

from sqlalchemy import text
from sqlalchemy.sql.elements import TextClause

from backend.models.db import Base, CostAttributionRow, JobFileCostRow, JobTelemetrySpanRow, JobTelemetrySummaryRow


def _columns(model: type[Base], alias: str, replacements: dict[str, str]) -> str:
    return ", ".join(
        f"{replacements.get(column.name, f'{alias}.{column.name}')} AS {column.name}"
        for column in model.__table__.columns
    )


def currency_query(query: str) -> TextClause:
    """Project only SELECT statements; writes continue to use original tables."""
    query = query.lstrip()
    prefix = "WITH"
    cte = re.match(r"WITH\s+(RECURSIVE\s+)?", query, re.IGNORECASE)
    if cte:
        prefix = "WITH RECURSIVE" if cte.group(1) else "WITH"
        query = ", " + query[cte.end() :]
    elif not query.upper().startswith("SELECT"):
        raise ValueError("Currency projection only accepts read queries")
    summary = _columns(
        JobTelemetrySummaryRow,
        "s",
        {
            "total_cost_usd": "u.usd",
            "subagent_cost_usd": """(SELECT SUM(p.cost_usd) FROM main.job_telemetry_spans p
            WHERE p.job_id=s.job_id AND p.session_kind=s.session_kind
              AND json_extract(p.attrs_json, '$.is_subagent')=1
              AND json_extract(p.attrs_json, '$.cost_provenance')='verified_usd')""",
            **{
                field: f"CASE WHEN s.derived_currency_qualified = 1 THEN s.{field} END"
                for field in (
                    "retry_cost_usd",
                    "peak_turn_cost_usd",
                    "avg_turn_cost_usd",
                    "cost_first_half_usd",
                    "cost_second_half_usd",
                )
            },
        },
    )
    spans = _columns(
        JobTelemetrySpanRow,
        "s",
        {
            "cost_usd": """CASE WHEN json_extract(s.attrs_json, '$.cost_provenance') = 'verified_usd'
                            THEN s.cost_usd END""",
            "attrs_json": """CASE WHEN s.span_type = 'llm' THEN json_set(s.attrs_json, '$.cost',
            CASE WHEN json_extract(s.attrs_json, '$.cost_provenance') = 'verified_usd' THEN s.cost_usd END)
            ELSE s.attrs_json END""",
        },
    )
    derived = """a.currency_qualified = 1 AND EXISTS (
        SELECT 1 FROM main.job_telemetry_summary s
        WHERE s.job_id=a.job_id AND s.session_kind='job' AND s.derived_currency_qualified=1)"""
    attribution = _columns(
        CostAttributionRow,
        "a",
        {
            "cost_usd": f"CASE WHEN {derived} THEN a.cost_usd END",
        },
    )
    files = _columns(
        JobFileCostRow,
        "a",
        {field: f"CASE WHEN {derived} THEN a.{field} END" for field in ("cost_usd", "read_cost", "write_cost")},
    )
    return text(f"""
        {prefix} usage_entries AS (
            SELECT e.job_id,
                   CASE json_extract(j.value, '$.role')
                     WHEN 'primary' THEN 'job' WHEN 'scout' THEN 'preflight'
                     ELSE json_extract(j.value, '$.role') END AS session_kind,
                   j.value AS value
            FROM events e, json_each(
                CASE WHEN e.kind = 'telemetry.usage' THEN
                    CASE WHEN json_type(e.payload, '$.native_usage') = 'array'
                         THEN json_extract(e.payload, '$.native_usage') ELSE '[]' END
                ELSE CASE WHEN json_type(e.payload, '$.usage') = 'array'
                         THEN json_extract(e.payload, '$.usage') ELSE '[]' END END
            ) j
            WHERE j.type = 'object' AND (
                (e.kind = 'telemetry.usage'
                 AND json_extract(e.event_metadata, '$.source_framework') IN ('copilot', 'claude')
                 AND json_extract(j.value, '$.producer') = json_extract(e.event_metadata, '$.source_framework'))
                OR (e.kind IN ('execution.native.usage', 'utility.completion.completed')
                    AND json_extract(e.event_metadata, '$.source_framework') = 'codeplane'
                    AND json_extract(e.event_metadata, '$.namespace[0]') = 'control'
                    AND json_extract(e.event_metadata, '$.namespace[1]') IN ('native-execution', 'utility'))
            )
              AND COALESCE(json_extract(e.event_metadata, '$.namespace[0]'), '') != 'observation-source'
              AND json_extract(j.value, '$.job_id') = e.job_id
              AND json_extract(j.value, '$.execution_epoch') IS json_extract(e.event_metadata, '$.execution_epoch')
              AND json_type(j.value, '$.verified') = 'true'
              AND json_extract(j.value, '$.unit') = 'USD'
              AND json_extract(j.value, '$.currency') = 'USD'
              AND json_type(j.value, '$.amount') IN ('text', 'integer', 'real')
              AND json_extract(j.value, '$.role') IN ('primary','scout','monitor','assessment','title','utility')
              AND json_type(j.value, '$.producer') = 'text'
              AND length(json_extract(j.value, '$.producer')) > 0
              AND json_type(j.value, '$.source') = 'text'
              AND length(json_extract(j.value, '$.source')) > 0
              AND json_valid('[' || json_extract(j.value, '$.amount') || ']')
        ),
        verified_cost AS (
            SELECT job_id, session_kind, SUM(CAST(json_extract(value, '$.amount') AS REAL)) AS usd
            FROM usage_entries
            WHERE json_type('[' || json_extract(value, '$.amount') || ']', '$[0]') IN ('real','integer')
              AND json_array_length('[' || json_extract(value, '$.amount') || ']') = 1
              AND CAST(json_extract(value, '$.amount') AS REAL) >= 0
              AND CAST(json_extract(value, '$.amount') AS REAL) < 1e308
            GROUP BY job_id, session_kind
        ),
        job_telemetry_summary AS (
            SELECT {summary},
                (SELECT SUM(json_extract(p.attrs_json, '$.estimated_cost_usd'))
                 FROM main.job_telemetry_spans p
                 WHERE p.job_id=s.job_id AND p.session_kind=s.session_kind
                   AND json_extract(p.attrs_json, '$.cost_provenance')='estimate') AS estimated_cost_usd,
                s.total_cost_usd AS legacy_cost_unverified
            FROM main.job_telemetry_summary s
            LEFT JOIN verified_cost u ON u.job_id = s.job_id AND u.session_kind = s.session_kind
        ),
        job_telemetry_spans AS (SELECT {spans} FROM main.job_telemetry_spans s),
        job_cost_attribution AS (SELECT {attribution} FROM main.job_cost_attribution a),
        job_file_cost AS (SELECT {files} FROM main.job_file_cost a)
        {query}
    """)
