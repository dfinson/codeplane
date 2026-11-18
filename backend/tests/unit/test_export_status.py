from concurrent.futures import ThreadPoolExecutor

from backend.services.events.export_status import ExportDropReason, ExportHealth


def test_optional_filter_withholding_and_sink_disable_have_distinct_local_health():
    health = ExportHealth(enabled=True)
    health.record_drop(ExportDropReason.metadata_withheld)
    assert health.snapshot().enabled
    health.record_drop(ExportDropReason.sink_failure, disable=True)
    snapshot = health.snapshot()
    assert not snapshot.enabled and snapshot.dropped_spans == 2
    assert dict(snapshot.dropped_by_reason) == {"metadata_withheld": 1, "sink_failure": 1}


def test_export_drop_accounting_is_safe_across_callback_and_export_threads():
    health = ExportHealth(enabled=True)
    with ThreadPoolExecutor(max_workers=4) as threads:
        list(threads.map(lambda _: health.record_drop(ExportDropReason.queue_full), range(1000)))
    assert health.snapshot().dropped_spans == 1000
