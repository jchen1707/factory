"""Normalized observations. Cumulative billed usage never measures context occupancy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory.agent.base import Usage


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


def usage_from_wire(raw: dict[str, Any]) -> Usage:
    def count(key: str) -> int:
        value = raw.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid token count: {key}")
        return value

    return Usage(
        input_tokens=count("inputTokens"),
        cached_input_tokens=count("cachedInputTokens"),
        cache_write_input_tokens=count("cacheWriteInputTokens"),
        output_tokens=count("outputTokens"),
        reasoning_output_tokens=count("reasoningOutputTokens"),
    )


@dataclass
class ThreadTelemetry:
    thread_id: str
    model: str
    semantics_verified: bool = False
    usage: Usage = field(default_factory=Usage)
    context: CurrentContext = field(default_factory=CurrentContext)
    compactions: list[dict[str, Any]] = field(default_factory=list)
    model_changes: list[dict[str, Any]] = field(default_factory=list)
    sequence: int = -1

    def observe(self, event: dict[str, Any], *, sequence: int, observed_at: float) -> None:
        params = event.get("params", {})
        if params.get("threadId") != self.thread_id or sequence <= self.sequence:
            return
        self.sequence = sequence
        method = event.get("method")
        if method == "thread/tokenUsage/updated":
            raw = params["tokenUsage"]
            usage = usage_from_wire(raw["total"])
            if (
                usage.input_tokens < self.usage.input_tokens
                or usage.output_tokens < self.usage.output_tokens
            ):
                self.context = CurrentContext(unavailable="usage counter regressed")
                return
            self.usage = usage
            last = usage_from_wire(raw["last"])
            window = raw.get("modelContextWindow")
            if self.semantics_verified and type(window) is int and window > 0:
                self.context = CurrentContext(last.total_tokens, window, observed_at)
            else:
                self.context = CurrentContext(unavailable="runtime context semantics unvalidated")
        elif method == "model/rerouted":
            self.model = str(params["toModel"])
            self.model_changes.append({"model": self.model, "observed_at": observed_at})
            self.context = CurrentContext(unavailable="awaiting measurement after model change")
        elif (
            method == "item/completed" and params.get("item", {}).get("type") == "contextCompaction"
        ):
            item_id = params["item"]["id"]
            if not any(c["id"] == item_id for c in self.compactions):
                self.compactions.append({"id": item_id, "observed_at": observed_at})
            self.context = CurrentContext(unavailable="awaiting measurement after compaction")

    def should_compact(self, *, safe_boundary: bool) -> bool:
        reading = self.context.read(now=self.context.observed_at or 0)
        return safe_boundary and reading.fraction is not None and reading.fraction >= 0.8
