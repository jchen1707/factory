# Issue tracker: Linear

Issues, specs and tickets live in **Linear**, reached over MCP. **Pull requests stay on
GitHub** — Linear holds the work item, GitHub holds the diff.

This is shared harness doctrine. Each repo keeps its own `docs/agents/issue-tracker.md`
naming the team it files into and its repo-specific review notes; everything below is true in
every harness repo.

## Connecting

Linear runs through **Docker MCP Toolkit**. Every harness configuration here starts
`docker mcp gateway run`, and Docker Desktop owns the authentication and the enabled-server
state. There is no per-repo API key to manage.

- Authenticate Linear in Docker Desktop.
- Enable Linear for the active Toolkit profile.
- Select the **Development** workspace.
- Check `/mcp` for the Toolkit gateway and its Linear tools.
- MCP servers load at **session start**. Changing the config or the credential does not take
  effect until you restart.
- If tools are missing, confirm that `docker mcp gateway run` works in the harness host.

<!-- harness:agnostic -->

- Trust the project before Codex can load `.codex/config.toml`.
- Where the sandbox runtime supplies Linear instead of the Toolkit, register the remote server
  once on the host and start sandboxes with `--static-mcp linear`:

  ```sh
  sbx mcp add linear --url https://mcp.linear.app/mcp
  ```

  Sandbox injection changes require a new session. Never add the credential to a repository.

<!-- /harness:agnostic -->

### The connection is shared, and the workspace selection is global

Docker MCP Toolkit shares its Linear connection with every attached client. Another
repository's session can change the selected workspace, and the change is invisible from
here. **Confirm Development before a write.**

Disable other Linear MCP connections. Multiple tool surfaces can target different workspaces,
and their names do not prove the destination.

### Rotating or repointing

Manage authentication and workspace selection in Docker Desktop MCP Toolkit. Restart the agent
harness after you reconnect or change the enabled server.

## Discovering the tools

**List the tools before first use rather than assuming names.** The surface changes between
releases, and the tool prefix depends on the harness and the configured server name.

`/mcp` shows the connected servers and their tools. Match the operation you need from the
table below to what is actually offered.

## Conventions

| Operation               | What to call                                                                                              |
| ----------------------- | --------------------------------------------------------------------------------------------------------- |
| **Create an issue**     | the create-issue tool — needs `team`; set `title`, `description` (markdown), optional `labels`, `project` |
| **Read an issue**       | the get-issue tool, with the identifier (`TEAM-123`)                                                      |
| **List issues**         | the list-issues tool — filter by `team`, `state`, `assignee`, `label`                                     |
| **Comment**             | the create-comment tool, with the issue id and markdown `body`                                            |
| **Apply/remove labels** | the update-issue tool, setting the `labels` array                                                         |
| **Change state**        | the update-issue tool, with the target workflow state                                                     |
| **Close**               | move to the team's Done/Cancelled state — Linear has no separate close verb                               |

Issue identifiers are `TEAM-NUMBER` (e.g. `BAC-4521`, `FRO-412`), not bare integers. A
reference like `#42` in conversation is **not** a Linear id — ask which team it belongs to
rather than guessing. In a repo's own conversation `#42` usually means a GitHub PR.

Note the team key is **not** returned by the MCP `get_team` tool — it exposes id, name, icon
and timestamps only. Read it from Linear's GraphQL API
(`{ teams { nodes { key name } } }`) or from any issue id in the UI.

## Status sync — the issue state must track the work

Nothing moves an issue automatically, and a session can end before a cleanup step runs. So
each transition happens **in the same turn as the git action that causes it**, not at the end
of the session. A whole implementation has shipped with an open PR while the issue sat in Todo
— that is the failure this table prevents.

| Moment                       | Action                                                                                                                |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Branch created for an issue  | Move the issue to **In Progress**                                                                                     |
| PR opened                    | Move to **In Review**; attach the PR URL to the issue (create-attachment tool); comment with the PR link and evidence |
| PR merged                    | Move to **Done**                                                                                                      |
| Work abandoned or superseded | Move to **Cancelled**, with a comment naming the successor                                                            |

