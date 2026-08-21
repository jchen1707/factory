"""Linear — the read side, and the five writes the control plane makes — §13.1.

**The factory reads tickets. It never creates one.** There is no create-issue mutation
in this file and a test greps `src/` to keep it that way: ticket creation is an
alignment decision, and alignment waits for the user. When an agent finds a bug next to
its ticket, it lands in the structured result, the PR body and the run record, and
James's separate `mattpocock-skills` intake decides whether it becomes an issue.

The credential is read from the macOS keychain **at use time**, held in a local for the
duration of one HTTP call, and never logged, written, exported or passed into a
sandbox. The control plane is not sandboxed, which is the whole reason this works.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factory.machine import Blocked

__all__ = [
    "BLOCKING_LABELS",
    "Condition",
    "Issue",
    "LinearClient",
    "LinearError",
    "evaluate_eligibility",
    "keychain_secret",
]

API_URL = "https://api.linear.app/graphql"
KEYCHAIN_SERVICE = "factory-linear"

#: §7.1 condition 7. Any of these means a human already said "not yet".
BLOCKING_LABELS = frozenset({"needs-info", "needs-triage", "ready-for-human", "wontfix"})

#: §7.1 condition 1. The label is the signature that starts the factory, and nothing
#: else is.
READY_LABEL = "ready-for-agent"

ACCEPTANCE_RE = re.compile(r"##\s*Acceptance|Acceptance\s+criteria", re.IGNORECASE)

MIN_SPEC_CHARS = 200


class LinearError(Exception):
    """The API said no, or could not be reached."""


def keychain_secret(service: str = KEYCHAIN_SERVICE) -> str:
    """Read a secret from the macOS keychain. The value is returned, never printed."""
    proc = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            os.environ.get("USER", ""),
            "-s",
            service,
            "-w",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise LinearError(
            f"no keychain item for service {service!r}. Store one with: "
            f'security add-generic-password -a "$USER" -s {service} -w'
        )
    return proc.stdout.strip()


@dataclass(frozen=True)
class Issue:
    identifier: str
    title: str
    description: str
    url: str
    state_name: str
    state_type: str
    team_key: str
    team_id: str
    labels: tuple[str, ...]
    parent_identifier: str | None
    parent_title: str | None
    parent_description: str
    comments: tuple[tuple[str, str, str], ...] = ()  # (author, created_at, body)
    siblings: tuple[tuple[str, str, str], ...] = ()  # (identifier, state, title)

    @property
    def acceptance_criteria(self) -> list[str]:
        """The checklist lines under the acceptance section, in order.

        Used for the planning-size heuristic and for the context file; deliberately not
        used to judge whether the work is done, which is the gate report's job.
        """
        lines = self.description.splitlines()
        out: list[str] = []
        inside = False
        for line in lines:
            if line.startswith("#") and ACCEPTANCE_RE.search(line):
                inside = True
                continue
            if inside and line.startswith("#"):
                break
            if inside and line.strip().startswith(("- [", "* [", "- ", "* ")):
                out.append(line.strip())
        return out


_ISSUE_QUERY = """
query($id: String!) {
  issue(id: $id) {
    identifier title description url
    state { name type }
    team { key id }
    labels { nodes { name } }
    parent {
      identifier title description
      children { nodes { identifier title state { name } } }
    }
    comments { nodes { body createdAt user { name } } }
  }
}
"""

_STATES_QUERY = """
query($teamId: String!) {
  team(id: $teamId) { states { nodes { id name type position } } }
}
"""

_COMMENT_MUTATION = """
mutation($issueId: String!, $body: String!) {
  commentCreate(input: { issueId: $issueId, body: $body }) {
    success comment { id }
  }
}
"""

_STATE_MUTATION = """
mutation($issueId: String!, $stateId: String!) {
  issueUpdate(id: $issueId, input: { stateId: $stateId }) {
    success issue { id state { name } }
  }
}
"""

_ISSUE_ID_QUERY = """
query($id: String!) { issue(id: $id) { id state { name } } }
"""


class LinearClient:
    """Every Linear call the factory makes. Nothing here creates an issue.

    `transport` exists so the whole intake path is testable with no network: the tests
    hand in a function, and the real one is the only thing that touches urllib.
    """

    def __init__(
        self,
        *,
        service: str = KEYCHAIN_SERVICE,
        transport: Callable[[str, dict[str, Any], str], dict[str, Any]] | None = None,
    ) -> None:
        self._service = service
        self._transport = transport or self._http

    # -- plumbing -----------------------------------------------------------------

    @staticmethod
    def _http(query: str, variables: dict[str, Any], key: str) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        request = urllib.request.Request(
            API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Authorization": key},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise LinearError(f"linear unreachable: {exc}") from exc
        if "errors" in payload:
            raise LinearError(f"linear error: {payload['errors']}")
        data: dict[str, Any] = payload["data"]
        return data

    def _call(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        return self._transport(query, variables, keychain_secret(self._service))

    # -- reads --------------------------------------------------------------------

    def issue(self, identifier: str) -> Issue:
        data = self._call(_ISSUE_QUERY, {"id": identifier})
        node = data.get("issue")
        if not node:
            raise Blocked("no-such-ticket", identifier)
        parent = node.get("parent") or {}
        siblings = tuple(
            (child["identifier"], child["state"]["name"], child["title"])
            for child in (parent.get("children") or {}).get("nodes", [])
            if child["identifier"] != node["identifier"]
        )
        comments = tuple(
            ((c.get("user") or {}).get("name", "unknown"), c["createdAt"], c["body"])
            for c in (node.get("comments") or {}).get("nodes", [])
        )
        return Issue(
            identifier=node["identifier"],
            title=node["title"],
            description=node.get("description") or "",
            url=node.get("url", ""),
            state_name=node["state"]["name"],
            state_type=node["state"]["type"],
            team_key=node["team"]["key"],
            team_id=node["team"]["id"],
            labels=tuple(label["name"] for label in node["labels"]["nodes"]),
            parent_identifier=parent.get("identifier"),
            parent_title=parent.get("title"),
            parent_description=parent.get("description") or "",
            comments=comments,
            siblings=siblings,
        )

    def issue_uuid(self, identifier: str) -> tuple[str, str]:
        """`(uuid, current state name)`. Mutations need the uuid, not the identifier."""
        data = self._call(_ISSUE_ID_QUERY, {"id": identifier})
        node = data.get("issue")
        if not node:
            raise Blocked("no-such-ticket", identifier)
        return node["id"], node["state"]["name"]

    def workflow_state_id(self, team_id: str, name: str) -> str:
        data = self._call(_STATES_QUERY, {"teamId": team_id})
        nodes = ((data.get("team") or {}).get("states") or {}).get("nodes", [])
        for node in nodes:
            if node["name"].lower() == name.lower():
                return node["id"]
        raise LinearError(
            f"team has no workflow state named {name!r}; it has {[n['name'] for n in nodes]}"
        )

    def comment_marker_present(self, identifier: str, marker: str) -> bool:
        """Reconciliation for a comment effect — §16.2 step 4.

        The marker is an HTML comment in the body: invisible to a reader, exact for a
        search. Finding it means the write already happened, so the ledger row is
        confirmed rather than the comment repeated.
        """
        return any(marker in body for _, _, body in self.issue(identifier).comments)

    # -- writes -------------------------------------------------------------------

    def add_comment(self, issue_uuid: str, body: str) -> str | None:
        data = self._call(_COMMENT_MUTATION, {"issueId": issue_uuid, "body": body})
        result = data.get("commentCreate") or {}
        if not result.get("success"):
            raise LinearError(f"commentCreate failed: {data}")
        return (result.get("comment") or {}).get("id")

    def move_state(self, issue_uuid: str, state_id: str) -> str:
        data = self._call(_STATE_MUTATION, {"issueId": issue_uuid, "stateId": state_id})
        result = data.get("issueUpdate") or {}
        if not result.get("success"):
            raise LinearError(f"issueUpdate failed: {data}")
        return ((result.get("issue") or {}).get("state") or {}).get("name", "")


# --------------------------------------------------------------------------------
# §7.1 — the intake contract
# --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Condition:
    number: int
    label: str
    passed: bool
    #: `None` means "not eligible, no row created, log once" — the ticket was never
    #: meant for the factory. A slug means "create the row and block on it", which is a
    #: ticket that was meant for the factory and is not ready.
    block_reason: str | None = None


@dataclass(frozen=True)
class RepoFacts:
    """What the intake contract needs from git and gh, gathered by the caller.

    Passed in rather than fetched here so the contract is a pure function over already
    known facts: the whole of §7.1 is then testable without a network or a checkout.
    """

    tracker_team: str | None
    open_pr_heads: tuple[str, ...] = ()
    base_ref_subjects: tuple[str, ...] = ()
    remote_branches: tuple[str, ...] = ()
    expected_branch: str = ""
    known_teams: frozenset[str] = field(default_factory=frozenset)


def evaluate_eligibility(issue: Issue, facts: RepoFacts) -> list[Condition]:
    """§7.1's nine conditions, plus the tenth P0-11 measured into the plan.

    Conditions 4 to 6 are the operational form of "the factory must not start from an
    unreviewed issue": cheap, deterministic, and they fail loudly.
    """
    labels = {label.lower() for label in issue.labels}

    conditions = [
        Condition(1, f"label {READY_LABEL!r} present", READY_LABEL in labels, None),
        Condition(
            2,
            f"workflow state is Todo (is {issue.state_name!r})",
            issue.state_name == "Todo",
            "state-not-todo",
        ),
        Condition(
            3,
            f"team {issue.team_key!r} is in the registry",
            issue.team_key in facts.known_teams,
            None,
        ),
        Condition(4, "has a parent issue", issue.parent_identifier is not None, "no-parent-spec"),
        Condition(
            5,
            f"parent description is at least {MIN_SPEC_CHARS} characters",
            len(issue.parent_description) >= MIN_SPEC_CHARS,
            "empty-spec",
        ),
        Condition(
            6,
            "description has an acceptance-criteria section",
            bool(ACCEPTANCE_RE.search(issue.description)),
            "no-acceptance-criteria",
        ),
        Condition(
            7,
            "no blocking label",
            not (labels & BLOCKING_LABELS),
            None,
        ),
        # Condition 8, corrected. `gh pr list --search <identifier>` is a fuzzy
        # full-text match: P0-11 measured it returning a PR that mentioned neither
        # identifier it was asked about, which would produce a false `duplicate-pr`
        # block. The head branch is exact, and it is what a duplicate would actually
        # share.
        Condition(
            8,
            "no open PR on this ticket's branch",
            facts.expected_branch not in facts.open_pr_heads,
            "duplicate-pr",
        ),
        Condition(
            9,
            f"the repo's tracker.team equals {issue.team_key!r}",
            facts.tracker_team == issue.team_key,
            "team-repo-mismatch",
        ),
        # Condition 10, the one P0-11 added: the work must not already be on the base
        # ref. Matched against commit **subjects** only — the naive body search returns
        # a commit that merely mentions the ticket in a retro paragraph, and a false
        # `already-implemented` stops a legitimate run with a reason a human then has
        # to disprove.
        Condition(
            10,
            "the identifier does not appear in a commit subject on the base ref",
            not facts.base_ref_subjects,
            "already-implemented",
        ),
    ]
    return conditions


def eligibility_verdict(conditions: Sequence[Condition]) -> tuple[bool, str | None, list[str]]:
    """`(eligible, block_reason or None, the failed labels)`.

    A failed condition with no `block_reason` means the ticket is not the factory's to
    look at — no row, no comment, one log line. A failed condition *with* one means the
    ticket is the factory's and is not ready, which is a `blocked` run a human can see.

    A reasonless failure **wins** over a reasoned one, and the order matters. The three
    reasonless conditions are the label, the team and the blocking labels: each of them
    says the ticket was never handed to the factory. Blocking such a ticket would be
    the factory commenting on somebody else's work because it noticed their spec was
    short.
    """
    failed = [c for c in conditions if not c.passed]
    if not failed:
        return True, None, []
    labels = [f"{c.number}. {c.label}" for c in failed]
    if any(c.block_reason is None for c in failed):
        return False, None, labels
    return False, failed[0].block_reason, labels


def context_files(issue: Issue) -> dict[str, str]:
    """§7.3 — the four files the control plane writes for the agent.

    Files rather than an MCP round-trip: strictly more reliable, and it costs no tokens
    on tool discovery. It is also why the build sandbox needs no Linear credential.
    """
    ticket = [
        f"# {issue.identifier} — {issue.title}",
        "",
        f"State: {issue.state_name}   Labels: {', '.join(issue.labels) or 'none'}",
        f"Link: {issue.url}",
        "",
        issue.description.strip(),
        "",
    ]
    spec = [
        f"# {issue.parent_identifier or 'no parent'} — {issue.parent_title or ''}",
        "",
        "The parent issue's description, verbatim. This is the approved specification;",
        "the ticket above is one slice of it.",
        "",
        issue.parent_description.strip(),
        "",
    ]
    breakdown = [
        f"# Breakdown — the siblings of {issue.identifier}",
        "",
        "The other slices of the same spec. They exist so the boundary of this ticket is",
        "visible: work that belongs to a sibling is out of scope here, and saying so in",
        "`out_of_scope` is the right answer rather than doing it.",
        "",
    ]
    breakdown += (
        [f"- `{ident}` [{state}] {title}" for ident, state, title in issue.siblings]
        if issue.siblings
        else ["_No sibling tickets._"]
    )
    comments = [f"# Comment thread on {issue.identifier}", ""]
    if issue.comments:
        for author, created, body in issue.comments:
            comments += [f"## {author} — {created}", "", body.strip(), ""]
    else:
        comments.append("_No comments._")

    return {
        "ticket.md": "\n".join(ticket),
        "spec.md": "\n".join(spec),
        "breakdown.md": "\n".join(breakdown) + "\n",
        "comments.md": "\n".join(comments) + "\n",
    }


def write_context(directory: Path, issue: Issue) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in context_files(issue).items():
        path = directory / name
        path.write_text(body, encoding="utf-8")
        written.append(path)
    return written
