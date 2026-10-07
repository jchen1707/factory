"""§21.1 / §4.5 — the three validation rules over the shipped, file-only catalogue."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from factory.execution import PRESETS
from factory.routing import RoutingError, load_routing, rewrite, validate

HOME = Path(__file__).resolve().parents[2]
SHIPPED = HOME / "config" / "models.toml"
#: The shipped `[models.*]` tables, so every test validates against the real catalogue
#: rather than a stand-in that can drift from it.
MODELS = SHIPPED.read_text(encoding="utf-8").partition("\n[models.")[2]
BUDGET = "[budget]\nusd_per_run = 20.0\nusd_warn_at = 12.0\n"


def _table(**overrides: str) -> str:
    values = {
        "builder_model": "claude-opus-5-5",
        "builder_effort": "medium",
        "reviewer_model": "claude-fable-5-1",
        "usd_per_run": "20.0",
        "usd_warn_at": "12.0",
    }
    values.update(overrides)
    return f"""
[roles.planner]
model = "claude-opus-5-5"
effort = "max"

[roles.builder]
model = "{values["builder_model"]}"
effort = "{values["builder_effort"]}"

[roles.reviewer]
model = "{values["reviewer_model"]}"
effort = "high"

[roles.synthesiser]
model = "claude-sonnet-5-5"
effort = "medium"

[roles.documenter]
model = "claude-sonnet-4-6"
effort = "low"

[budget]
usd_per_run = {values["usd_per_run"]}
usd_warn_at = {values["usd_warn_at"]}

