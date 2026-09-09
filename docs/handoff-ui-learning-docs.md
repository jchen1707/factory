# UI, learning and documentation completion handoff

Current continuation handoff, September 9, 2026. The factory specification remains
authoritative. This carries the approved work forward; do not re-plan.

## Resume here

Read `AGENTS.md`, this handoff, the [latest acceptance](acceptance/learning-continuation-2026-09-09.md),
the [transport audit](discovery/learning-transcript-transport-2026-09-09.md), and the
[interactive probe record](discovery/interactive-learning-2026-09-09.md). Inspect Git status
and worktrees first. James authorized separate worktrees and parallel agents, selected the
**dark operations console**, and requires notes in Obsidian **Project Learnings**.

The integrated branch is `feat/ui-learning-docs` at `/Users/james/factory`. All changes
remain local; nothing was pushed, merged or deployed. Preserve the original untracked work:

- `.agents/plans/feat-runtime-certification-and-delegation/`
- `docs/runtime-frontend-acceptance-2026-09-06.md`
- `docs/runtime-nemoclaw-acceptance-2026-09-06.md`

## Completed — do not repeat without a relevant change

- All 21 fixture UI drafts and the selected production dark console across seven routes.
  Original browser acceptance: 210 draft page checks, 43 axe audits; production 70 page
  checks, 22 screenshots and five passing scenario tests. Deployment remains separate.
- Shared capture repairs, stable worktree/clone identity, atomic notes/indexes, duplicate
  protection, nonblocking outcomes and explicit missing configuration/transcript handling.
- Factory host lifecycle capture, retries on recollection and honest `retained-events-partial`
  receipts. There is no independent durable capture queue.
- README/operator guidance, document classification/archive and nine rendered workflows.
- **Real destination proven:** a genuine code-audit session wrote
  `/Users/james/Documents/Obsidian Vault/Project Learnings/2026-09-09 factory 01a08454.md`.
  Both indexes reference it; existing lessons were unchanged. A later actual Codex session
  in another factory worktree recalled only this relevant note and cited it. No historical
  backlog was processed. Invocation-local hooks prove the path, not permanent installation.
- **Body-only miss repaired:** harness `1231a1f` adds bounded indexed-body fallback after
  zero summary/path hits. Actual later-session proof and 179 shared tests passed.
  Unindexed notes and additional body evidence alongside hits still require wider skill search.
- Latest source and consumer gates and independent review passed; see latest acceptance.
  Factory fake adapters and host headless measurements do not establish sandbox behavior.

## Required next: safe runtime and full transcript retention

### Sandbox prerequisite

The September 9 fresh `shell`-agent probe rules out the base image, shell startup and
sanitized host environment: sandbox creation adds the declared credential variable to
container configuration. No model executed and the owned sandbox was removed.
[Sanitized source evidence](discovery/artifacts/learning-repair/sandbox-source-results.json)
is authoritative; do not repeat the same creation with no new hypothesis.

Earlier [live sandbox acceptance](runtime-live-sandbox-acceptance.md) located the same
injection stage and records James's project-specific acknowledgement after an HTTP 401
measurement. That exception does **not** transfer to new probes. The current source probe
did not test authentication. Do not weaken policy or treat an old invalid token observation
as a current absence of capability. A clean provisioning path, or a separately measured
and explicitly approved project exception, is needed before sandbox model execution.
Do not rotate credentials, remove global bindings, or touch any `codex-*` sandbox.

### Transport implementation and actual acceptance

Factory currently retains events, not complete runtime rollouts. The transport audit names
concrete seams; do not relabel partial evidence as full. The installed host schema exposes
nullable/unstable `Thread.path`; actual sandbox versions and paths need measurement.

With an approved safe disposable runtime:

1. Measure native transcript location and accessibility for both legacy and app-server
   paths, clean completion and interruption, bind-mounted worktrees and clones.
2. Implement session-specific export through the sandbox adapter at evidence collection,
   before archive/removal. Cover interrupted collection without an `exit` file, cancellation,
   recollection/resume and relevant child paths. Shared distillation/indexing stay in layer A;
   factory owns lifecycle/export. Do not execute candidate worktree scripts on the host.
3. Remove the disposable sandbox and prove recovery of known user/tool/assistant content
   using only host-retained data. Prove capture, both indexes, replay deduplication and later
   relevant recall. Measure native hook process environment independently of tool environment.
4. Run owner gates and fresh certification if the certified worker changes. Keep abrupt
   removal before export and terminal attempts never recollected explicit as recoverable gaps.

No new queue, vector store, schema migration or historical bulk recovery is assumed.

## Required next: interactive shutdown

Automated PTY probes did **not** establish interactive completion. They stopped at trust
screens; two early exit keystrokes instead accepted trust and Codex persisted these entries:

```text
/private/var/folders/1f/039kvb3j5vb1n0nr66j5xw6c0000gn/T/factory-interactive-learning-vexa753n
/private/var/folders/1f/039kvb3j5vb1n0nr66j5xw6c0000gn/T/factory-interactive-learning-t7uq5676
```

This violated the trust-store boundary. James was informed. No automated cleanup was done;
removing these entries, if desired, belongs to James. All probes stopped. Do not repeat
unattended trust-screen interaction. The archived script is evidence, not an approved recipe.

A properly authorized interactive session still needs clean exit, interruption, callback and
transcript timing, capture/index outcomes and recall. Claude's earlier OAuth expiry remains
historical; trust prevented a current authentication check. James must handle authentication
if still expired. Never request secrets or write `~/.codex/config.toml`.

## Configuration and publication boundaries

Process `OBSIDIAN_VAULT_DIRECTORY` is authoritative when present, including empty/invalid
values; otherwise `OBSIDIAN_VAULT_DIR` is supported. The actual host alias matches the registry.
Codex shell-tool policy `set` did not reach native hooks in measured exec/app-server paths.
Factory host workers supply the registry vault in process environment. Do not repair this by
editing user configuration. Distillation defaults to originating runtime; explicit override
remains available. Partial evidence cannot replace an existing same-session note.

Source worktree `/Users/james/harness-learning-repair`, branch `fix/learning-capture-recall`,
head `1231a1f`, is based on harness v2. Never edit generated main or vendor files by hand.
Consumer worktrees under `/Users/james/` remain on `fix/learning-recall-sync`:

| Worktree | Head |
| --- | --- |
| python-harness-learning-repair | e95622c |
| frontend-harness-learning-repair | 847b558 |
| go-harness-learning-repair | f587d62 |

Factory evidence worktrees: `factory-transcript-transport` (`5cfa2ed`) and
`factory-interactive-evidence` (`9e273fe`); both were cherry-picked into the integrated
branch. Earlier feature worktrees remain intact. Avoid duplicate overlapping factory PRs.
Source lands on harness v2 first, James merges, then sync consumers to the landed SHA and
follow stack/submodule publication. Current feature pins passed integrity, not remote
freshness. Do not edit stack submodules in the user's original harness checkout.

James owns deployment and live restart. Dark selection already authorizes implementation.
After separately approved deployment, smoke-test all seven routes read-only.

## Verification after changes

Run the declared `gate_report.mjs --force --json` in each changed repository. Factory requires
Ruff lint, Ruff format check, mypy and pytest. After console changes, use the opt-in browser
suite in [its README](ui-alternatives/production-validation/README.md). Check local doc links
and render changed Mermaid diagrams. Preserve old evidence and add dated observations.

Do not mark runtime acceptance complete until the missing matrix is evidenced or its
human/external blockers are explicitly accepted. Real Obsidian capture/recall is now proven;
interactive shutdown and full sandbox transcript retention are not.
