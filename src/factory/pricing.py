"""Dated API-equivalent estimates. These are never Codex account charges."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from factory.agent.base import Usage


@dataclass(frozen=True)
class RequestUsage:
    model: str
    usage: Usage
    day: date
    service_tier: str | None
    long_context: bool | None


@dataclass(frozen=True)
class Estimate:
    usd: float | None
    complete: bool
    reason: str | None = None
    price_revision: str | None = None


@dataclass(frozen=True)
class PriceBook:
    rows: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path) -> PriceBook:
        if not path.exists():
            return cls(())
        raw = tomllib.loads(path.read_text())
        return cls(tuple(raw.get("rate", ())))

    def estimate(self, request: RequestUsage) -> Estimate:
        if request.service_tier is None or request.long_context is None:
            return Estimate(None, False, "request service tier or context band unavailable")
        tier = "fast" if request.service_tier == "priority" else request.service_tier
        rows = [
            r
            for r in self.rows
            if r["model"] == request.model
            and r["service_tier"] == tier
            and r["long_context"] == request.long_context
            and r["effective_from"] <= request.day <= r["effective_until"]
        ]
        if len(rows) != 1:
            return Estimate(None, False, "no unique dated price for model, tier and context band")
        row = rows[0]
        usage = request.usage
        counts = (
            usage.input_tokens - usage.cached_input_tokens - usage.cache_write_input_tokens,
            usage.cached_input_tokens,
            usage.cache_write_input_tokens,
            usage.output_tokens,
        )
        if any(n < 0 for n in counts):
            return Estimate(None, False, "inconsistent input and cache counts")
        rates = [
            row.get(k)
            for k in (
                "input_per_mtok",
                "cached_input_per_mtok",
                "cache_write_per_mtok",
                "output_per_mtok",
            )
        ]
        if any(
            n and (r is None or not math.isfinite(r) or r < 0)
            for n, r in zip(counts, rates, strict=True)
        ):
            return Estimate(None, False, "partially priced usage")
        usd = sum(n * (r or 0) for n, r in zip(counts, rates, strict=True)) / 1_000_000
        return Estimate(usd, True, price_revision=f"{row['source']}@{row['effective_from']}")
