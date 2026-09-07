"""The agent contract — prompt in, structured result plus usage out."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "AgentAdapter",
    "AgentInvocation",
    "SchemaInvalid",
    "SchemaUnsupported",
    "Transcript",
    "Usage",
    "validate_against_schema",
]


@dataclass(frozen=True)
class Usage:
    """The usage object as P0-7 measured it, and only as it measured it.

    `total_tokens` is **absent** from the real stream despite appearing in the binary's
    symbol table, so it is summed here rather than read. `reasoning_output_tokens` was
    0 on runs that plainly reasoned, so it is a lower bound for cost and never a
    measurement.
    """

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_input_tokens=self.cache_write_input_tokens + other.cache_write_input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_output_tokens=self.reasoning_output_tokens + other.reasoning_output_tokens,
        )


@dataclass(frozen=True)
class Transcript:
    """What one agent run produced, read back off the filesystem."""

    session_id: str | None
    usage: Usage
    failed: bool
    failure: str | None
    #: Items of `type: "error"` that are **not** failures. The
    #: `--dangerously-bypass-hook-trust` warnings arrive in exactly that shape, twice,
    #: on a run that then succeeds; only `turn.failed` and the exit code mean failure.
    error_items: tuple[str, ...] = ()
    #: Hook denials never reach the JSON stream — they are stderr lines from
    #: `codex_core::tools::router`. Without this the evidence trail loses every block.
    hook_denials: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    files_touched: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentInvocation:
    """Everything one `codex exec` needs, resolved before anything is spawned."""

    model: str
    effort: str
    workdir: str
    prompt_path: Path
    schema_path: Path
    output_path: Path
    events_path: Path
    stderr_path: Path
    exit_path: Path
    heartbeat_path: Path
    #: Where the wrapper publishes the agent's in-VM process group, so a timeout signals
    #: *this* run and not every codex in a shared build sandbox (`detached_shell_script`).
    pgid_path: Path
    vault_directory: str
    env: Mapping[str, str] = field(default_factory=dict)
    resume_session: str | None = None


class AgentAdapter(Protocol):
    def command(self, invocation: AgentInvocation) -> Sequence[str]: ...

    def wrapper_script(self, invocation: AgentInvocation) -> str: ...

    def read_transcript(self, events: Path, stderr: Path) -> Transcript: ...


# --------------------------------------------------------------------------------
# Validating what the agent returned
# --------------------------------------------------------------------------------


class SchemaInvalid(Exception):
    """The agent's structured result does not match the schema it was given. F6."""


class SchemaUnsupported(Exception):
    """The schema uses a keyword this validator does not implement.

    Raised rather than ignored, and that is the entire design of the validator below.
    A partial JSON-Schema implementation that skips what it does not know is the
    "looks green, proves nothing" failure this system exists to catch, arriving through
    the check meant to prevent it. If a schema grows a keyword, this raises at the first
    run and someone implements it.
    """


_SUPPORTED = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "enum",
        "const",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "maxLength",
        "minLength",
        "minimum",
        "minItems",
        "uniqueItems",
    }
)

_TYPES: dict[str, Any] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def validate_against_schema(instance: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Validate `instance`, refusing any schema keyword that is not implemented."""
    unknown = set(schema) - _SUPPORTED
    if unknown:
        raise SchemaUnsupported(f"{path}: schema uses unimplemented keywords {sorted(unknown)}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaInvalid(f"{path}: expected {schema['const']!r}, got {instance!r}")

    allowed: Any = schema.get("enum")
    if allowed is not None and instance not in allowed:
        raise SchemaInvalid(f"{path}: {instance!r} is not one of {allowed}")

    declared: Any = schema.get("type")
    if declared is not None:
        names = [declared] if isinstance(declared, str) else list(declared)
        expected = tuple(
            t
            for name in names
            for t in (_TYPES[name] if isinstance(_TYPES[name], tuple) else (_TYPES[name],))
        )
        # `bool` is a subclass of `int`, and a boolean where a number is wanted is
        # exactly the confusion a schema is meant to catch.
        if isinstance(instance, bool) and bool not in expected:
            raise SchemaInvalid(f"{path}: expected {names}, got a boolean")
        if not isinstance(instance, expected):
            raise SchemaInvalid(f"{path}: expected {names}, got {type(instance).__name__}")

    if isinstance(instance, str):
        limit: Any = schema.get("maxLength")
        if limit is not None and len(instance) > int(limit):
            raise SchemaInvalid(f"{path}: longer than maxLength {limit}")
        floor: Any = schema.get("minLength")
        if floor is not None and len(instance) < int(floor):
            raise SchemaInvalid(f"{path}: shorter than minLength {floor}")

    if isinstance(instance, list):
        if schema.get("uniqueItems"):
            values = [_json_value(item) for item in instance]
            if len(set(values)) != len(values):
                raise SchemaInvalid(f"{path}: duplicate uniqueItems")
        min_items: Any = schema.get("minItems")
        if min_items is not None and len(instance) < int(min_items):
            raise SchemaInvalid(f"{path}: fewer than minItems {min_items}")
        item_schema: Any = schema.get("items")
        if item_schema is not None:
            for index, item in enumerate(instance):
                validate_against_schema(item, item_schema, f"{path}[{index}]")

    if isinstance(instance, dict):
        properties: Mapping[str, Mapping[str, Any]] = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in instance:
                raise SchemaInvalid(f"{path}: missing required property {name!r}")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise SchemaInvalid(f"{path}: unexpected properties {sorted(extra)}")
        for name, value in instance.items():
            if name in properties:
                validate_against_schema(value, properties[name], f"{path}.{name}")


def _json_value(value: Any) -> Any:
    """Hashable JSON equality: booleans differ from numbers, numeric 1 equals 1.0."""
    if isinstance(value, dict):
        return ("object", tuple(sorted((key, _json_value(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ("array", tuple(_json_value(item) for item in value))
    if isinstance(value, bool):
        return ("boolean", value)
    return ("scalar", value)
