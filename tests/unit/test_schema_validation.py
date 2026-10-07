"""The structured-answer validator is pinned here, and to nothing else."""

from __future__ import annotations

from pathlib import Path

import pytest

from factory.agent.base import SchemaInvalid, SchemaUnsupported, validate_against_schema

HOME = Path(__file__).resolve().parents[2]


def test_the_validator_refuses_a_keyword_it_does_not_implement() -> None:
    # A partial JSON-Schema implementation that skips what it does not know is the
    # "looks green, proves nothing" failure arriving through the check meant to
    # prevent it.
    with pytest.raises(SchemaUnsupported):
        validate_against_schema({"a": 1}, {"type": "object", "patternProperties": {}})


def _implement_schema() -> dict:
    import json

    return json.loads((HOME / "schemas" / "implement_result.schema.json").read_text())


#: A complete `implement_result`, every required key present.
_COMPLETE_RESULT = {
    "status": "implemented",
    "summary": "did the thing",
    "files_changed": ["src/app/main.py"],
    "tests_added": ["tests/test_main.py"],
    "out_of_scope": [],
    "behaviour_changed": True,
    "seam_confirmed": True,
    "tdd_used": True,
    "gates_run": ["ruff check"],
    "blocked_reason": None,
    "docs_updated": [],
}


def test_behaviour_changed_is_required() -> None:
    schema = _implement_schema()
    without = {k: v for k, v in _COMPLETE_RESULT.items() if k != "behaviour_changed"}
    with pytest.raises(SchemaInvalid, match="behaviour_changed"):
        validate_against_schema(without, schema)
    validate_against_schema(_COMPLETE_RESULT, schema)


def test_every_property_is_required() -> None:
    """Every key in `properties` is in `required`, so the builder cannot leave a field
    out and have the control plane read its absence as an answer.

    A field with nothing to say uses an empty array or an explicit null instead of
    being absent, so nothing is lost by requiring all of them. Measured 2026-08-21 as a
    strict-schema rejection inside the sandbox, after the run had spent a sandbox, a
    worktree and a detached exec on it; kept as the schema's own invariant.
    """
    schema = _implement_schema()
    assert set(schema["required"]) == set(schema["properties"])


def test_a_boolean_is_not_accepted_where_a_number_is_wanted() -> None:
    with pytest.raises(SchemaInvalid, match="boolean"):
        validate_against_schema(True, {"type": "integer"})


def test_schema_unique_items_uses_json_equality() -> None:
    schema = {"type": "array", "uniqueItems": True}
    validate_against_schema([True, 1, "1"], schema)
    validate_against_schema([{"x": True}, {"x": 1}], schema)
    for repeated in (["src", "src"], [1, 1.0], [{"x": [1]}, {"x": [1.0]}]):
        with pytest.raises(SchemaInvalid, match="uniqueItems"):
            validate_against_schema(repeated, schema)
