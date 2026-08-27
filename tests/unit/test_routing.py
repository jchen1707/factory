"""§21.1 / §4.5 — the three validation rules, and the ultra prohibition."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from factory.routing import ModelFacts, RoutingError, load_routing, rewrite, validate

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
    assert routing.usd_per_run == 50.0


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


# --------------------------------------------------------------------------------
# Editing the table — `routing` owns writing `models.toml`, not just reading it
# --------------------------------------------------------------------------------


def test_validate_and_load_routing_agree_on_every_table(tmp_path: Path) -> None:
    """The property the tempfile round-trip made unwritable.

    The console had to write its candidate to a `NamedTemporaryFile` and parse it back,
    because `load_routing` took only a path. That is the interface's shape leaking into
    the caller, and it meant the form's "no" and the tick's "no" were two calls that
    someone had to keep the same. They are one call now, and this asserts it over the
    accept case and every refusal §4.5 has.
    """
    tables = [
        _table(),
        _table(reviewer_model="gpt-5.6-sol"),  # rule 1: reviewer shares the builder
        _table(builder_model="gpt-5.9-nope"),  # rule 2: not in the catalogue
        _table(builder_effort="ultra"),  # the ultra prohibition
        _table(usd_per_run="0.0"),  # rule 3
        _table(usd_warn_at="99.0"),  # rule 3, the other half
    ]
    for body in tables:
        path = tmp_path / "models.toml"
        path.write_text(body)

        from_text = _outcome(validate, body)
        from_path = _outcome(load_routing, path)

        assert from_text == from_path, body


def _outcome(call, argument):  # type: ignore[no-untyped-def]
    """The verdict as a comparable value: the roles on success, the message on refusal."""
    try:
        routing = call(argument, cache=CACHE)
    except RoutingError as exc:
        return ("refused", str(exc))
    return ("accepted", {name: (r.model, r.effort) for name, r in routing.roles.items()})


def test_validate_rejects_a_syntax_error_before_it_reaches_the_rules() -> None:
    with pytest.raises(tomllib.TOMLDecodeError):
        validate("[roles.builder\nmodel = ", cache=CACHE)


def test_rewrite_changes_only_the_values_the_form_owns(tmp_path: Path) -> None:
    """A targeted line rewrite, not a re-serialisation.

    `models.toml` carries the measured model catalogue and a page of comments explaining
    why each number is what it is. Round-tripping it through a TOML writer throws all of
    that away, so the rewrite touches the value and nothing else — and a line whose value
    is unchanged comes back byte-identical, or a one-effort edit arrives as a five-line
    diff nobody can read.
    """
    path = tmp_path / "models.toml"
    body = _table()
    path.write_text(body)

    text = rewrite(path, {"effort.builder": "max", "usd_warn_at": "13"})

    assert validate(text, cache=CACHE).roles["builder"].effort == "max"
    assert validate(text, cache=CACHE).usd_warn_at == 13.0
    # Everything the form did not name is untouched, comments included.
    before = [line for line in body.splitlines() if line.strip()]
    after = [line for line in text.splitlines() if line.strip()]
    assert len(before) == len(after)
    changed = [(b, a) for b, a in zip(before, after, strict=True) if b != a]
    assert len(changed) == 2


def test_rewrite_leaves_the_comment_column_where_it_found_it(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text('[roles.builder]\neffort = "xhigh"      # measured on 2026-08-21\n')

    text = rewrite(path, {"effort.builder": "max"})

    assert text.splitlines()[1].index("#") == len('effort = "xhigh"      ')
