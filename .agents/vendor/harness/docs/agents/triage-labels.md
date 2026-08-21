# Triage Labels

The skills speak in terms of canonical triage roles: two **category** roles and five **state**
roles. Every triaged issue carries exactly one of each. This file maps those roles to the
actual label strings used in the issue tracker.

This is shared harness doctrine. The repo you are working in may add a
`docs/agents/triage-labels.md` of its own for facts that are true only there.

## Category roles

| Role in mattpocock/skills | Label in our tracker | Meaning                    |
| ------------------------- | -------------------- | -------------------------- |
| `bug`                     | `Bug`                | Something is broken        |
| `enhancement`             | `Feature`            | New feature or improvement |

## State roles

| Role in mattpocock/skills | Label in our tracker | Meaning                                  |
| ------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`            | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`              | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`         | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`         | `ready-for-human`    | Requires human implementation            |
| `wontfix`                 | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding
label string from these tables.

## Where the labels live

The tracker is **Linear** (see `issue-tracker.md`), workspace **Development**
(`development-jchen`). All seven labels exist there as **workspace** labels, so both the
`Backend` and `Frontend` teams see the same set — neither team has its own copy, and a label
created by one is immediately visible to the other.

`Bug` and `Feature` are ungrouped. The five state labels live under a **`Triage`** parent
group. Verified in the workspace on 2026-08-06.

**The labels are workspace-scoped, and the workspace selection is shared.** Docker MCP
Toolkit hands the same Linear connection to every attached client, so another repository's
session can change which workspace is selected. Confirm **Development** before a write.

Moving a repo to a different workspace means creating all seven labels again — nothing
travels with the connection.

## State labels are not workflow states

State labels are **labels**, not workflow states — applying one does not move the issue across
the board. Set the Linear workflow state explicitly when the role implies one.

## One category per issue

The skill requires exactly one category role per issue. The workspace also carries an
`Improvement` label that predates this mapping, and both `Feature` and `Improvement` would map
to `enhancement` — applying both puts two categories on one issue and breaks the mapping.

`Feature` is the single `enhancement` target. `Improvement`, and any other synonym the
workspace acquires, stays available for manual use and is never applied by triage.

## When a label in the table is missing

Listing the labels can turn up a role with nothing to map to — a fresh workspace, or a label
somebody removed. Create it rather than improvising: a triaged issue carries exactly one
category and one state role, so silently skipping the missing one leaves the issue outside
the mapping and every later query that filters on it misses the issue entirely.

Create it with the exact string in the table above, in the same group (`Triage` for a state
role, ungrouped for a category), and say in the session report that you created it. Do not
substitute the nearest existing label — that is how a second `enhancement` synonym gets into
the workspace, which is the problem the section above exists to prevent.

## Editing labels needs the Linear UI

The Linear MCP server exposes `create_issue_label` but **no update or delete** for labels.
Renaming a label, fixing a description, or removing one has to happen in the Linear UI
(Settings → Labels) — an agent session cannot do it. A typo is a trip to the UI, so list what
is actually present before trusting this table. Only the mapping tables above are editable
from here.
