"""§21.1 — slugs, branch names, and the base-ref rule."""

from __future__ import annotations

import pytest

from factory.repo import branch_name, branch_type_for_labels, slugify


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Application skeleton: Settings, structured logging, app factory",
            "application-skeleton-settings-structured",
        ),
        ("Search your Projects by name", "search-your-projects-by-name"),
        (
            "raw ZodError escapes instead of ValidationError",
            "raw-zoderror-escapes-instead-of-validati",
        ),
        ("Fix   the    spacing", "fix-the-spacing"),
        ("Trailing punctuation!!!", "trailing-punctuation"),
        ("--leading and trailing--", "leading-and-trailing"),
        ("Unicode — em dashes and ünïcode", "unicode-em-dashes-and-n-code"),
        ("ALLCAPS TITLE", "allcaps-title"),
        ("123 numbers stay", "123-numbers-stay"),
        ("a" * 80, "a" * 40),
    ],
)
def test_slugify(title: str, expected: str) -> None:
    assert slugify(title) == expected


def test_slug_never_ends_in_a_hyphen_after_truncation() -> None:
    # Truncation lands mid-separator here; a dangling hyphen would show up in the
    # branch name and therefore in every PR URL.
    title = "abcdefghij abcdefghij abcdefghij abcdefghij more words"
    assert not slugify(title).endswith("-")


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["Feature", "ready-for-agent"], "feat"),
        (["Bug"], "fix"),
        (["bug"], "fix"),
        (["ready-for-agent"], "chore"),
        ([], "chore"),
    ],
)
def test_branch_type(labels: list[str], expected: str) -> None:
    assert branch_type_for_labels(labels) == expected


def test_branch_name_matches_both_agents_md_conventions() -> None:
    assert (
        branch_name(
            "feat", "BAC-4", "Application skeleton: Settings, structured logging, app factory"
        )
        == "feat/BAC-4-application-skeleton-settings-structured"
    )
    assert branch_name("fix", "FRO-8", "raw ZodError escapes").startswith("fix/FRO-8-")