A parent issue follows its children: the first child in progress moves the parent to **In
Progress**; the last child done moves it to **Done**.

### Make it mechanical: Linear's GitHub integration

The table above is doctrine, and doctrine can be skipped. Linear's own GitHub integration does
most of it deterministically — install it once (Linear → Settings → Integrations → GitHub, a
human action; an agent cannot do it) and:

- A branch named `<type>/<TEAM>-<num>-<slug>` links the PR to the issue automatically.
- "Fixes TEAM-123" in the PR description moves the issue to **In Review** when the PR opens
  and **Done** when it merges, and attaches the PR.

Once installed, the agent's remaining manual duties shrink to: the **In Progress** move at
branch creation, the evidence comment, and the parent rollup. Until it is installed, the whole
table is manual — do not assume the automation exists; check the issue actually moved.

## Labels vs workflow states

Linear separates **workflow state** (Backlog / Todo / In Progress / Done — a single value that
drives the board) from **labels** (many per issue). The five canonical triage roles in
`triage-labels.md` are **labels**, not states. Applying `ready-for-agent` does not move the
issue across the board; set the state explicitly when the role implies one.

## When a skill says "publish to the issue tracker"

Create a Linear issue in the repo's default team — each repo's own `issue-tracker.md` names
it. Put the spec in the issue `description` as markdown. If the skill produced a document
longer than fits comfortably, put the summary and acceptance criteria in the description and
link the full document.

## When a skill says "fetch the relevant ticket"

Get the issue by identifier, then read its comments for the discussion.

## Wayfinding operations

Used by `/wayfinder`. The **map** is one issue; **tickets** are its children.

- **Map** — an issue labelled `wayfinder:map` holding the Notes / Decisions-so-far / Fog body.
- **Child ticket** — a Linear **sub-issue** of the map (`parent` field), labelled
  `wayfinder:<type>` (`research` / `prototype` / `grilling` / `task`).
- **Blocking** — Linear has native issue relations: use the **blocks / blocked-by** relation
  rather than a text convention. A ticket is unblocked when every blocker reaches a completed
  state.
- **Frontier query** — the map's sub-issues that are not Done, have no unresolved blocked-by
  relation, and no assignee; first in map order wins.
- **Claim** — assign the issue to the current user; this is the session's first write.
- **Resolve** — comment the answer, move to Done, then append a pointer to the map's
  Decisions-so-far.

## AI-authored content carries a disclaimer

When a session writes tracker content the user did not author — an answer generated in auto
mode, a reply fabricated to unblock an interview skill — end that content with:

> _This was generated by AI during \<phase\>._

The disclaimer travels with the AI-authored content, whichever skill produced it. The `triage`
skill adds it on its own; the other skills rely on this rule.

Verify provenance before a reply drives a state or label change. A reply that exists is not
proof the reporter wrote it — an unattended session can generate the human side of an
interview and post it. Treat a comment with the disclaimer above, or with an uncertain author,
as machine output, not as reporter input.

## Correcting a published spec

An error in a published spec misleads every agent that reads it later, so fix it where it
stands — a skill's "do not modify the parent issue" protects scope, not known-wrong facts.
When a downstream phase finds a spec error:

1. Patch only the wrong paragraph in the issue description.
2. Mark the patch inline: _Corrected during \<phase\>, \<date\>: \<what changed\>._
3. Comment on the issue with the correction and the reason.
4. Check every ticket already derived from the spec for the copied claim; patch those the same
   way.
5. Disclose the correction in the session report.

## How reviews resolve a ticket

The **Spec** axis of a review resolves the originating ticket from the Linear id in the branch
name or commit trailer. Name branches `<type>/<TEAM>-<num>-<slug>` so the link is mechanical;
each repo's own `issue-tracker.md` gives its prefix and its fallback when there is no ticket.
A branch named with the wrong prefix silently loses its spec axis.
