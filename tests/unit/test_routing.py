"""§21.1 / §4.5 — the three validation rules, and the ultra prohibition."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.routing import ModelFacts, RoutingError, load_routing

HOME = Path(__file__).resolve().parents[2]

CACHE = {
    slug: ModelFacts(
        slug=slug,
        display=slug,
        default_effort="medium",
        supported_efforts=efforts,
        context_window=272000,
        effective_percent=95,
        hidden=hidden,
    )
    for slug, efforts, hidden in [
        ("gpt-5.6-sol", ("low", "medium", "high", "xhigh", "max", "ultra"), False),
        # Terra is what the shipped table routes the reviewer at, so the stand-in cache
        # has to carry it: these two tests load `config/models.toml` itself, and a
        # fixture missing a model the real cache has fails the shipped table for a fact
        # about the fixture. `~/.codex/models_cache.json` lists it at low..ultra.
        ("gpt-5.6-terra", ("low", "medium", "high", "xhigh", "max", "ultra"), False),
        ("gpt-5.6-luna", ("low", "medium", "high", "xhigh", "max"), False),
        ("gpt-5.5", ("low", "medium", "high", "xhigh"), False),
        ("gpt-5.4-mini", ("low", "medium", "high", "xhigh"), False),
    ]
}


def _table(**overrides: str) -> str:
    values = {
        "builder_model": "gpt-5.6-sol",
        "builder_effort": "xhigh",
        "reviewer_model": "gpt-5.5",
        "usd_per_run": "20.0",
        "usd_warn_at": "12.0",
    }
    values.update(overrides)
    return f"""
[roles.planner]
model = "gpt-5.6-sol"
effort = "max"

[roles.builder]
model = "{values["builder_model"]}"
effort = "{values["builder_effort"]}"

[roles.reviewer]
model = "{values["reviewer_model"]}"
effort = "high"

[roles.synthesiser]
model = "gpt-5.6-luna"
effort = "medium"

[roles.documenter]
model = "gpt-5.4-mini"
effort = "low"

[budget]
usd_per_run = {values["usd_per_run"]}
usd_warn_at = {values["usd_warn_at"]}
"""


def _load(tmp_path: Path, body: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "models.toml"
    path.write_text(body)
    return load_routing(path, cache=CACHE)


def test_the_shipped_table_validates() -> None:
    routing = load_routing(HOME / "config" / "models.toml", cache=CACHE)
    assert routing.roles["builder"].model != routing.roles["reviewer"].model
    assert routing.usd_per_run == 20.0


def test_the_shipped_table_records_all_eight_measured_models() -> None:
    # p0-12 correction 1: eight, not seven. `gpt-reserve` is hidden but real, and a
    # table that omitted a model the validation would accept has drifted.
    routing = load_routing(HOME / "config" / "models.toml", cache=CACHE)
    assert len(routing.models) == 8
    assert routing.models["gpt-reserve"].hidden
    assert routing.models["codex-auto-review"].hidden


def test_reviewer_sharing_the_builders_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="must differ"):
        _load(tmp_path, _table(reviewer_model="gpt-5.6-sol"))


def test_ultra_is_refused_for_the_builder(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="ultra"):
        _load(tmp_path, _table(builder_effort="ultra"))


def test_an_unmeasured_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="measured model list"):
        _load(tmp_path, _table(builder_model="gpt-9-imaginary"))


def test_an_unsupported_effort_is_refused(tmp_path: Path) -> None:
    # gpt-5.5 offers low..xhigh and no `max`. Naming one it does not have is as broken
    # as naming a model that does not exist.
    with pytest.raises(RoutingError, match="supports"):
        _load(
            tmp_path, _table(reviewer_model="gpt-5.5").replace('effort = "high"', 'effort = "max"')
        )


def test_budget_rules(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="greater than zero"):
        _load(tmp_path, _table(usd_per_run="0.0"))
    with pytest.raises(RoutingError, match="must be below"):
        _load(tmp_path, _table(usd_warn_at="25.0"))


def test_the_context_denominator_uses_context_window_not_the_larger_figure() -> None:
    # p0-12 correction 2: max_context_window differs per model (272k/872k/1M) while
    # context_window is uniformly 272k. Using the larger one would understate pressure.
    routing = load_routing(HOME / "config" / "models.toml", cache=CACHE)
    assert routing.facts("gpt-5.6-sol").usable_context == 272000 * 95 // 100
