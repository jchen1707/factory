from datetime import date
from pathlib import Path

import pytest

from factory.agent.base import Usage
from factory.pricing import PriceBook, RequestUsage


@pytest.fixture
def prices() -> PriceBook:
    return PriceBook.load(Path(__file__).parents[2] / "config" / "prices.toml")


def test_cached_write_and_output_pricing(prices: PriceBook) -> None:
    usage = Usage(
        input_tokens=1000, cached_input_tokens=400, cache_write_input_tokens=100, output_tokens=200
    )
    result = prices.estimate(
        RequestUsage("gpt-5.6-sol", usage, date(2026, 9, 6), "standard", False)
    )
    assert result.complete
    assert result.usd == pytest.approx((500 * 4 + 400 * 0.4 + 100 * 5 + 200 * 20) / 1_000_000)


def test_long_context_and_service_tier(prices: PriceBook) -> None:
    usage = Usage(input_tokens=1000, output_tokens=200)
    result = prices.estimate(RequestUsage("gpt-5.6-sol", usage, date(2026, 9, 6), "standard", True))
    assert result.usd == pytest.approx(0.014)
    assert not prices.estimate(
        RequestUsage("gpt-5.6-sol", usage, date(2026, 9, 6), "unknown", True)
    ).complete


def test_missing_historical_and_request_detail_stays_incomplete(prices: PriceBook) -> None:
    usage = Usage(input_tokens=1000)
    for model, day, tier, long in [
        ("missing", date(2026, 9, 6), "standard", False),
        ("gpt-5.6-sol", date(2026, 8, 1), "standard", False),
        ("gpt-5.6-sol", date(2026, 9, 6), None, False),
        ("gpt-5.6-sol", date(2026, 9, 6), "standard", None),
    ]:
        result = prices.estimate(RequestUsage(model, usage, day, tier, long))
        assert not result.complete
        assert result.usd is None
        assert result.reason