[models.{MODELS}"""


def _load(tmp_path: Path, body: str):  # type: ignore[no-untyped-def]
    path = tmp_path / "models.toml"
    path.write_text(body)
    return load_routing(path)


def test_the_shipped_table_validates() -> None:
    routing = load_routing(SHIPPED)
    assert {name: (r.model, r.effort) for name, r in routing.roles.items()} == {
        "planner": ("claude-opus-5-5", "high"),
        "builder": ("claude-opus-5-5", "medium"),
        "reviewer": ("claude-fable-5-1", "high"),
        "synthesiser": ("claude-sonnet-5-5", "medium"),
        "documenter": ("claude-sonnet-5-5", "medium"),
    }
    assert routing.usd_per_run == 50.0


def test_the_shipped_catalogue_is_the_measured_claude_models() -> None:
    routing = load_routing(SHIPPED)
    assert {
        slug: (facts.context_window, facts.supported_efforts)
        for slug, facts in routing.models.items()
    } == {
        "claude-fable-5-1": (1000000, ("low", "medium", "high", "xhigh", "max")),
        "claude-opus-5-5": (1000000, ("low", "medium", "high", "xhigh", "max")),
        "claude-sonnet-5-5": (1000000, ("low", "medium", "high", "xhigh", "max")),
        "claude-opus-4-7": (1000000, ("low", "medium", "high", "xhigh", "max")),
        "claude-sonnet-4-6": (200000, ("low", "medium", "high", "max")),
        "claude-haiku-4-5-20251001": (200000, ()),
    }


def test_routing_reads_only_the_file_it_is_given(tmp_path: Path) -> None:
    """The catalogue is the file. A home directory carrying a Codex model cache and a
    Claude Code model catalog that list none of the shipped models must change nothing,
    and no file other than `models.toml` may be opened."""
    home = tmp_path / "home"
    poison = json.dumps({"models": [{"slug": "not-a-shipped-model"}]})
    for relative in (".codex/models_cache.json", ".claude/cache/model-catalog/cc.json"):
        (home / relative).parent.mkdir(parents=True)
        (home / relative).write_text(poison)
    probe = (
        "import sys\n"
        "from pathlib import Path\n"
        "from factory.routing import load_routing\n"
        "opened = []\n"
        "sys.addaudithook(lambda event, args: event == 'open' and opened.append(str(args[0])))\n"
        "roles = load_routing(Path(sys.argv[1])).roles\n"
        "print(roles['builder'].model)\n"
        "print(*opened, sep='\\n')\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", probe, str(SHIPPED)],
        env={**os.environ, "HOME": str(home), "CODEX_HOME": str(home / ".codex")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    builder, *opened = done.stdout.splitlines()
    assert builder == "claude-opus-5-5"
    assert opened == [str(SHIPPED)]


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_passes_the_rules_the_roles_pass(preset: str) -> None:
    roles = "".join(
        f'[roles.{name}]\nmodel = "{model}"\neffort = "{effort}"\n\n'
        for name, (model, effort) in PRESETS[preset].items()
    )
    routing = validate(f"{roles}{BUDGET}\n[models.{MODELS}")
    assert {name: (r.model, r.effort) for name, r in routing.roles.items()} == PRESETS[preset]


def test_reviewer_sharing_the_builders_model_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="must differ"):
        _load(tmp_path, _table(reviewer_model="claude-opus-5-5"))


@pytest.mark.parametrize("model", ["claude-9-imaginary", "opus", "sonnet"])
def test_a_model_outside_the_catalogue_is_refused(tmp_path: Path, model: str) -> None:
    # Aliases are refused too: `opus` resolves to whatever Anthropic ships next.
    with pytest.raises(RoutingError, match=r"not in models.toml's \[models\.\*\] catalogue"):
        _load(tmp_path, _table(builder_model=model))


def test_an_unsupported_effort_is_refused(tmp_path: Path) -> None:
    # claude-sonnet-4-6 offers no `xhigh`. Naming an effort a model does not have is as
    # broken as naming a model that does not exist, and Claude Code silently falls back.
    with pytest.raises(RoutingError, match="supports"):
        _load(tmp_path, _table(builder_model="claude-sonnet-4-6", builder_effort="xhigh"))


def test_a_role_on_a_model_without_efforts_must_name_none(tmp_path: Path) -> None:
    haiku = _table().replace(
        'model = "claude-sonnet-4-6"\neffort = "low"\n', 'model = "claude-haiku-4-5-20251001"\n'
    )
    assert _load(tmp_path, haiku).roles["documenter"].effort is None
    with pytest.raises(RoutingError, match="takes no effort"):
        _load(
            tmp_path,
            haiku.replace(
                'model = "claude-haiku-4-5-20251001"\n',
                'model = "claude-haiku-4-5-20251001"\neffort = "low"\n',
            ),
        )


def test_a_role_must_name_an_effort_its_model_offers(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="supports"):
        _load(
            tmp_path,
            _table().replace(
                'model = "claude-sonnet-4-6"\neffort = "low"\n', 'model = "claude-sonnet-4-6"\n'
            ),
        )


def test_budget_rules(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="greater than zero"):
        _load(tmp_path, _table(usd_per_run="0.0"))
    with pytest.raises(RoutingError, match="must be below"):
        _load(tmp_path, _table(usd_warn_at="25.0"))


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
        _table(reviewer_model="claude-opus-5-5"),  # rule 1: reviewer shares the builder
        _table(builder_model="claude-9-nope"),  # rule 2: not in the catalogue
        _table(builder_effort="ultra"),  # rule 2: not an effort the model offers
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
        routing = call(argument)
    except RoutingError as exc:
        return ("refused", str(exc))
    return ("accepted", {name: (r.model, r.effort) for name, r in routing.roles.items()})


def test_validate_rejects_a_syntax_error_before_it_reaches_the_rules() -> None:
    with pytest.raises(tomllib.TOMLDecodeError):
        validate("[roles.builder\nmodel = ")


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

    assert validate(text).roles["builder"].effort == "max"
    assert validate(text).usd_warn_at == 13.0
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


def test_max_turns_defaults_from_the_budget_and_a_role_may_override_it(tmp_path: Path) -> None:
    body = _table().replace("[budget]", "[budget]\nmax_turns = 120")
    body = body.replace('effort = "high"\n', 'effort = "high"\nmax_turns = 40\n', 1)  # the reviewer
    routing = _load(tmp_path, body)
    assert routing.max_turns == 120
    assert routing.role("builder").max_turns == 120
    assert routing.role("reviewer").max_turns == 40
    # Absent entirely, the shipped default applies rather than a zero that would refuse
    # every launch.
    assert _load(tmp_path, _table()).role("builder").max_turns == 200


def test_a_zero_turn_cap_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RoutingError, match="max_turns"):
        _load(tmp_path, _table().replace("[budget]", "[budget]\nmax_turns = 0"))


def test_the_hold_thresholds_default_per_window_and_a_partial_table_keeps_the_other(
    tmp_path: Path,
) -> None:
    assert load_routing(SHIPPED).hold_at == {"five_hour": 0.9, "seven_day": 0.95}
    assert _load(tmp_path, _table()).hold_at == {"five_hour": 0.9, "seven_day": 0.95}

    routing = _load(tmp_path, _table() + "\n[budget.hold_at]\nseven_day = 0.8\n")

    assert routing.hold_at == {"five_hour": 0.9, "seven_day": 0.8}


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ("five_hours = 0.9", "five_hours"),
        ("five_hour = 0", "above 0"),
        ("seven_day = 1.5", "at most 1"),
    ],
)
def test_a_hold_threshold_the_guard_could_not_honour_is_refused(
    tmp_path: Path, table: str, message: str
) -> None:
    with pytest.raises(RoutingError, match=message):
        _load(tmp_path, _table() + f"\n[budget.hold_at]\n{table}\n")
