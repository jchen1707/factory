"""Role to model and effort — §4.5.

Routing is a control-plane concern, not a prompt concern, so it is a lookup here and
not a line in an agent file. The factory never edits an agent file to change a model,
because a routing table a per-agent file can override is not a routing table.

`models.toml`'s `[models.*]` table is the only catalogue. Nothing outside the repository
is read: a catalogue that depends on the host's home directory validates differently on
every machine, and the suite stops being hermetic.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, get_args

from factory.agent.claude import Effort

__all__ = [
    "ModelFacts",
    "Role",
    "Routing",
    "RoutingError",
    "load_routing",
    "rewrite",
    "validate",
]


class RoutingError(Exception):
    """A routing table the daemon must refuse to start on."""


@dataclass(frozen=True)
class ModelFacts:
    slug: str
    context_window: int
    supported_efforts: tuple[str, ...]
    default_effort: str | None


#: Turns one launch may take before the CLI stops it with `error_max_turns`. The ladder
#: resumes the session with a fresh allowance, so a cap that is too low costs a rung, not
#: the work; one that is too high lets a looping agent run until the budget does.
DEFAULT_MAX_TURNS = 200

#: Utilisation, per subscription window as `rate_limit_event` names it, at which no new
#: launch starts until that window resets. Below 1.0 so the launches already running
#: have headroom to finish rather than meet a 429 mid-write.
DEFAULT_HOLD_AT: Mapping[str, float] = MappingProxyType({"five_hour": 0.9, "seven_day": 0.95})


@dataclass(frozen=True)
class Role:
    name: str
    model: str
    effort: str | None
    preset: str = "existing"
    max_turns: int = DEFAULT_MAX_TURNS


@dataclass(frozen=True)
class Routing:
    roles: Mapping[str, Role]
    models: Mapping[str, ModelFacts]
    usd_per_run: float
    usd_warn_at: float
    max_turns: int = DEFAULT_MAX_TURNS
    hold_at: Mapping[str, float] = field(default_factory=lambda: DEFAULT_HOLD_AT)

    def role(self, name: str) -> Role:
        try:
            return self.roles[name]
        except KeyError:
            raise RoutingError(f"no role {name!r} in models.toml") from None


def _models_from_file(raw: Mapping[str, Any]) -> dict[str, ModelFacts]:
    out: dict[str, ModelFacts] = {}
    for slug, body in dict(raw.get("models", {})).items():
        entry = dict(body)
        default = entry.get("default_effort")
        out[slug] = ModelFacts(
            slug=slug,
            context_window=int(entry["context_window"]),
            supported_efforts=tuple(str(e) for e in entry.get("supported_efforts", ())),
            default_effort=None if default is None else str(default),
        )
    return out


def load_routing(path: Path) -> Routing:
    """Parse `models.toml` and apply §4.5's three validation rules."""
    return _validated(tomllib.loads(path.read_text(encoding="utf-8")))


