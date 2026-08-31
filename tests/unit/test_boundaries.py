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


#: Every `gh` verb pair that appears in `src/`. Derived, not enumerated: the test below
#: reads the argv literals and compares the *set*, so adding a `gh` call the factory has
#: no business making fails here rather than passing unnoticed. §25's four claims are
#: about shapes; this is the same idea one level up — the surface, not one bad token.
_GH_ARGV = re.compile(r'"gh"\s*,\s*"([a-z-]+)"\s*,\s*"([a-z-]+)"')

ALLOWED_GH_CALLS = {
    ("auth", "status"),  # doctor
    ("pr", "list"),  # the F16 duplicate guard, and the poller's open-PR scan
    ("pr", "create"),  # deliver
    ("pr", "edit"),  # deliver, on a re-delivery
    ("pr", "view"),  # complete — evidence that James merged
}


def test_the_factory_never_merges_a_pull_request() -> None:
    """`gh pr merge` has no code path, and neither has anything else unaccounted for.

    "The factory never merges" was two docstrings (`steps/complete.py:3`,
    `steps/deliver.py:4`) and an argument grep. The docstrings cannot fail. Moving PRs to
    ready-for-review takes the factory one step nearer the merge button, so the guarantee
    gets read off the source here instead: five verbs, and `merge` is not one of them.
    """
    found: set[tuple[str, str]] = set()
    for _, body in _bodies():
        found |= set(_GH_ARGV.findall(body))

    assert ("pr", "merge") not in found
    assert ("pr", "review") not in found  # approving is James's too (§24.8)
    assert found <= ALLOWED_GH_CALLS, f"unaccounted gh calls: {sorted(found - ALLOWED_GH_CALLS)}"
    # …and the allow-list is not quietly describing calls that no longer exist.
    assert found == ALLOWED_GH_CALLS


def test_no_pull_request_is_merged_through_the_rest_api_either() -> None:
    # The argument grep above catches `["gh", "pr", "merge"]`. It does not catch the
    # other spelling of the same act, where the verb is a path segment rather than an
    # argv token: `gh api --method PUT repos/{o}/{r}/pulls/{n}/merge`.
    for path, body in _bodies():
        assert not re.search(r"pulls/[^\s\"']*/merge|/merge[\"'/]", body), path


#: The `glab` half of the same surface. A second forge adapter walks straight past
#: `_GH_ARGV` — `glab mr merge` contains no `"gh"` literal — so "the factory never merges"
#: would have quietly become a claim about GitHub only, with no test failing to say so.
#: Every forge added from here needs its pair of entries in this file, and that is the
#: whole reason the adapters are kept to six functions.
_GLAB_ARGV = re.compile(r'"glab"\s*,\s*"([a-z-]+)"\s*,\s*"([a-z-]+)"')

ALLOWED_GLAB_CALLS = {
    ("mr", "list"),  # the F16 duplicate guard
    ("mr", "create"),  # deliver
    ("mr", "update"),  # deliver, on a re-delivery — `gh pr edit`'s counterpart
    ("mr", "view"),  # complete — evidence that James merged
}


def test_the_factory_never_merges_a_merge_request_either() -> None:
    """The GitLab adapter is held to the same five-verb surface as the GitHub one.

    GitLab spells approval `glab mr approve` and merge `glab mr merge`; it also has
    `glab mr merge --when-pipeline-succeeds`, which is the same act deferred, and
    `--auto-merge`, which is the same act named differently. All of them are absent, and
    the set comparison below is what keeps them absent rather than this docstring.
    """
    found: set[tuple[str, str]] = set()
    for _, body in _bodies():
        found |= set(_GLAB_ARGV.findall(body))

    assert ("mr", "merge") not in found
    assert ("mr", "approve") not in found  # approving is James's too (§24.8)
    assert found <= ALLOWED_GLAB_CALLS, (
        f"unaccounted glab calls: {sorted(found - ALLOWED_GLAB_CALLS)}"
    )
    assert found == ALLOWED_GLAB_CALLS


def test_no_merge_request_is_merged_through_the_gitlab_api_either() -> None:
    # `PUT /projects/{id}/merge_requests/{iid}/merge`, and the auto-merge flags that are
    # the same act with a delay on it. The `/merge[\"'/]` half of the GitHub test above
    # already catches the bare path segment; these are the spellings it does not.
    for path, body in _bodies():
        assert not re.search(r"merge_requests/[^\s\"']*/merge", body), path
        for flag in ("--auto-merge", "--when-pipeline-succeeds", "merge_when_pipeline"):
            assert flag not in body, f"{flag} appears in {path}"


def test_no_push_option_opens_a_merge_request_as_a_side_effect() -> None:
    """GitLab can open an MR from the push itself, which would bypass the §13.2 body.

    `git push -o merge_request.create` races the F16 duplicate guard and produces an MR
    with no gate report, no review summary and no cost — the evidence the PR body exists to
    carry. `gitlab.push` documents the omission; this is what enforces it.
    """
    for path, body in _bodies():
        assert "merge_request.create" not in body, path
        assert "merge_request.merge_when" not in body, path


def test_no_source_records_a_transition_with_no_source_state() -> None:
    """`from_state=None` is the one way past `record_transition`'s §5.2 check.

    That is deliberate — it means "place this run here", which is not a hop and has no
    edge to check. Nothing in `src/` places a run: a run is created at `approved` by
    `insert_run` and moves only by transitions. Tests use it to set a scenario up.

    Without this grep the guard is advisory: a call site that found the table
    inconvenient could pass `None` and write whatever it liked, which is exactly the
    convention-shaped enforcement the check exists to replace.
    """
    for path, body in _bodies():
        assert "from_state=None" not in body, f"{path} places a run instead of moving it"


def test_no_run_pipeline_module_simulates_itself() -> None:
    """§25, extended: `--dry-run` is gone from the run pipeline and must stay gone.

    It was an *untested* simulation of a state machine — `Context.dry_run`,
    `shadow_state`, `shadow_worktree`, `shadow_branch`, `planned` and `would()`, ~130
    sites across 17 modules, a second implementation nobody had ever checked agreed with
    the first. Measured 2026-08-27: **no test drove the run pipeline with
    `dry_run=True`.** That is the exact drift the three-layer scheme exists to end,
    living inside the layer that exists to end it.

    A grep rather than a comment, because the shape is what comes back: one `if
    ctx.dry_run:` added to one step reads as a two-line convenience and is the whole
    thing again.

    `gc.py` is deliberately exempt. `gc --dry-run` is a real sweep planner, it is tested,
    and `gc.sweep` takes `dry_run` as its own parameter and never sees a `Context`.
    """
    for path, body in _bodies():
        if path.name == "gc.py":
            continue
        assert "shadow_" not in body, f"{path} carries a shadow of the real state"
        assert "ctx.dry_run" not in body, f"{path} branches on a simulated run"
        assert ".would(" not in body, f"{path} records a command it is not going to run"
