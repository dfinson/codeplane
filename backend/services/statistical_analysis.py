"""Cross-job statistical analysis service.

Analyses accumulated telemetry to surface actionable cost observations:
- File reread hotspots (same file read many times across jobs)
- Tool failure patterns (high failure rates for specific tools)
- Turn cost escalation (cost/turn increases significantly late in jobs)
- Retry waste (retries that cost more than the original attempt)
- Phase imbalance (verification consuming more than reasoning)

Run periodically or after each job completion.
"""

from __future__ import annotations

import structlog
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.persistence.observations_repo import ObservationsRepo

log = structlog.get_logger()


async def run_analysis(session: AsyncSession) -> int:
    """Run all analysis passes. Returns the number of observations written."""
    repo = ObservationsRepo(session)
    count = 0
    count += await _analyse_file_rereads(session, repo)
    count += await _analyse_tool_failures(session, repo)
    count += await _analyse_turn_escalation(session, repo)
    count += await _analyse_retry_waste(session, repo)
    count += await _analyse_phase_imbalance(session, repo)
    log.info("statistical_analysis_complete", observations=count)
    return count


async def _analyse_file_rereads(session: AsyncSession, repo: ObservationsRepo) -> int:
    """Find files read excessively across jobs."""
    result = await session.execute(
        text("""
            SELECT
                file_path,
                COUNT(*) as total_reads,
                COUNT(DISTINCT job_id) as job_count,
                SUM(CASE WHEN access_type = 'read' THEN byte_count ELSE 0 END) as total_bytes
            FROM job_file_access_log
            WHERE access_type = 'read'
                AND created_at >= datetime('now', '-30 days')
            GROUP BY file_path
            HAVING COUNT(*) >= 10 AND COUNT(DISTINCT job_id) >= 3
            ORDER BY total_reads DESC
            LIMIT 20
        """)
    )
    rows = result.mappings().all()
    count = 0
    for r in rows:
        await repo.upsert(
            category="file_reread",
            severity="warning" if r["total_reads"] >= 50 else "info",
            title=f"Excessive rereads: {r['file_path']}",
            detail=(
                f"File '{r['file_path']}' was read {r['total_reads']} times "
                f"across {r['job_count']} jobs in the last 30 days."
            ),
            evidence={
                "file_path": r["file_path"],
                "total_reads": r["total_reads"],
                "job_count": r["job_count"],
                "total_bytes": r["total_bytes"],
            },
            job_count=r["job_count"],
        )
        count += 1
    return count


async def _analyse_tool_failures(session: AsyncSession, repo: ObservationsRepo) -> int:
    """Find tools with high failure rates."""
    result = await session.execute(
        text("""
            SELECT
                name,
                COUNT(*) as total_calls,
                SUM(CASE WHEN json_extract(attrs_json, '$.success') = 0
                         OR json_extract(attrs_json, '$.success') = 'false'
                    THEN 1 ELSE 0 END) as failures,
                COUNT(DISTINCT job_id) as job_count
            FROM job_telemetry_spans
            WHERE span_type = 'tool'
                AND created_at >= datetime('now', '-30 days')
            GROUP BY name
            HAVING total_calls >= 10
                AND CAST(failures AS FLOAT) / total_calls >= 0.2
            ORDER BY failures DESC
            LIMIT 20
        """)
    )
    rows = result.mappings().all()
    count = 0
    for r in rows:
        failure_rate = r["failures"] / r["total_calls"] * 100
        await repo.upsert(
            category="tool_failure",
            severity="critical" if failure_rate >= 50 else "warning",
            title=f"High failure rate: {r['name']} ({failure_rate:.0f}%)",
            detail=(
                f"Tool '{r['name']}' failed {r['failures']}/{r['total_calls']} times "
                f"({failure_rate:.1f}%) across {r['job_count']} jobs."
            ),
            evidence={
                "tool_name": r["name"],
                "total_calls": r["total_calls"],
                "failures": r["failures"],
                "failure_rate_pct": round(failure_rate, 1),
                "job_count": r["job_count"],
            },
            job_count=r["job_count"],
        )
        count += 1
    return count


