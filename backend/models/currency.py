"""Nullable monetary arithmetic for read projections, never native-unit conversion."""

from collections.abc import Iterable
from decimal import Decimal

from backend.models.usage import UsageAmount


def verified_usd(usage: Iterable[UsageAmount]) -> Decimal | None:
    amounts = [item.amount for item in usage if item.unit == "USD" and item.verified and item.currency == "USD"]
    return sum(amounts, Decimal(0)) if amounts else None


def optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float, Decimal, str)) else None


def sum_known(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def divide_known(value: float | None, divisor: float | int) -> float | None:
    return value / divisor if value is not None and divisor > 0 else None
