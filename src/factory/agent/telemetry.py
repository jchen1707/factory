"""Normalized observations. Cumulative billed usage never measures context occupancy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextReading:
    fraction: float | None
    status: str
    age_seconds: float | None


@dataclass(frozen=True)
class CurrentContext:
    tokens: int | None = None
    effective_window: int | None = None
    observed_at: float | None = None
    unavailable: str = "current context unavailable"

    def read(self, *, now: float, stale_after: float = 120) -> ContextReading:
        age = None if self.observed_at is None else max(0, now - self.observed_at)
        if self.tokens is None or not self.effective_window:
            return ContextReading(None, self.unavailable, age)
        if age is None or age > stale_after:
            return ContextReading(None, "stale", age)
        fraction = self.tokens / self.effective_window
        status = (
            "compact at safe boundary"
            if fraction >= 0.8
            else "warn"
            if fraction >= 0.7
            else "fresh"
        )
        return ContextReading(fraction, status, age)
