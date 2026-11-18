import threading

from backend.services.events.export_status import ExportDropReason
from backend.services.events.export_transport import BoundedDelivery, Delivery, retry_after


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        return self.stopped


def test_retry_schedule_and_per_attempt_budget_are_exact():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)
    attempts = []

    def fail(timeout):
        attempts.append(timeout)
        return Delivery(False, retryable=True)

    result = delivery.send(fail)
    assert clock.waits == [1, 2, 4, 8]
    assert attempts == [5] * 5
    assert result.reason == ExportDropReason.retry_exhausted


def test_slow_requests_share_one_thirty_second_window():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)
    attempts = []

    def slow(timeout):
        attempts.append(timeout)
        clock.now += timeout
        return Delivery(False, retryable=True)

    assert delivery.send(slow).reason == ExportDropReason.retry_exhausted
    assert clock.now <= 30
    assert max(attempts) <= 5 and len(attempts) == 4
    assert clock.waits == [1, 2, 4]


def test_retry_after_must_fit_total_budget_and_auth_never_retries():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)
    result = delivery.send(lambda timeout: Delivery(False, retryable=True, retry_after=31))
    assert result.reason == ExportDropReason.retry_exhausted and clock.waits == []
    result = delivery.send(lambda timeout: Delivery(False, reason=ExportDropReason.authentication))
    assert result.reason == ExportDropReason.authentication and clock.waits == []
    stop = threading.Event()
    stop.set()
    assert BoundedDelivery(stop=stop).send(lambda _: Delivery(True)).reason == ExportDropReason.shutdown
    assert retry_after("nan") is None and retry_after("invalid") is None and retry_after("0") == 0
