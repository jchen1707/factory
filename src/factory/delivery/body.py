"""The pull-request / merge-request body — §13.2.

Forge-neutral on purpose. The body is evidence the run recorded (gates, review, red-phase
replay, cost) and none of it is GitHub's or GitLab's; only the *delivery* of it differs, so
the adapters in `github.py` and `gitlab.py` share this one renderer rather than each
growing a copy that drifts.

The body is assembled in Python rather than a jinja2 template: the control plane is
**stdlib-only by design** (it holds the keychain credential and runs unattended), and adding
a template engine for one string is the kind of dependency that decision exists to refuse.
A golden test pins the output.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from factory.steps.review import FORCED

__all__ = ["render_pr_body"]


def render_pr_body(
    *,
    ticket: str,
    title: str,
    restatement: str,
    gates: list[dict[str, Any]],
    gate_verdict: str,
    review_summary: dict[str, Any] | None,
    redphase: dict[str, str] | None,
    escalations: Sequence[dict[str, str]] = (),
    out_of_scope: Sequence[str],
    artifact_path: str,
    tokens_in: int,
    tokens_out: int,
    usd: float | None,
) -> str:
    """Assemble the PR body in §13.2's order.

    `Fixes <TEAM-NUM>`, the one-paragraph restatement, the gate report table (every
    `not_applicable`/`unavailable`/`skipped` row with its caveat), the review summary (Tier-1
    findings + the skipped-Tier-2 rule name), the red-phase replay result, any §15.3
    escalation a human cleared to let the review run, out-of-scope notes, the attempt
    artifact path, and the cost.

    `escalations` is empty on the ordinary run — the guards did not fire — and a reader
    should be able to tell the two apart without knowing the section exists, so the heading
    is emitted only when there is something under it.
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
    if escalations:
        lines.append("## Cleared escalations (§15.3)")
        lines.append("")
        lines.append(
            "A guard stopped this run and a human cleared it. The review below ran "
            "*after* that judgement, not instead of it."
        )
        lines.append("")
        for esc in escalations:
            note = esc.get("note", "").strip()
            lines.append(f"- **`{esc.get('rule', '?')}`** cleared{f' — {note}' if note else ''}")
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
    lines.append("_Opened by the factory, **ready for review**. Merge is a human's._")
    return "\n".join(lines) + "\n"
