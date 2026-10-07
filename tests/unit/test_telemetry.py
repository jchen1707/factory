from factory.agent.telemetry import ContextReading, CurrentContext


def test_current_context_reads_occupancy_only_while_fresh() -> None:
    context = CurrentContext(tokens=750, effective_window=1000, observed_at=100)
    assert context.read(now=110) == ContextReading(0.75, "warn", 10)
    assert CurrentContext(650, 1000, 100).read(now=110).status == "fresh"
    assert CurrentContext(850, 1000, 100).read(now=110).status == "compact at safe boundary"
    assert context.read(now=300) == ContextReading(None, "stale", 200)
    assert CurrentContext(observed_at=100).read(now=110).status == "current context unavailable"
