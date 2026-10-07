"""The structured-answer contract: what an agent returned, checked against its schema."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "SchemaInvalid",
    "SchemaUnsupported",
    "validate_against_schema",
]


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
