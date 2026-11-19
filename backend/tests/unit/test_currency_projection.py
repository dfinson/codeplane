"""Source-qualified currency reads must not mutate historical native cost values."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from backend.models.events import new_event
from backend.models.session_event import EventMetadata
from backend.models.usage import UsageAmount
from backend.persistence.event_repo import EventRepository
from backend.persistence.telemetry_analytics_repo import TelemetryAnalyticsRepository
from backend.persistence.telemetry_summary_repo import TelemetrySummaryRepository
from backend.tests.unit.test_approval_service import session_factory as session_factory

if TYPE_CHECKING:
    from collections.abc import Awaitable


@pytest.mark.parametrize("unit,amount", [("copilot_cost_unit", "2.31"), ("USD", "1.25"), ("USD", "0"), (None, None)])
async def test_currency_rollups_require_explicit_source_usd_and_preserve_raw_totals(session_factory, unit, amount):
    async with session_factory() as session:
        summary = TelemetrySummaryRepository(session)
        await summary.init_job("job-1", sdk="copilot", model="fixture", repo="/test")
        await summary.increment("job-1", total_cost_usd=2.31)
        if unit is not None:
            usage = UsageAmount(
                amount=amount,
                unit=unit,
                currency="USD" if unit == "USD" else None,
                verified=unit == "USD",
                producer="claude" if unit == "USD" else "copilot",
                model="fixture",
                job_id="job-1",
                execution_epoch="epoch",
                role="primary",
                source="native-response",
            )
            await EventRepository(session).append(
                new_event(
                    "job-1",
                    "telemetry.usage",
                    {"cost_usd": 2.31, "native_usage": [usage.model_dump(mode="json")]},
                    metadata=EventMetadata(source_framework=usage.producer, execution_epoch="epoch"),
                )
            )
        await session.commit()
        raw_before = (await session.execute(text("SELECT total_cost_usd FROM job_telemetry_summary"))).scalar_one()
        events_before = (await session.execute(text("SELECT payload FROM events ORDER BY id"))).scalars().all()
        repo = TelemetryAnalyticsRepository(session)
        expected = float(amount) if unit == "USD" else None
        assert (await repo.aggregate())["total_cost_usd"] == expected
        assert (await repo.cost_by_day())[0]["cost"] == expected
        assert (await repo.cost_by_repo())[0]["total_cost_usd"] == expected
        assert (await repo.cost_by_model())[0]["total_cost_usd"] == expected
        assert (await repo.scorecard())["budget"][0]["total_cost_usd"] == expected
        assert (
            (await session.execute(text("SELECT total_cost_usd FROM job_telemetry_summary"))).scalar_one()
            == raw_before
            == 2.31
        )
        assert (await session.execute(text("SELECT payload FROM events ORDER BY id"))).scalars().all() == events_before


@pytest.mark.parametrize("kind", ["native", "verified", "zero", "unknown", "estimate"])
async def test_new_usage_projects_currency_and_estimates_separately(session_factory, kind):
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.services.analytics.model_pricing import ModelPricingService
    from backend.services.events.telemetry_subscriber import TelemetrySubscriber

    pending: list[Awaitable[None]] = []
    pricing = ModelPricingService(cache_path=__import__("pathlib").Path("/unused"))
    pricing._pricing = {"fixture": {"input": 1, "output": 2}}
    subscriber = TelemetrySubscriber(
        session_factory=session_factory,
        schedule_write=pending.append,
        model_pricing=pricing if kind == "estimate" else None,
    )
    unit = "USD" if kind in {"verified", "zero"} else "copilot_cost_unit"
    producer = "claude" if unit == "USD" else "copilot"
    usage = UsageAmount(
        amount="0" if kind == "zero" else "2.31",
        unit=unit,
        currency="USD" if unit == "USD" else None,
        verified=unit == "USD",
        producer=producer,
        model="fixture",
        job_id="job-1",
        execution_epoch="epoch",
        role="primary",
        source="native",
    )
    event = new_event(
        "job-1",
        "telemetry.usage",
        {
            "model": "fixture",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 999,
            "native_usage": [] if kind in {"unknown", "estimate"} else [usage.model_dump(mode="json")],
        },
        metadata=EventMetadata(source_framework=producer, execution_epoch="epoch"),
    )
    async with session_factory() as session:
        await EventRepository(session).append(event)
        await session.commit()
    await subscriber.handle_event(event)
    while pending:
        await pending.pop(0)
    async with session_factory() as session:
        expected = 0 if kind == "zero" else 2.31 if kind == "verified" else None
        summary = await TelemetrySummaryRepository(session).get_currency("job-1")
        (span,) = await TelemetrySpansRepository(session).list_for_job("job-1")
        assert summary["total_cost_usd"] == expected
        assert span["cost_usd"] == span["attrs"]["cost"] == expected
        assert summary["estimated_cost_usd"] == (pytest.approx(0.00002) if kind == "estimate" else None)
        assert span["attrs"]["estimated_cost_usd"] == summary["estimated_cost_usd"]
        assert (await TelemetrySummaryRepository(session).get("job-1"))["total_cost_usd"] != 999


async def test_mixed_currency_native_roles_and_unknown_attribution_reach_api(session_factory):
    from backend.api.analytics import (
        analytics_job_context,
        analytics_models,
        analytics_overview,
        analytics_repos,
        analytics_scorecard,
        analytics_yield,
        retry_cost_summary,
    )
    from backend.persistence.cost_attribution_repo import CostAttributionRepository
    from backend.persistence.file_access_repo import FileAccessRepository
    from backend.persistence.file_cost_repo import FileCostRepository
    from backend.persistence.job_repo import JobRepository
    from backend.persistence.latency_attribution_repo import LatencyAttributionRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.services.analytics.analytics_service import AnalyticsService
    from backend.services.analytics.telemetry_query_service import TelemetryQueryService

    async with session_factory() as session:
        summaries = TelemetrySummaryRepository(session)
        await summaries.init_job("job-1", sdk="codeplane", model="fixture", repo="/test")
        await summaries.increment("job-1", total_cost_usd=2.81)
        await summaries.init_job("job-1", sdk="copilot", session_kind="preflight")
        await summaries.increment("job-1", session_kind="preflight", total_cost_usd=8)
        await session.execute(text("UPDATE jobs SET state='completed', resolution='merged' WHERE id='job-1'"))
        for index, (unit, amount, role) in enumerate(
            [("copilot_cost_unit", "2.31", "primary"), ("USD", "0.5", "primary"), ("copilot_cost_unit", "8", "scout")]
        ):
            usage = UsageAmount(
                amount=amount,
                unit=unit,
                currency="USD" if unit == "USD" else None,
                verified=unit == "USD",
                producer="claude" if unit == "USD" else "copilot",
                model="fixture",
                job_id="job-1",
                execution_epoch="epoch",
                role=role,
                source="native",
            )
            await EventRepository(session).append(
                new_event(
                    "job-1",
                    "telemetry.usage",
                    {"native_usage": [usage.model_dump(mode="json")]},
                    event_id=f"mixed-{index}",
                    metadata=EventMetadata(source_framework=usage.producer, execution_epoch="epoch"),
                )
            )
        await CostAttributionRepository(session).insert(
            job_id="job-1", dimension="activity", bucket="reasoning", cost_usd=2.81
        )
        await FileCostRepository(session).insert_batch(
            job_id="job-1", rows=[{"file_path": "fixture.py", "cost_usd": 2.81}]
        )
        await session.commit()
        svc = AnalyticsService(session)
        overview = await analytics_overview(svc, 7)
        assert overview.total_cost_usd == 0.5
        assert {u.role for u in overview.cost_usage} == {"primary", "scout"}
        assert sum(u.amount for u in overview.cost_usage if u.unit == "copilot_cost_unit") == __import__(
            "decimal"
        ).Decimal("10.31")
        assert (await analytics_scorecard(svc, 7)).budget[0].total_cost_usd == 0.5
        assert (await analytics_models(svc, 7)).models[0].total_cost_usd == 0.5
        assert (await analytics_repos(svc, 7)).repos[0].total_cost_usd == 0.5
        assert (await analytics_job_context("job-1", svc)).job.cost == 0.5
        assert (await analytics_yield(svc, 7, None)).total_cost_usd == 0.5
        assert (await retry_cost_summary(svc, 7)).retry_cost_usd is None
        assert (await svc.sidecar_cost_breakdown(period_days=7))[0]["total_cost_usd"] is None
        assert (await svc.cost_drivers_for_job("job-1"))[0]["cost_usd"] is None
        assert (await svc.file_cost_for_job("job-1"))[0]["cost_usd"] is None
        query = TelemetryQueryService(
            CostAttributionRepository(session),
            FileAccessRepository(session),
            JobRepository(session),
            LatencyAttributionRepository(session),
            TelemetrySpansRepository(session),
            summaries,
        )
        response = await query.get_telemetry("job-1")
        assert response.total_cost == 0.5 and response.legacy_cost_unverified == 2.81
        assert response.sidecar_sessions[0].total_cost_usd is None


@pytest.mark.parametrize("unit,amount", [("copilot_cost_unit", "2.31"), ("USD", "0"), ("USD", "0.5"), (None, None)])
async def test_sidecar_in_memory_currency_uses_typed_evidence_not_legacy_number(unit, amount):
    from unittest.mock import AsyncMock

    from backend.services.adapters.agent_adapter import CompletionResult
    from backend.services.sidecar.session import SidecarSessionManager

    usage = (
        ()
        if unit is None
        else (
            UsageAmount(
                amount=amount,
                unit=unit,
                currency="USD" if unit == "USD" else None,
                verified=unit == "USD",
                producer="claude" if unit == "USD" else "copilot",
                model="fixture",
                job_id=None,
                execution_epoch=None,
                role="utility",
                source="native",
            ),
        )
    )
    adapter = AsyncMock()
    adapter.complete.return_value = CompletionResult(text="inert", cost_usd=999, usage=usage)
    manager = SidecarSessionManager(adapter, model="fixture", pool_size=0)
    try:
        await manager.open("job-1", "fixture").complete("inert")
        expected = float(amount) if unit == "USD" else None
        assert manager.get_metrics()["jobs"]["job-1"]["costUsd"] == expected
        manager.close_job("job-1")
        assert manager.get_metrics()["jobs"]["job-1"]["costUsd"] == expected
    finally:
        await manager.shutdown()


@pytest.mark.parametrize("known", [True, False])
async def test_real_attribution_is_currency_qualified_only_from_known_span_sources(session_factory, known):
    from backend.persistence.cost_attribution_repo import CostAttributionRepository
    from backend.persistence.telemetry_spans_repo import TelemetrySpansRepository
    from backend.services.analytics.cost_attribution import compute_attribution

    async with session_factory() as session:
        summary = TelemetrySummaryRepository(session)
        await summary.init_job("job-1", sdk="codeplane")
        await TelemetrySpansRepository(session).insert(
            job_id="job-1",
            span_type="llm",
            name="fixture",
            started_at=0,
            duration_ms=10,
            turn_number=1,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0 if known else 2.31,
            attrs={"cost": 0 if known else 2.31, **({"cost_provenance": "verified_usd"} if known else {})},
        )
        await compute_attribution(session, "job-1")
        rows = await CostAttributionRepository(session).for_job("job-1")
        assert rows and all(row["cost_usd"] == (0 if known else None) for row in rows)
        if known:
            await summary.increment("job-1", llm_call_count=1)
            assert all(row["cost_usd"] is None for row in await CostAttributionRepository(session).for_job("job-1"))


def test_currency_migration_preserves_historical_numeric_values():
    import importlib.util
    from pathlib import Path

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine

    path = Path(__file__).parents[3] / "alembic/versions/0082_currency_projection_provenance.py"
    spec = importlib.util.spec_from_file_location("currency_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            for table in ("job_telemetry_summary", "job_cost_attribution", "job_file_cost"):
                conn.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, total_cost_usd REAL, original BLOB)"))
                conn.execute(text(f"INSERT INTO {table} VALUES (1, 2.31, X'0001FF')"))
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
            for table in ("job_telemetry_summary", "job_cost_attribution", "job_file_cost"):
                row = conn.execute(text(f"SELECT * FROM {table}")).one()
                assert tuple(row) == (1, 2.31, b"\x00\x01\xff", 0)
    finally:
        engine.dispose()


async def test_custom_metrics_queries_use_qualified_views_including_ctes(session_factory):
    from backend.services.metrics.query_executor import execute_query

    async with session_factory() as session:
        await TelemetrySummaryRepository(session).init_job("job-1", sdk="copilot")
        await TelemetrySummaryRepository(session).increment("job-1", total_cost_usd=2.31)
        await session.commit()
    assert await execute_query(session_factory, "SELECT SUM(total_cost_usd) AS cost FROM job_telemetry_summary") == [
        {"cost": None}
    ]
    assert await execute_query(
        session_factory,
        "WITH\nselected AS (SELECT total_cost_usd FROM job_telemetry_summary) "
        "SELECT SUM(total_cost_usd) AS cost FROM selected",
    ) == [{"cost": None}]


@pytest.mark.parametrize(
    "defect", ["false-amount", "invalid-amount", "unverified", "native-currency", "foreign-job", "observer"]
)
async def test_unqualified_evidence_cannot_mint_usd_zero(session_factory, defect):
    async with session_factory() as session:
        await TelemetrySummaryRepository(session).init_job("job-1", sdk="claude")
        value = UsageAmount(
            amount="0",
            unit="USD",
            currency="USD",
            verified=True,
            producer="claude",
            job_id="job-1",
            execution_epoch="epoch",
            model=None,
            role="primary",
            source="native",
        ).model_dump(mode="json")
        if defect == "false-amount":
            value["amount"] = False
        elif defect == "invalid-amount":
            value["amount"] = "not-a-number"
        elif defect == "unverified":
            value["verified"] = False
        elif defect == "native-currency":
            value["unit"] = "copilot_cost_unit"
        elif defect == "foreign-job":
            value["job_id"] = "foreign"
        await EventRepository(session).append(
            new_event(
                "job-1",
                "telemetry.usage",
                {"native_usage": [value]},
                metadata=EventMetadata(
                    source_framework="claude",
                    execution_epoch="epoch",
                    namespace=("observation-source",) if defect == "observer" else (),
                ),
            )
        )
        assert (await TelemetryAnalyticsRepository(session).aggregate())["total_cost_usd"] is None
