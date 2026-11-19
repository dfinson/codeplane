"""Read-only currency views over existing canonical evidence and telemetry projections.

Legacy numeric columns are never proof of a currency. No rows are rewritten and
no accounting ledger is created. The connection's deterministic read function
uses the same strict source qualifier as Python telemetry and costUsage.
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
    if not re.match(r"(?:SELECT|WITH)\b", query, re.IGNORECASE):
        raise ValueError("Currency projection only accepts read queries")
    summary = _columns(
        JobTelemetrySummaryRow,
        "s",
        {
            "total_cost_usd": "u.usd",
            "subagent_cost_usd": """(SELECT SUM(codeplane_span_usd(p.job_id,p.session_kind,p.attrs_json))
            FROM main.job_telemetry_spans p
            WHERE p.job_id=s.job_id AND p.session_kind=s.session_kind
              AND json_extract(p.attrs_json, '$.is_subagent')=1)""",
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
            "cost_usd": "codeplane_span_usd(s.job_id,s.session_kind,s.attrs_json)",
            "attrs_json": """CASE WHEN s.span_type = 'llm' THEN json_set(s.attrs_json, '$.cost',
            codeplane_span_usd(s.job_id,s.session_kind,s.attrs_json))
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
    receipt_values = {
        "job_id": "u.job_id",
        "session_kind": "u.session_kind",
        "sdk": "u.producer",
        "model": "COALESCE(u.model, '')",
        "repo": "COALESCE(j.repo, '')",
        "branch": "COALESCE(j.branch, '')",
        "status": "j.state",
        "created_at": "u.first_seen",
        "updated_at": "u.last_seen",
        "completed_at": "j.completed_at",
        "total_cost_usd": "NULL",
        "llm_call_count": "u.calls",
        **{
            unit: f"COALESCE(u.{unit}, 0)"
            for unit in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
        },
    }
    receipt_columns = ", ".join(
        f"{receipt_values.get(column.name, 'NULL' if column.nullable else '0')} AS {column.name}"
        for column in JobTelemetrySummaryRow.__table__.columns
    )
    return text(f"""
        WITH usage_entries AS (
            SELECT e.job_id, e.event_id, e.timestamp,
                   CASE json_extract(j.value, '$.role')
                     WHEN 'primary' THEN 'job' WHEN 'scout' THEN 'preflight'
                     ELSE json_extract(j.value, '$.role') END AS session_kind,
                   j.value AS value
            FROM events e, json_each(codeplane_currency_usage(e.kind,e.job_id,e.payload,e.event_metadata)) j
            WHERE e.kind IN ('telemetry.usage','execution.native.usage','utility.completion.completed')
        ),
        verified_cost AS (
            SELECT job_id, session_kind, MIN(timestamp) AS first_seen, MAX(timestamp) AS last_seen,
                MIN(json_extract(value,'$.producer')) AS producer, MIN(json_extract(value,'$.model')) AS model,
                COUNT(DISTINCT event_id) AS calls,
                SUM(CASE WHEN json_extract(value,'$.unit')='input_tokens'
                    THEN json_extract(value,'$.amount') END) AS input_tokens,
                SUM(CASE WHEN json_extract(value,'$.unit')='output_tokens'
                    THEN json_extract(value,'$.amount') END) AS output_tokens,
                SUM(CASE WHEN json_extract(value,'$.unit')='cache_read_tokens'
                    THEN json_extract(value,'$.amount') END) AS cache_read_tokens,
                SUM(CASE WHEN json_extract(value,'$.unit')='cache_write_tokens'
                    THEN json_extract(value,'$.amount') END) AS cache_write_tokens,
                SUM(CASE WHEN json_extract(value,'$.unit')='USD'
                    THEN CAST(json_extract(value, '$.amount') AS REAL) END) AS usd
            FROM usage_entries
            GROUP BY job_id, session_kind
        ),
        source_summary AS (
            SELECT {_columns(JobTelemetrySummaryRow, "existing", {})}
            FROM main.job_telemetry_summary existing
            UNION ALL
            SELECT {receipt_columns} FROM verified_cost u JOIN main.jobs j ON j.id=u.job_id
            WHERE NOT EXISTS (SELECT 1 FROM main.job_telemetry_summary t
                              WHERE t.job_id=u.job_id AND t.session_kind=u.session_kind)
        ),
        job_telemetry_summary AS (
            SELECT {summary},
                (SELECT SUM(json_extract(p.attrs_json, '$.estimated_cost_usd'))
                 FROM main.job_telemetry_spans p
                 WHERE p.job_id=s.job_id AND p.session_kind=s.session_kind
                   AND json_extract(p.attrs_json, '$.cost_provenance')='estimate') AS estimated_cost_usd,
                s.total_cost_usd AS legacy_cost_unverified
            FROM source_summary s
            LEFT JOIN verified_cost u ON u.job_id = s.job_id AND u.session_kind = s.session_kind
        ),
        job_telemetry_spans AS (SELECT {spans} FROM main.job_telemetry_spans s),
        job_cost_attribution AS (SELECT {attribution} FROM main.job_cost_attribution a),
        job_file_cost AS (SELECT {files} FROM main.job_file_cost a)
        SELECT * FROM ({query})
    """)
