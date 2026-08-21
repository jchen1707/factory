"""§25 — the boundaries, proved by grep over `src/`.

These are the four claims the definition of done says must be provable without running
anything: the factory cannot merge, cannot approve, cannot force-push, and cannot file a
ticket. A grep is a blunt instrument, which is the point — it fails on the *shape* of
the call, so a future author cannot add one without also editing this file and noticing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
SOURCES = sorted(SRC.rglob("*.py"))


def _bodies() -> list[tuple[Path, str]]:
    out = []
    for path in SOURCES:
        text = path.read_text(encoding="utf-8")
        # Comments and docstrings *name* these things constantly — that is the file
        # saying why it does not do them. Only code is searched.
        stripped = re.sub(r'"""(?:.|\n)*?"""', "", text)
        stripped = re.sub(r"^\s*#.*$", "", stripped, flags=re.MULTILINE)
        out.append((path, stripped))
    return out


def test_there_are_sources_to_search() -> None:
    assert len(SOURCES) > 15


@pytest.mark.parametrize(
    "forbidden",
    [
        '"merge"',
        '"--approve"',
        '"force-with-lease"',
        '"reset", "--hard"',
    ],
)
def test_no_forbidden_git_or_gh_argument_appears(forbidden: str) -> None:
    hits = [str(path) for path, body in _bodies() if forbidden in body]
    assert not hits, f"{forbidden} appears in {hits}"


def test_force_never_appears_near_a_push() -> None:
    # `--force` on its own is legitimate: `git worktree remove --force` is how F15
    # ends, and it is the alternative to `rm -rf`. What must never exist is a force
    # anywhere near a push.
    pattern = re.compile(
        r"push[^\n]{0,120}(--force|-f\b|force-with-lease)"
        r"|(--force|-f\b)[^\n]{0,120}push"
    )
    for path, body in _bodies():
        assert not pattern.search(body), f"a forced push appears in {path}"


def test_there_is_no_create_issue_code_path() -> None:
    # Ticket creation is an alignment decision and alignment waits for the user
    # (§13.1). It happens in James's mattpocock-skills intake, not here.
    for path, body in _bodies():
        for mutation in ("issueCreate", "createIssue", "issue_create"):
            assert mutation not in body, f"{mutation} appears in {path}"


def test_the_only_linear_mutations_are_the_two_the_plan_names() -> None:
    linear = (SRC / "factory" / "intake" / "linear.py").read_text()
    mutations = set(re.findall(r"\bmutation\(\$[^)]*\)\s*\{\s*(\w+)", linear))
    assert mutations == {"commentCreate", "issueUpdate"}


def test_no_module_shells_out_through_a_shell() -> None:
    # Gates are declared as argv, never shell strings (§2.3). The one `/bin/sh -lc`
    # in the codebase is the *sandbox* wrapper, which is a script the factory composes
    # from quoted parts and hands to the VM, not a host shell invocation.
    for path, body in _bodies():
        assert "shell=True" not in body, f"shell=True appears in {path}"


def test_nothing_writes_to_the_codex_config() -> None:
    # Writing to a user's agent-trust store from an automated process defeats the
    # trust store. The factory reads that file in exactly one place and never opens it
    # for writing.
    for path, body in _bodies():
        if ".codex" not in body:
            continue
        assert not re.search(r"config\.toml[\"'][^\n]*write", body), path
        assert "write_text" not in body or "codex" not in path.name


def test_the_sandbox_namespace_appears_only_as_a_factory_prefix() -> None:
    from factory.policy import sandbox_is_factory_owned

    assert not sandbox_is_factory_owned("codex-python-harness")
    assert sandbox_is_factory_owned("factory-build-python-harness")
