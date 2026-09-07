from factory.agent.telemetry import ContextReading, ThreadTelemetry


def event(total: int, last: int, window: int = 1000) -> dict:
    def usage(n: int) -> dict:
        return {
            "inputTokens": n,
            "cachedInputTokens": 0,
            "outputTokens": 0,
            "reasoningOutputTokens": 0,
            "totalTokens": n,
        }

    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": "thread-a",
            "turnId": "turn-a",
            "tokenUsage": {
                "total": usage(total),
                "last": usage(last),
                "modelContextWindow": window,
            },
        },
    }


def test_intermediate_usage_never_treats_cumulative_tokens_as_occupancy() -> None:
    t = ThreadTelemetry("thread-a", "model-a", semantics_verified=True)
    t.observe(event(100_000, 750), sequence=1, observed_at=100)
    assert t.usage.input_tokens == 100_000
    assert t.context.read(now=110) == ContextReading(0.75, "warn", 10)
    t.observe(event(100_000, 750), sequence=1, observed_at=150)
    assert t.context.observed_at == 100
    assert t.context.read(now=300).status == "stale"


def test_compaction_invalidates_occupancy_until_next_normal_turn_usage() -> None:
    t = ThreadTelemetry("thread-a", "model-a", semantics_verified=True)
    t.observe(event(100_000, 850), sequence=1, observed_at=100)
    assert t.should_compact(safe_boundary=False) is False
    assert t.should_compact(safe_boundary=True) is True
    t.observe(
        {
            "method": "item/completed",
            "params": {
                "threadId": "thread-a",
                "turnId": "compact-turn",
                "item": {"id": "compact-1", "type": "contextCompaction"},
            },
        },
        sequence=2,
        observed_at=101,
    )
    assert t.context.read(now=102).status == "awaiting measurement after compaction"
    t.observe(event(101_000, 200), sequence=3, observed_at=103)
    assert t.context.read(now=104).fraction == 0.2
    assert t.usage.input_tokens == 101_000
    assert len(t.compactions) == 1


def test_unvalidated_semantics_model_change_and_thread_isolation() -> None:
    t = ThreadTelemetry("thread-b", "model-a")
    t.observe(event(1000, 750), sequence=1, observed_at=100)
    assert t.usage.input_tokens == 0
    t = ThreadTelemetry("thread-a", "model-a")
    t.observe(event(1000, 750), sequence=1, observed_at=100)
    assert t.context.read(now=110).fraction is None
    t.observe(
        {"method": "model/rerouted", "params": {"threadId": "thread-a", "toModel": "model-b"}},
        sequence=2,
        observed_at=111,
    )
    assert t.model == "model-b"
    assert t.context.read(now=112).fraction is None


def test_out_of_order_usage_cannot_regress_cumulative_usage() -> None:
    t = ThreadTelemetry("thread-a", "model-a", semantics_verified=True)
    t.observe(event(2000, 500), sequence=2, observed_at=101)
    t.observe(event(1000, 800), sequence=1, observed_at=100)
    assert t.usage.input_tokens == 2000
    assert t.context.read(now=102).fraction == 0.5
