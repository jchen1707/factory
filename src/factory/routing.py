"""Role to model and effort — §4.5.

Routing is a control-plane concern, not a prompt concern, so it is a lookup here and
not a line in an agent file. The factory never edits an agent file to change a model:
layer A's frontmatter `model:` is an advisory default for the Claude path, and on the
Codex path this table is authoritative, because a routing table a per-agent file can
override is not a routing table.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "MODEL_CACHE",
    "ModelFacts",
    "Role",
    "Routing",
    "RoutingError",
    "load_model_cache",
    "load_routing",
    "rewrite",
    "validate",
]

#: Where Codex keeps the account's real model list. Read rather than guessed, and
#: reading it costs no model call (§24.10).
MODEL_CACHE = Path.home() / ".codex" / "models_cache.json"

#: "Maximum reasoning with automatic task delegation" — the cache's own words. The
#: delegation spawns work the control plane did not schedule and cannot see, which
#: breaks the single-writer rule §4.4 is built on. `max` gives the depth without it.
FORBIDDEN_BUILDER_EFFORT = "ultra"


class RoutingError(Exception):
    """A routing table the daemon must refuse to start on."""


@dataclass(frozen=True)
class ModelFacts:
    slug: str
    display: str
    default_effort: str
    supported_efforts: tuple[str, ...]
    context_window: int
    effective_percent: int
    hidden: bool

    @property
    def usable_context(self) -> int:
        """The denominator for the context percentage (§18.5).

        P0-7 proved the event stream carries neither the window nor a percentage, so
        this is the only source. It is `context_window`, never `max_context_window`,
        which differs per model and would understate the pressure (p0-12).
        """
        return self.context_window * self.effective_percent // 100


@dataclass(frozen=True)
class Role:
    name: str
    model: str
    effort: str


@dataclass(frozen=True)
class Routing:
    provider: str
    roles: Mapping[str, Role]
    models: Mapping[str, ModelFacts]
    usd_per_run: float
    usd_warn_at: float

    def role(self, name: str) -> Role:
        try:
            return self.roles[name]
        except KeyError:
            raise RoutingError(f"no role {name!r} in models.toml") from None

    def facts(self, slug: str) -> ModelFacts:
        try:
            return self.models[slug]
        except KeyError:
            raise RoutingError(f"no model {slug!r} in models.toml") from None


def load_model_cache(path: Path = MODEL_CACHE) -> dict[str, ModelFacts]:
    """The measured model list, as Codex itself records it.

    The field names are the ones P0-12 read out of the real file: efforts live under
    `supported_reasoning_levels` as `{effort, description}` objects and the default
    under `default_reasoning_level`, which is not what §4.5's prose implies.
    """
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, ModelFacts] = {}
    for model in raw.get("models", []):
        slug = model["slug"]
        out[slug] = ModelFacts(
            slug=slug,
            display=model.get("display_name", slug),
            default_effort=model.get("default_reasoning_level", "medium"),
            supported_efforts=tuple(
                level["effort"] for level in model.get("supported_reasoning_levels", [])
            ),
            context_window=int(model.get("context_window", 0)),
            effective_percent=int(model.get("effective_context_window_percent", 100)),
            hidden=model.get("visibility") == "hide",
        )
    return out


def _models_from_file(raw: Mapping[str, Any]) -> dict[str, ModelFacts]:
    out: dict[str, ModelFacts] = {}
    for slug, body in dict(raw.get("models", {})).items():
        entry = dict(body)
        out[slug] = ModelFacts(
            slug=slug,
            display=str(entry.get("display", slug)),
            default_effort=str(entry.get("default_effort", "medium")),
            supported_efforts=tuple(str(e) for e in entry.get("supported_efforts", ())),
            context_window=int(entry.get("context_window", 0)),
            effective_percent=int(entry.get("effective_percent", 100)),
            hidden=bool(entry.get("hidden", False)),
        )
    return out


def load_routing(path: Path, *, cache: Mapping[str, ModelFacts] | None = None) -> Routing:
    """Parse `models.toml` and apply §4.5's three validation rules.

    The catalogue used for rule 2 is the **live cache** when it is readable, because
    that is what the CLI will actually accept, and the file's own table otherwise. A
    difference between the two is drift rather than an error, and `factory doctor`
    reports it: refusing to start over a stale comment in a config file would be a
    worse failure than the drift.
    """
    return _validated(tomllib.loads(path.read_text(encoding="utf-8")), cache=cache)


def _validated(raw: dict[str, Any], *, cache: Mapping[str, ModelFacts] | None = None) -> Routing:
    """The rules themselves, over a parsed table. One body, two entry points.

    Split out of `load_routing` so `validate` can run it against text that is not on disk
    yet. Everything below this line is unchanged; the only thing the split buys is that
    there is exactly one copy of it.
    """
    roles = {
        name: Role(name=name, model=str(body["model"]), effort=str(body["effort"]))
        for name, body in dict(raw.get("roles", {})).items()
    }
    if not roles:
        raise RoutingError("models.toml declares no roles")

    file_models = _models_from_file(raw)
    measured = dict(cache) if cache is not None else load_model_cache()
    catalogue = measured or file_models
    if not catalogue:
        raise RoutingError(
            "no model catalogue: ~/.codex/models_cache.json is unreadable and "
            "models.toml declares no [models.*] table"
        )

    budget = dict(raw.get("budget", {}))
    usd_per_run = float(budget.get("usd_per_run", 0.0))
    usd_warn_at = float(budget.get("usd_warn_at", 0.0))

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
    # is as broken as one that does not exist, and the cache carries both facts.
    for role in roles.values():
        facts = catalogue.get(role.model)
        if facts is None:
            raise RoutingError(
                f"role {role.name!r} names model {role.model!r}, which is not in the "
                f"measured model list: {sorted(catalogue)}"
            )
        if facts.supported_efforts and role.effort not in facts.supported_efforts:
            raise RoutingError(
                f"role {role.name!r} asks {role.model!r} for effort {role.effort!r}; "
                f"it supports {list(facts.supported_efforts)}"
            )

    if builder and builder.effort == FORBIDDEN_BUILDER_EFFORT:
        raise RoutingError(
            "roles.builder.effort must never be 'ultra': it delegates tasks "
            "automatically, which spawns work the control plane did not schedule and "
            "cannot see. Use 'max' for the same reasoning depth."
        )

    # Rule 3.
    if usd_per_run <= 0:
        raise RoutingError("budget.usd_per_run must be greater than zero")
    if not usd_warn_at < usd_per_run:
        raise RoutingError(
            f"budget.usd_warn_at ({usd_warn_at}) must be below usd_per_run ({usd_per_run})"
        )

    return Routing(
        provider=str(dict(raw.get("defaults", {})).get("provider", "codex")),
        roles=roles,
        models=file_models or catalogue,
        usd_per_run=usd_per_run,
        usd_warn_at=usd_warn_at,
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


def validate(text: str, *, cache: Mapping[str, ModelFacts] | None = None) -> Routing:
    """§4.5's rules over a candidate table, with no file involved.

    This is the function `load_routing` should always have been, and its absence had a
    shape: the console had to write its candidate to a `NamedTemporaryFile`, parse it back
    through `load_routing`, and unlink it in a `finally` — a tempfile round-trip that was
    the interface leaking into the caller. `load_routing` is now read-then-validate, so
    the form's "no" and the tick's "no" are literally the same call rather than two calls
    that have to be kept the same.

    `tomllib.loads` first, so a syntax error says so before §4.5 does.
    """
    raw = tomllib.loads(text)
    return _validated(raw, cache=cache)
