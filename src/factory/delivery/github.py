"""GitHub delivery from the host — §13.2, §17.4.

The control plane performs every push and every PR write, from the host, with the existing
`gh` keyring credential. The sandbox gets no git remote credential. Every command here is an
argv list run with `subprocess`, never a shell string — the same rule the rest of the control
plane follows, and the reason `shell=True` appears nowhere in this repository.

The PR body is assembled in Python rather than a jinja2 template: the control plane is
**stdlib-only by design** (it holds the keychain credential and runs unattended), and adding
a template engine for one string is the kind of dependency that decision exists to refuse.
The §13.2 layout lives in `render_pr_body` and a golden test pins its output.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from factory.steps.review import FORCED

__all__ = [
    "GithubError",
    "create_pr",
    "edit_pr",
    "find_pr",
    "push",
    "render_pr_body",
]


class GithubError(Exception):
    """A `git` or `gh` command that failed, with its output attached."""


# --------------------------------------------------------------------------------
# §17.4 — push (hooks disabled), then the PR
# --------------------------------------------------------------------------------


def push(worktree: Path, branch: str) -> None:
    """`git -C <worktree> -c core.hooksPath=/dev/null push -u origin <branch>`.

    Hooks are disabled on the push path because the host's hooks are for the agent's
    uncommitted edits, not for the factory's own commit; a hook firing here would be the
    wrong layer and could block a push the gates already cleared.
    """
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(worktree),
            "-c",
            "core.hooksPath=/dev/null",
            "push",
            "-u",
            "origin",
            branch,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"git push failed in {worktree}:\n{proc.stderr.strip()}")


def find_pr(worktree: Path, branch: str) -> str | None:
    """The open PR URL for `branch`, or None. F16 — run before create so a second run edits
    rather than opening a duplicate."""
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--head",
            branch,
            "--json",
            "number,url",
            "--limit",
            "5",
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        # `gh` not authenticated, or no repo — treat as "no existing PR" and let create fail
        # loudly instead, which is the more useful signal.
        return None
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return rows[0]["url"] if rows else None


def create_pr(worktree: Path, *, base: str, head: str, title: str, body_file: Path) -> str:
    """`gh pr create --draft`. The PR opens as a draft; James marks it ready (§13.2).

    `--base` is the project's base branch (`v2`, never `main`); `--head` is the run's branch.
    The body comes from a file (`--body-file`) so a long body never enters the process table.
    """
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"gh pr create failed:\n{proc.stderr.strip()}")
    # `gh pr create` prints the new PR's URL to stdout.
    return proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""


def edit_pr(worktree: Path, number: int, *, body_file: Path) -> None:
    """`gh pr edit <number> --body-file`. Idempotent by PR number (§13.2)."""
    proc = subprocess.run(
        ["gh", "pr", "edit", str(number), "--body-file", str(body_file)],
        cwd=str(worktree),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GithubError(f"gh pr edit {number} failed:\n{proc.stderr.strip()}")


def pr_number(url: str) -> int | None:
    """Extract the PR number from a `gh` URL, for `edit_pr` on a re-run."""
    import re

    match = re.search(r"/pull/(\d+)", url)
    return int(match.group(1)) if match else None


# --------------------------------------------------------------------------------
# §13.2 — the PR body, in order
# --------------------------------------------------------------------------------


def render_pr_body(
    *,
    ticket: str,
    title: str,
    restatement: str,
    gates: list[dict[str, Any]],
    gate_verdict: str,
    review_summary: dict[str, Any] | None,
    redphase: dict[str, str] | None,
    out_of_scope: Sequence[str],
    artifact_path: str,
    tokens_in: int,
    tokens_out: int,
    usd: float | None,
) -> str:
    """Assemble the PR body in §13.2's order.

    `Fixes <TEAM-NUM>`, the one-paragraph restatement, the gate report table (every
    `not_applicable`/`unavailable`/`skipped` row with its caveat), the review summary (Tier-1
    findings + the skipped-Tier-2 rule name), the red-phase replay result, out-of-scope notes,
    the attempt artifact path, and the cost.
    """
    lines: list[str] = []
    lines.append(f"Fixes {ticket}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(restatement.strip() or f"{title}")
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    lines.append(f"Verdict: **{gate_verdict}**")
    lines.append("")
    lines.append("| gate | kind | status | caveat |")
    lines.append("| --- | --- | --- | --- |")
    for gate in gates:
        caveat = gate.get("caveat") or ""
        lines.append(
            f"| {gate.get('name', '?')} | {gate.get('kind', '?')} | "
            f"{gate.get('status', '?')} | {caveat} |"
        )
    lines.append("")
    lines.append("## Review")
    lines.append("")
    if review_summary:
        tier2 = review_summary.get("tier2", "no-trigger")
        if tier2 == "ran":
            lines.append("Tier 2 (full nine-axis review) ran.")
            ran_tier2 = True
        elif tier2 == FORCED:
            # `--full-review` asked for the fan-out; no trigger rule fired. Saying "skipped"
            # here — as the first completed Tier 2 (FRO-6) was reported — misstates that the
            # review ran, so it is said plainly.
            lines.append("Tier 2 ran (forced with `--full-review`; no trigger rule fired).")
            ran_tier2 = True
        else:
            lines.append(f"Tier 2 skipped — rule: `{tier2}`.")
            ran_tier2 = False
        findings = review_summary.get("findings", [])
        # When Tier 2 ran, `findings` aggregates both tiers; calling them "Tier-1 findings"
        # would hide that the fan-out produced any. Only a skipped Tier 2 leaves the
        # findings as Tier-1 alone.
        label = "Findings" if ran_tier2 else "Tier-1 findings"
        if findings:
            lines.append("")
            lines.append(f"{label}:")
            for f in findings:
                lines.append(
                    f"- [{f.get('severity', '?')}] {f.get('file', '?')}"
                    f"{':' + str(f.get('line')) if f.get('line') else ''}: {f.get('summary', '')}"
                )
        else:
            lines.append("")
            lines.append("No findings." if ran_tier2 else "Tier-1 found no defects.")
    else:
        lines.append("_No review summary recorded._")
    lines.append("")
    lines.append("## Red-phase replay (§15.3)")
    lines.append("")
    if redphase:
        lines.append(f"Status: **{redphase.get('status', '?')}** — {redphase.get('reason', '')}")
    else:
        lines.append("_Not run (no behaviour change reported)._")
    lines.append("")
    lines.append("## Out of scope")
    lines.append("")
    if out_of_scope:
        for note in out_of_scope:
            lines.append(f"- {note}")
    else:
        lines.append("_None recorded._")
    lines.append("")
    lines.append("## Evidence")
    lines.append("")
    lines.append(f"Attempt artifact: `{artifact_path}`")
    lines.append("")
    lines.append("## Cost")
    lines.append("")
    cost = f"${usd:.2f}" if usd is not None else "token counts only (no price row)"
    lines.append(f"Tokens: in {tokens_in}, out {tokens_out} — {cost}")
    lines.append("")
    lines.append("_This PR was opened by the factory as a **draft**. Merge is a human's._")
    return "\n".join(lines) + "\n"
