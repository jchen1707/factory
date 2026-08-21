#!/usr/bin/env python3
"""P0-11 measurement harness — NOT factory code, and it writes nothing.

Lists Linear issues labelled `ready-for-agent`, evaluates §7.1's nine eligibility
conditions against each, and prints a verdict per issue. No mutation, anywhere:
no Linear write, no git write, no PR. Condition 8 shells out to `gh pr list`
(read-only) and condition 9 reads `harness.config.json` from the local clone.

The API key is read from the macOS keychain by service name and is never printed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

API = "https://api.linear.app/graphql"

QUERY = """
query($after: String) {
  issues(first: 50, after: $after, filter: { labels: { name: { eq: "ready-for-agent" } } }) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier title description
      state { name type }
      team { key }
      labels { nodes { name } }
      parent { identifier description }
    }
  }
}
"""

BLOCKING_LABELS = {"needs-info", "needs-triage", "ready-for-human", "wontfix"}
ACCEPTANCE = re.compile(r"##\s*Acceptance|Acceptance\s+criteria", re.I)


def keychain(service: str) -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "", "-s", service, "-w"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        import os

        r = subprocess.run(
            ["security", "find-generic-password", "-a", os.environ["USER"], "-s", service, "-w"],
            capture_output=True,
            text=True,
        )
    if r.returncode != 0:
        raise SystemExit(f"no keychain item for service {service!r}")
    return r.stdout.strip()


def linear(key: str, after: str | None) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"after": after}}).encode()
    req = urllib.request.Request(
        API, data=body, headers={"Content-Type": "application/json", "Authorization": key}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if "errors" in payload:
        raise SystemExit(f"linear error: {payload['errors']}")
    return payload["data"]["issues"]


def open_pr_for(identifier: str) -> bool:
    r = subprocess.run(
        ["gh", "pr", "list", "--search", identifier, "--state", "open", "--json", "number"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False
    try:
        return bool(json.loads(r.stdout or "[]"))
    except json.JSONDecodeError:
        return False


def evaluate(issue: dict, registry: dict[str, Path]) -> list[tuple[int, bool, str]]:
    labels = {n["name"] for n in issue["labels"]["nodes"]}
    state = issue["state"]["name"]
    team = (issue["team"] or {}).get("key", "")
    parent = issue.get("parent")
    desc = issue.get("description") or ""
    repo = registry.get(team)

    conds: list[tuple[int, bool, str]] = [
        (1, "ready-for-agent" in labels, "label ready-for-agent"),
        (2, state == "Todo", f"state is Todo (is {state!r})"),
        (3, team in registry, f"team {team!r} in registry"),
        (4, parent is not None, "has a parent issue"),
        (
            5,
            bool(parent) and len(parent.get("description") or "") >= 200,
            "parent description >= 200 chars",
        ),
        (6, bool(ACCEPTANCE.search(desc)), "has an acceptance-criteria section"),
        (7, not (labels & BLOCKING_LABELS), "no blocking label"),
        (8, not open_pr_for(issue["identifier"]), "no open PR references it"),
    ]
    if repo and (repo / "harness.config.json").exists():
        cfg = json.loads((repo / "harness.config.json").read_text())
        conds.append((9, cfg.get("tracker", {}).get("team") == team, "repo tracker.team matches"))
    else:
        conds.append((9, False, "repo harness.config.json unreadable"))
    return conds


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", default="factory-linear")
    ap.add_argument(
        "--repo",
        action="append",
        default=[],
        metavar="KEY=PATH",
        help="team key to clone path, e.g. BAC=/Users/james/python-harness",
    )
    a = ap.parse_args()
    registry = {kv.split("=", 1)[0]: Path(kv.split("=", 1)[1]) for kv in a.repo}

    key = keychain(a.service)
    nodes, after = [], None
    while True:
        page = linear(key, after)
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    print(f"{len(nodes)} issue(s) labelled ready-for-agent\n")
    for issue in nodes:
        conds = evaluate(issue, registry)
        ok = all(c[1] for c in conds)
        print(f"{issue['identifier']}  {'ELIGIBLE' if ok else 'NOT ELIGIBLE'}  {issue['title']}")
        for n, passed, label in conds:
            if not passed:
                print(f"    ✗ {n}. {label}")
    print("\nNothing was written.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
