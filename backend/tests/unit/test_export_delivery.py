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

    def fail(deadline):
        attempts.append(deadline - clock.now)
        return Delivery(False, retryable=True)

    result = delivery.send(fail)
    assert clock.waits == [1, 2, 4, 8]
    assert attempts == [5] * 5
    assert result.reason == ExportDropReason.retry_exhausted


def test_slow_requests_share_one_thirty_second_window():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)
    attempts = []

    def slow(deadline):
        timeout = deadline - clock.now
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


def test_delivery_rejects_success_that_returns_after_its_deadline():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)

    def late_success(deadline):
        clock.now = deadline + 1
        return Delivery(True)

    assert not delivery.send(late_success).success


def test_observed_auth_failure_still_disables_retry_even_when_delivery_is_late():
    clock = Clock()
    delivery = BoundedDelivery(clock=lambda: clock.now, stop=clock)

    def refused(deadline):
        clock.now = deadline + 1
        return Delivery(False, reason=ExportDropReason.authentication)

    assert delivery.send(refused).reason == ExportDropReason.authentication
    assert clock.waits == []