async def _analyse_turn_escalation(session: AsyncSession, repo: ObservationsRepo) -> int:
    """Find jobs where cost/turn escalates significantly in the second half."""
    result = await session.execute(
        text("""
            SELECT
                job_id,
                total_turns,
                cost_first_half_usd,
                cost_second_half_usd,
                total_cost_usd
            FROM job_telemetry_summary
            WHERE total_turns >= 6
                AND cost_second_half_usd > 0
                AND cost_first_half_usd > 0
                AND (cost_second_half_usd / cost_first_half_usd) >= 2.0
                AND created_at >= datetime('now', '-30 days')
            ORDER BY (cost_second_half_usd - cost_first_half_usd) DESC
            LIMIT 20
        """)
    )
    rows = result.mappings().all()
    if len(rows) < 3:
        return 0

    total_waste = sum(
        max(0, r["cost_second_half_usd"] - r["cost_first_half_usd"])
        for r in rows
    )
    await repo.upsert(
        category="turn_escalation",
        severity="warning" if total_waste >= 1.0 else "info",
        title=f"Cost escalation in {len(rows)} jobs",
        detail=(
            f"{len(rows)} jobs had 2nd-half costs ≥2x 1st-half costs. "
            f"Estimated waste: ${total_waste:.2f}."
        ),
        evidence={
            "affected_jobs": [dict(r) for r in rows[:5]],
            "total_jobs": len(rows),
        },
        job_count=len(rows),
        total_waste_usd=total_waste,
    )
    return 1


async def _analyse_retry_waste(session: AsyncSession, repo: ObservationsRepo) -> int:
    """Find tools where retries are common and costly."""
    result = await session.execute(
        text("""
            SELECT
                name as tool_name,
                SUM(CASE WHEN is_retry = 1 THEN 1 ELSE 0 END) as retry_count,
                COUNT(*) as total_calls,
                COUNT(DISTINCT job_id) as job_count
            FROM job_telemetry_spans
            WHERE span_type = 'tool'
                AND created_at >= datetime('now', '-30 days')
            GROUP BY name
            HAVING retry_count >= 5
            ORDER BY retry_count DESC
            LIMIT 20
        """)
    )
    rows = result.mappings().all()
    count = 0
    for r in rows:
        retry_pct = r["retry_count"] / r["total_calls"] * 100
        if retry_pct < 10:
            continue
        await repo.upsert(
            category="retry_waste",
            severity="warning" if retry_pct >= 30 else "info",
            title=f"Frequent retries: {r['tool_name']} ({retry_pct:.0f}%)",
            detail=(
                f"Tool '{r['tool_name']}' was retried {r['retry_count']}/{r['total_calls']} "
                f"times ({retry_pct:.1f}%) across {r['job_count']} jobs."
            ),
            evidence={
                "tool_name": r["tool_name"],
                "retry_count": r["retry_count"],
                "total_calls": r["total_calls"],
                "retry_pct": round(retry_pct, 1),
                "job_count": r["job_count"],
            },
            job_count=r["job_count"],
        )
        count += 1
    return count


async def _analyse_phase_imbalance(session: AsyncSession, repo: ObservationsRepo) -> int:
    """Find patterns where verification cost exceeds reasoning cost."""
    result = await session.execute(
        text("""
            SELECT
                a.job_id,
                r.cost_usd as reasoning_cost,
                v.cost_usd as verification_cost,
                s.total_cost_usd
            FROM job_cost_attribution a
            JOIN job_cost_attribution r ON r.job_id = a.job_id
                AND r.dimension = 'phase' AND r.bucket = 'agent_reasoning'
            JOIN job_cost_attribution v ON v.job_id = a.job_id
                AND v.dimension = 'phase' AND v.bucket = 'verification'
            JOIN job_telemetry_summary s ON s.job_id = a.job_id
            WHERE a.dimension = 'phase'
                AND v.cost_usd > r.cost_usd
                AND a.created_at >= datetime('now', '-30 days')
            GROUP BY a.job_id
            ORDER BY (v.cost_usd - r.cost_usd) DESC
            LIMIT 20
        """)
    )
    rows = result.mappings().all()
    if len(rows) < 2:
        return 0

    total_excess = sum(
        max(0, r["verification_cost"] - r["reasoning_cost"])
        for r in rows
    )
    await repo.upsert(
        category="phase_imbalance",
        severity="warning" if total_excess >= 0.5 else "info",
        title=f"Verification > Reasoning in {len(rows)} jobs",
        detail=(
            f"{len(rows)} jobs spent more on verification than reasoning. "
            f"Excess verification cost: ${total_excess:.2f}."
        ),
        evidence={
            "affected_jobs": [dict(r) for r in rows[:5]],
            "total_jobs": len(rows),
        },
        job_count=len(rows),
        total_waste_usd=total_excess,
    )
    return 1
