"""Tests for the idempotent re-enrichment path (reenrich.py)."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.events import EventKind, new_event
from backend.services.events.reenrich import (
    _REENRICH_MARKER_KIND,
    _job_locks,
    _pair_key,
    _StartBaselineTracker,
    reenrich_job_events,
)


@pytest.fixture(autouse=True)
def _clear_locks():
    """Clear per-job locks between tests."""
    _job_locks.clear()
    yield
    _job_locks.clear()


@pytest.fixture
def mock_session_factory():
    """Create a mock async session factory that returns an async context manager."""
    session = AsyncMock()

    # Make the factory return an async context manager
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = ctx
    return factory


class TestRuntimeContract:
    async def test_partial_failure_does_not_mark_replay_complete(self, mock_session_factory):
        with (
            patch("backend.persistence.event_repo.EventRepository") as repo_cls,
            patch("backend.services.events.reenrich._make_legacy_enricher") as enricher_cls,
            patch("backend.services.events.reenrich._append_marker", new_callable=AsyncMock) as marker,
        ):
            repo = repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(
                return_value=[new_event("j1", EventKind.message_assistant, {"content": "fixture"})]
            )
            enricher_cls.return_value.process.side_effect = ValueError("fixture enrichment failure")
            enricher_cls.return_value.flush.return_value = []
            with contextlib.suppress(RuntimeError):
                await reenrich_job_events("j1", mock_session_factory)
            assert marker.await_count == 0


class TestReenrichIdempotency:
    @pytest.mark.asyncio
    async def test_skips_when_marker_exists(self, mock_session_factory):
        """Re-enrichment is skipped when the marker event already exists."""
        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(
                return_value=[
                    new_event(
                        session_id="j1",
                        kind=_REENRICH_MARKER_KIND,
                        payload={"updated_count": 5},
                    )
                ]
            )

            result = await reenrich_job_events("j1", mock_session_factory)
            assert result == 0

    @pytest.mark.asyncio
    async def test_force_deletes_and_reinserts_marker(self, mock_session_factory):
        """force=True deletes old marker and inserts a fresh one with correct count."""
        marker = new_event(
            session_id="j1",
            kind=_REENRICH_MARKER_KIND,
            payload={"updated_count": 5},
        )
        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[marker])
            repo.list_all_events_by_job = AsyncMock(return_value=[])
            repo.delete_event = AsyncMock()

            result = await reenrich_job_events("j1", mock_session_factory, force=True)
            assert result == 0
            # Should delete existing marker
            repo.delete_event.assert_called_once_with(marker.id)

    @pytest.mark.asyncio
    async def test_no_events_returns_zero(self, mock_session_factory):
        """Empty job returns 0."""
        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(return_value=[])

            result = await reenrich_job_events("j1", mock_session_factory)
            assert result == 0


class TestReenrichConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_calls_do_not_double_replay(self, mock_session_factory):
        """Second concurrent call returns 0 immediately (lock held)."""
        # Simulate a slow reenrich by holding the lock manually
        from backend.services.events.reenrich import _get_job_lock

        lock = _get_job_lock("j1")
        await lock.acquire()

        # Second call should detect lock is held and return 0 immediately
        result = await reenrich_job_events("j1", mock_session_factory)
        assert result == 0

        lock.release()

    @pytest.mark.asyncio
    async def test_lock_cleaned_up_after_completion(self, mock_session_factory):
        """Per-job lock is removed from _job_locks after reenrich completes."""
        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(return_value=[])

            await reenrich_job_events("j1", mock_session_factory)
            # Lock should be cleaned up — no unbounded accumulation
            assert "j1" not in _job_locks

    @pytest.mark.asyncio
    async def test_marker_events_excluded_from_replay(self, mock_session_factory):
        """Marker events in the stream are skipped during re-enrichment."""
        marker_event = new_event(
            session_id="j1",
            kind=_REENRICH_MARKER_KIND,
            payload={"updated_count": 3},
        )
        normal_event = new_event(
            session_id="j1",
            kind=EventKind.message_user,
            payload={"content": "hello"},
        )

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            # Return both marker and normal events
            repo.list_all_events_by_job = AsyncMock(return_value=[marker_event, normal_event])
            repo.update_metadata = AsyncMock()

            with patch("backend.services.events.reenrich._make_legacy_enricher") as mock_enricher_cls:
                enricher = mock_enricher_cls.return_value
                enricher.process = MagicMock(return_value=None)
                enricher.flush = MagicMock(return_value=[])

                await reenrich_job_events("j1", mock_session_factory)

                # Enricher.process should only be called with the normal event
                # (marker is skipped)
                enricher.process.assert_called_once_with(normal_event)


class TestReenrichBatching:
    @pytest.mark.asyncio
    async def test_multi_batch_processing(self, mock_session_factory):
        """Events are loaded in batches — verifies pagination works."""
        from backend.services.events.reenrich import _BATCH_SIZE

        # Create events for 2 batches
        events_batch_1 = [
            new_event(session_id="j1", kind=EventKind.message_user, payload={"content": f"msg-{i}"})
            for i in range(_BATCH_SIZE)
        ]
        events_batch_2 = [
            new_event(session_id="j1", kind=EventKind.message_user, payload={"content": f"msg-{i}"}) for i in range(10)
        ]

        call_count = 0

        async def _mock_list_all(job_id, *, limit=None, offset=0):
            nonlocal call_count
            call_count += 1
            if offset == 0:
                return events_batch_1
            elif offset == _BATCH_SIZE:
                return events_batch_2
            return []

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=_mock_list_all)
            repo.update_metadata = AsyncMock()

            with patch("backend.services.events.reenrich._make_legacy_enricher") as mock_enricher_cls:
                enricher = mock_enricher_cls.return_value
                enricher.process = MagicMock(return_value=None)
                enricher.flush = MagicMock(return_value=[])

                await reenrich_job_events("j1", mock_session_factory)

                # Should have called list_all_events_by_job at least 2 times
                # (batch 1, batch 2, and possibly a final empty check)
                assert call_count >= 2
                # Enricher.process should have been called for all events
                assert enricher.process.call_count == _BATCH_SIZE + 10


class _InPlaceMutatingEnricher:
    """Faithful stand-in for a hypothetical TraceForge that enriches by mutating
    the event's metadata IN PLACE and returning the SAME event object.

    This aliases the input and the result (``result is event`` and
    ``result.metadata is event.metadata``), which is exactly the shape that
    defeats a naive live ``e.metadata != event.metadata`` comparison — the two
    sides are the same object, so ``!=`` is always False and the write is
    silently skipped. reenrich must instead compare against a snapshot captured
    *before* ``process`` and still persist the change.
    """

    def __init__(self, *args, **kwargs) -> None:
        self.processed: list[str] = []

    def process(self, event):  # noqa: ANN001, ANN201
        self.processed.append(event.id)
        # Enrich by replacing metadata on the SAME event object (in place).
        enriched_md = event.metadata.model_copy(update={"tool_display": "shell"})
        object.__setattr__(event, "metadata", enriched_md)
        return event

    def flush(self):  # noqa: ANN201
        return []


class TestReenrichMutationSafety:
    """Point 2: the change check is robust to in-place metadata mutation and the
    updated/marker count stays accurate."""

    @pytest.mark.asyncio
    async def test_in_place_mutation_still_persists_metadata(self, mock_session_factory):
        """An in-place-mutating enricher must NOT cause a silently-skipped write.

        Proves the snapshot-before-process fix: metadata is persisted and the
        returned updated count (which is written into the marker) is correct.
        """
        event = new_event(
            session_id="j1",
            kind=EventKind.tool_call_completed,
            payload={"tool_name": "bash", "tool_call_id": "tc-1"},
        )
        # Precondition: the original metadata has no tool_display, so enrichment
        # (tool_display="shell") is a genuine, detectable change.
        assert event.metadata.tool_display is None

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=[[event], []])
            repo.update_metadata = AsyncMock()

            with patch("backend.services.events.reenrich._make_legacy_enricher", _InPlaceMutatingEnricher):
                updated = await reenrich_job_events("j1", mock_session_factory)

            # Metadata was persisted despite input/result aliasing.
            repo.update_metadata.assert_awaited_once()
            _, kwargs = repo.update_metadata.await_args
            assert kwargs["event_id"] == event.id
            assert kwargs["metadata"].tool_display == "shell"
            # Marker/return count reflects exactly one write.
            assert updated == 1

    @pytest.mark.asyncio
    async def test_no_change_writes_nothing(self, mock_session_factory):
        """When enrichment yields no net metadata change, no write occurs and the
        count stays zero (avoid unnecessary writes)."""

        class _NoOpEnricher:
            def __init__(self, *a, **k) -> None:
                pass

            def process(self, event):  # noqa: ANN001, ANN201
                # Return the event unchanged (no metadata delta).
                return event

            def flush(self):  # noqa: ANN201
                return []

        event = new_event(session_id="j1", kind=EventKind.message_user, payload={"content": "hi"})

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=[[event], []])
            repo.update_metadata = AsyncMock()

            with patch("backend.services.events.reenrich._make_legacy_enricher", _NoOpEnricher):
                updated = await reenrich_job_events("j1", mock_session_factory)

            repo.update_metadata.assert_not_awaited()
            assert updated == 0

    @pytest.mark.asyncio
    async def test_real_enricher_persists_enriched_metadata(self, mock_session_factory):
        """End-to-end with the REAL TF 0.1.5 enricher: a classifiable tool-start is
        buffered then flushed as an enriched orphan whose metadata is persisted."""
        event = new_event(
            session_id="j1",
            kind=EventKind.tool_call_started,
            payload={"tool_name": "bash", "tool_call_id": "tc-1", "arguments": '{"command": "ls -la"}'},
        )
        assert event.metadata.classification is None  # not yet enriched

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=[[event], []])
            repo.update_metadata = AsyncMock()

            # No TFEnricher patch — uses the real enricher.
            updated = await reenrich_job_events("j1", mock_session_factory)

        repo.update_metadata.assert_awaited()
        _, kwargs = repo.update_metadata.await_args
        assert kwargs["event_id"] == event.id
        assert kwargs["metadata"].classification is not None
        assert updated >= 1


def _tool_start(tcid: str, sid: str = "j1"):
    return new_event(
        session_id=sid,
        kind=EventKind.tool_call_started,
        payload={"tool_call_id": tcid, "tool_name": "bash"},
    )


def _tool_completion(tcid: str, sid: str = "j1"):
    return new_event(
        session_id=sid,
        kind=EventKind.tool_call_completed,
        payload={"tool_call_id": tcid, "tool_name": "bash", "success": True},
    )


class TestStartBaselineTracker:
    """Unit tests for the bounded start-baseline tracker (memory-leak fix)."""

    def test_paired_starts_do_not_accumulate(self):
        """Resolving each start with its completion retires it — the store never
        grows with the number of paired calls."""
        tracker = _StartBaselineTracker()
        for i in range(200):
            start = _tool_start(f"tc-{i}")
            tracker.record_start(start, f"base-{i}")
            tracker.retire_completion(_tool_completion(f"tc-{i}"))
        # 200 paired calls, but nothing retained and the peak never exceeded the
        # single transiently-buffered start.
        assert len(tracker) == 0
        assert tracker.peak_size == 1

    def test_unpaired_starts_retained_until_emitted(self):
        """Unpaired starts stay retrievable for orphan comparison while paired
        traffic flows through; peak stays bounded by concurrent unpaired count."""
        tracker = _StartBaselineTracker()
        u1, u2 = _tool_start("tc-a"), _tool_start("tc-b")
        tracker.record_start(u1, "A")
        tracker.record_start(u2, "B")
        for i in range(50):
            tracker.record_start(_tool_start(f"p-{i}"), f"b{i}")
            tracker.retire_completion(_tool_completion(f"p-{i}"))
        # The two unpaired starts remain available with their own baselines.
        assert tracker.baseline_for(u1) == "A"
        assert tracker.baseline_for(u2) == "B"
        assert len(tracker) == 2
        # 2 unpaired + at most 1 transient paired — NOT ~52.
        assert tracker.peak_size <= 3
        tracker.retire_emitted(u1)
        tracker.retire_emitted(u2)
        assert len(tracker) == 0

    def test_displaced_start_keeps_own_baseline(self):
        """Two starts sharing a tool_call_id: the displaced one keeps its own
        baseline and is retired independently of the newer buffered start."""
        tracker = _StartBaselineTracker()
        a = _tool_start("dup")
        b = _tool_start("dup")  # same tool_call_id, distinct event id
        assert _pair_key(a) == _pair_key(b)  # they collide on TF's pair key
        assert a.id != b.id

        tracker.record_start(a, "origA")
        tracker.record_start(b, "origB")  # displaces the pair index a -> b
        assert tracker.baseline_for(a) == "origA"
        assert tracker.baseline_for(b) == "origB"

        # 'a' is emitted as a displaced orphan; retiring it must not drop 'b',
        # which the pair index now points at.
        tracker.retire_emitted(a)
        assert tracker.baseline_for(a) is None
        assert tracker.baseline_for(b) == "origB"

        # 'b' is later resolved by its completion.
        tracker.retire_completion(_tool_completion("dup"))
        assert tracker.baseline_for(b) is None
        assert len(tracker) == 0


class TestPairKey:
    """`_pair_key` mirrors TF 0.1.5's `_extract_tool_call_id`: a pair key exists
    only for a non-empty **string** ``tool_call_id`` (missing/empty/non-string
    are unpaired)."""

    def test_valid_string_id_yields_pair_key(self):
        ev = _tool_start("tc-1", sid="jX")
        assert _pair_key(ev) == ("jX", "tc-1")

    def test_empty_string_id_yields_no_pair_key(self):
        ev = new_event(
            session_id="j1",
            kind=EventKind.tool_call_started,
            payload={"tool_call_id": "", "tool_name": "bash"},
        )
        assert _pair_key(ev) is None

    def test_missing_id_yields_no_pair_key(self):
        ev = new_event(
            session_id="j1",
            kind=EventKind.tool_call_started,
            payload={"tool_name": "bash"},
        )
        assert _pair_key(ev) is None

    def test_non_string_id_yields_no_pair_key(self):
        for bad in (123, 12.5, True, ["tc"], {"id": "tc"}, None):
            ev = new_event(
                session_id="j1",
                kind=EventKind.tool_call_started,
                payload={"tool_call_id": bad, "tool_name": "bash"},
            )
            assert _pair_key(ev) is None, f"expected no pair key for {bad!r}"

    def test_empty_payload_yields_no_pair_key(self):
        ev = new_event(session_id="j1", kind=EventKind.tool_call_started, payload={})
        assert _pair_key(ev) is None


class _FakePairingEnricher:
    """Simulates TF buffering/pairing without the real model: a start with a
    valid tool_call_id buffers (returns None); its completion drops the paired
    start and returns the enriched completion (same id); a start with NO valid
    tool_call_id cannot be paired, so — exactly like TF — it is emitted
    immediately, enriched, under its own id; flush() emits the still-unpaired
    buffered starts as enriched orphans (same id). Enrichment adds tool_display
    so metadata genuinely differs."""

    def __init__(self, *args, **kwargs) -> None:
        self._pending: dict[tuple[str, str], object] = {}

    @staticmethod
    def _enrich(event):
        md = event.metadata.model_copy(update={"tool_display": "shell"})
        return event.model_copy(update={"metadata": md})

    def process(self, event):  # noqa: ANN001, ANN201
        if event.kind == EventKind.tool_call_started:
            tcid = (event.payload or {}).get("tool_call_id")
            if not isinstance(tcid, str) or not tcid:
                # No valid tool_call_id — not buffered; emitted immediately.
                return self._enrich(event)
            self._pending[(event.session_id, tcid)] = event
            return None
        if event.kind == EventKind.tool_call_completed:
            tcid = (event.payload or {}).get("tool_call_id")
            self._pending.pop((event.session_id, tcid), None)  # consume paired start
            return self._enrich(event)
        return event

    def flush(self):  # noqa: ANN201
        orphans = [self._enrich(ev) for ev in self._pending.values()]
        self._pending.clear()
        return orphans


class TestReenrichPairingBounded:
    """Point: paired starts must not accumulate across batches, while unpaired
    starts remain available for correct per-orphan baseline comparison."""

    @pytest.mark.asyncio
    async def test_paired_starts_bounded_across_batches(self, mock_session_factory):
        import backend.services.events.reenrich as reenrich_mod

        # 2 truly-unpaired starts, then 20 interleaved start/completion pairs.
        u1, u2 = _tool_start("u1"), _tool_start("u2")
        events = [u1, u2]
        for i in range(20):
            events.append(_tool_start(f"p{i}"))
            events.append(_tool_completion(f"p{i}"))

        async def _list_all(job_id, *, limit, offset=0):
            return events[offset : offset + limit]

        captured: dict[str, _StartBaselineTracker] = {}
        real_tracker_cls = reenrich_mod._StartBaselineTracker

        class _CapturingTracker(real_tracker_cls):  # type: ignore[valid-type, misc]
            def __init__(self) -> None:
                super().__init__()
                captured["tracker"] = self

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=_list_all)
            repo.update_metadata = AsyncMock()

            with (
                patch("backend.services.events.reenrich._make_legacy_enricher", _FakePairingEnricher),
                patch("backend.services.events.reenrich._BATCH_SIZE", 4),
                patch("backend.services.events.reenrich._StartBaselineTracker", _CapturingTracker),
            ):
                updated = await reenrich_job_events("j1", mock_session_factory)

        tracker = captured["tracker"]
        # Non-vacuous boundedness: 20 paired calls spread over multiple batches,
        # but retained baselines peak at the 2 concurrently-unpaired starts
        # (+1 transient) — NOT ~22. A leak (retiring only by emitted id) would
        # push peak to 22.
        assert tracker.peak_size <= 3
        assert len(tracker) == 0  # everything retired by the end

        # Writes: 20 enriched completions + 2 unpaired orphans = 22.
        assert updated == 22
        written_ids = {c.kwargs["event_id"] for c in repo.update_metadata.await_args_list}
        # The two unpaired starts' orphans were compared against their own
        # retained baseline and persisted.
        assert u1.id in written_ids
        assert u2.id in written_ids

    @pytest.mark.asyncio
    async def test_idless_start_emitted_immediately_leaves_tracker_empty(self, mock_session_factory):
        """A tool-start with no valid tool_call_id is emitted immediately under
        its OWN id (never buffered). Its baseline must be retired after
        persistence even though ``e.id == event.id`` — otherwise one baseline
        leaks per id-less start."""
        import backend.services.events.reenrich as reenrich_mod

        # 3 id-less starts interleaved with a normal paired start/completion.
        idless = [
            new_event(
                session_id="j1",
                kind=EventKind.tool_call_started,
                payload={"tool_name": "bash"},  # no tool_call_id at all
            )
            for _ in range(3)
        ]
        events = [
            idless[0],
            _tool_start("p0"),
            idless[1],
            _tool_completion("p0"),
            idless[2],
        ]

        async def _list_all(job_id, *, limit, offset=0):
            return events[offset : offset + limit]

        captured: dict[str, _StartBaselineTracker] = {}
        real_tracker_cls = reenrich_mod._StartBaselineTracker

        class _CapturingTracker(real_tracker_cls):  # type: ignore[valid-type, misc]
            def __init__(self) -> None:
                super().__init__()
                captured["tracker"] = self

        with patch("backend.persistence.event_repo.EventRepository") as mock_repo_cls:
            repo = mock_repo_cls.return_value
            repo.list_by_job = AsyncMock(return_value=[])
            repo.list_all_events_by_job = AsyncMock(side_effect=_list_all)
            repo.update_metadata = AsyncMock()

            with (
                patch("backend.services.events.reenrich._make_legacy_enricher", _FakePairingEnricher),
                patch("backend.services.events.reenrich._BATCH_SIZE", 2),
                patch("backend.services.events.reenrich._StartBaselineTracker", _CapturingTracker),
            ):
                updated = await reenrich_job_events("j1", mock_session_factory)

        tracker = captured["tracker"]
        # The id-less starts were emitted immediately and their baselines retired
        # despite sharing their event id — nothing leaks.
        assert len(tracker) == 0
        # Peak never held more than the single transiently-buffered paired start
        # plus the id-less start being processed in the same step.
        assert tracker.peak_size <= 2

        # All three id-less starts + the paired completion were persisted (each
        # enriched with tool_display), compared against their own baseline.
        written_ids = {c.kwargs["event_id"] for c in repo.update_metadata.await_args_list}
        for ev in idless:
            assert ev.id in written_ids
        assert updated == 4  # 3 id-less starts + 1 completion