def _validated(raw: dict[str, Any]) -> Routing:
    """The rules themselves, over a parsed table. One body, two entry points.

    Split out of `load_routing` so `validate` can run it against text that is not on disk
    yet, with exactly one copy of the rules.
    """
    budget = dict(raw.get("budget", {}))
    usd_per_run = float(budget.get("usd_per_run", 0.0))
    usd_warn_at = float(budget.get("usd_warn_at", 0.0))
    max_turns = int(budget.get("max_turns", DEFAULT_MAX_TURNS))
    hold_at = {**DEFAULT_HOLD_AT, **{k: float(v) for k, v in budget.get("hold_at", {}).items()}}

    roles = {
        name: Role(
            name=name,
            model=str(body["model"]),
            effort=None if body.get("effort") is None else str(body["effort"]),
            max_turns=int(body.get("max_turns", max_turns)),
        )
        for name, body in dict(raw.get("roles", {})).items()
    }
    if not roles:
        raise RoutingError("models.toml declares no roles")

    catalogue = _models_from_file(raw)
    if not catalogue:
        raise RoutingError("models.toml declares no [models.*] catalogue")

    # Rule 1. A reviewer sharing the builder's model carries the builder's bias, which
    # is the entire reason the roles are split. Two efforts of one model share priors.
    builder = roles.get("builder")
    reviewer = roles.get("reviewer")
    if builder and reviewer and builder.model == reviewer.model:
        raise RoutingError(
            f"roles.reviewer.model must differ from roles.builder.model "
            f"(both are {builder.model!r})"
        )

    # Rule 2, plus the effort half of it: a model that does not offer the effort named
    # is as broken as one that does not exist. Only full ids are keys, so an alias such
    # as `opus`, which moves when a new model ships, is refused here.
    for model_facts in catalogue.values():
        unknown = sorted(set(model_facts.supported_efforts) - set(get_args(Effort)))
        if unknown:
            raise RoutingError(
                f"model {model_facts.slug!r} lists efforts {unknown} the Claude CLI does not "
                f"take; the levels are {list(get_args(Effort))}"
            )
    for role in roles.values():
        facts = catalogue.get(role.model)
        if facts is None:
            raise RoutingError(
                f"role {role.name!r} names model {role.model!r}, which is not in "
                f"models.toml's [models.*] catalogue: {sorted(catalogue)}"
            )
        if not facts.supported_efforts:
            if role.effort is not None:
                raise RoutingError(
                    f"role {role.name!r} asks {role.model!r} for effort {role.effort!r}; "
                    f"it takes no effort, so the role must not name one"
                )
        elif role.effort not in facts.supported_efforts:
            raise RoutingError(
                f"role {role.name!r} asks {role.model!r} for effort {role.effort!r}; "
                f"it supports {list(facts.supported_efforts)}"
            )

    # Rule 3.
    if usd_per_run <= 0:
        raise RoutingError("budget.usd_per_run must be greater than zero")
    if not usd_warn_at < usd_per_run:
        raise RoutingError(
            f"budget.usd_warn_at ({usd_warn_at}) must be below usd_per_run ({usd_per_run})"
        )
    for role in roles.values():
        if role.max_turns < 1:
            raise RoutingError(f"role {role.name!r} max_turns must be at least 1")
    unknown_windows = sorted(set(hold_at) - set(DEFAULT_HOLD_AT))
    if unknown_windows:
        raise RoutingError(
            f"budget.hold_at names {unknown_windows}; the windows are {list(DEFAULT_HOLD_AT)}"
        )
    for window, threshold in hold_at.items():
        if not 0 < threshold <= 1:
            raise RoutingError(f"budget.hold_at.{window} must be above 0 and at most 1")

    return Routing(
        roles=roles,
        models=catalogue,
        usd_per_run=usd_per_run,
        usd_warn_at=usd_warn_at,
        max_turns=max_turns,
        hold_at=MappingProxyType(hold_at),
    )


# --------------------------------------------------------------------------------
# Editing `models.toml` — the other half of owning it
# --------------------------------------------------------------------------------


def rewrite(path: Path, form: Mapping[str, Any]) -> str:
    """Apply the form's role/budget edits to `models.toml`, returning the new text.

    A targeted line rewrite rather than a re-serialisation: the file carries the measured
    model catalogue and a page of comments explaining why each number is what it is, and
    round-tripping it through a TOML writer would throw all of that away. Only the values
    the form actually owns are touched.
    """
    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    current_role: str | None = None
    out: list[str] = []
    in_budget = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[roles."):
            current_role = stripped[len("[roles.") :].rstrip("]").strip()
            in_budget = False
        elif stripped.startswith("["):
            current_role = None
            in_budget = stripped.startswith("[budget]")

        if current_role and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in ("model", "effort"):
                proposed = form.get(f"{key}.{current_role}")
                if proposed is not None:
                    out.append(_replace_value(line, f'"{str(proposed).strip()}"'))
                    continue
        if in_budget and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in ("usd_per_run", "usd_warn_at"):
                proposed = form.get(key)
                if proposed is not None:
                    out.append(_replace_value(line, str(float(str(proposed).strip()))))
                    continue
        out.append(line)
    return "\n".join(out) + "\n"


def _replace_value(line: str, rendered: str) -> str:
    """Swap the value on one `key = value  # comment` line, leaving everything else byte-
    identical — including the column the comment sits in.

    A line whose value is unchanged is returned untouched rather than re-rendered. The
    alternative (always rebuilding the line) reflows the comment alignment of every role
    in the file on any edit, so a one-effort change arrives as a five-line diff and the
    next reader cannot see what actually changed.
    """
    head, _, rest = line.partition("=")
    comment_at = rest.find("#")
    current = (rest if comment_at < 0 else rest[:comment_at]).strip()
    if current == rendered:
        return line
    if comment_at < 0:
        return f"{head}= {rendered}"
    # Keep the comment in its original column where the new value still fits under it.
    comment = rest[comment_at:]
    padding = len(rest[:comment_at]) - len(f" {rendered}")
    return f"{head}= {rendered}{' ' * padding if padding > 0 else '  '}{comment}"


def validate(text: str) -> Routing:
    """§4.5's rules over a candidate table, with no file involved.

    This is the function `load_routing` should always have been, and its absence had a
    shape: the console had to write its candidate to a `NamedTemporaryFile`, parse it back
    through `load_routing`, and unlink it in a `finally` — a tempfile round-trip that was
    the interface leaking into the caller. `load_routing` is now read-then-validate, so
    the form's "no" and the tick's "no" are literally the same call rather than two calls
    that have to be kept the same.

    `tomllib.loads` first, so a syntax error says so before §4.5 does.
    """
    return _validated(tomllib.loads(text))
