# Learning acceptance continuation — September 9, 2026

This continues the [integrated acceptance](ui-learning-docs-2026-09-08.md).
Dark console implementation and browser acceptance were already complete; nothing was
deployed. Work used independent source, consumer and factory evidence worktrees.

## Newly established

**Real Obsidian destination and later recall passed.** An actual read-only Codex audit
of the factory's transcript retention code completed, fired native SessionEnd, and
its detached shared distiller wrote
`Project Learnings/2026-09-09 factory 01a08454.md` in the configured vault
`/Users/james/Documents/Obsidian Vault`. Both indexes reference the note. Hash comparison
found no existing lesson modified or deleted; only the existing project index changed.
This was a genuine code audit, not a synthetic lesson or historical recovery.

A later actual Codex session in the `factory-dark-console` worktree received one project
summary at SessionStart and only that relevant note at UserPromptSubmit. With tools
prohibited by the prompt, it correctly explained partial event evidence, the proposed
retention seam and the outstanding sandbox measurement, citing the exact note path.
The [method](../discovery/artifacts/learning-repair/real-vault-method.md) and
[sanitized results](../discovery/artifacts/learning-repair/real-vault-results.json)
record session identity, note hash, index references, callback provenance and response.
This proves native headless host capture and later real-vault recall; invocation-local
hook registration does not establish permanent installation or interactive shutdown.

**Automatic body-only recall passed after repair.** Harness `1231a1f` reproduces and fixes
a query that matched only a note body. After zero summary/path hits, the hook searches
at most 32 indexed regular files, up to 64 KiB each, and returns at most four excerpts
of 3,000 characters. Incomplete searches report partial evidence. A real later-worktree
Codex session consumed the lesson and unseen marker from this fallback. That separate
measurement used a temporary fixture vault. Source evidence is under
`harness/docs/evidence/learning-body-recall/` on the source feature branch.
Unindexed notes and body evidence alongside summary hits still require the wider skill
search; automatic recall is not an exhaustive semantic search.

The shared skill also now accepts the documented process environment alias and no longer
claims an absent hook log proves the hook never fired. Consumer guidance was aligned.
An independent reviewer found no material issue and separately ran six recall tests.

## Sandbox source narrowed; transport still unverified

A fresh owned `factory-build-learning-source-b3c8e4a4` sandbox used the stock Codex
Docker template with the `shell` agent and sanitized host environment. Read-only Docker
inspection found no declared secret variable in the image environment but found one in
the created container. Direct Python exec also saw it. This rules out shell startup,
Codex-specific startup and host environment inheritance in that probe; sandbox creation
adds it. No credential value was emitted and no model ran. Initial removal required
noninteractive confirmation; explicit owned-sandbox stop and forced removal succeeded.

[Source results](../discovery/artifacts/learning-repair/sandbox-source-results.json) and
[reproducer](../discovery/artifacts/learning-repair/factory-learning-source-probe.py.txt)
preserve the measurement. Earlier [live sandbox evidence](../runtime-live-sandbox-acceptance.md)
had independently located creation-time injection and recorded James's project-specific
acknowledgement after HTTP 401. That acknowledgement does not authorize new learning
probe sandboxes, and the current probe did not retest authentication.

The [transport audit](../discovery/learning-transcript-transport-2026-09-09.md) confirms
that neither factory runtime exports the native rollout before teardown. Host Codex
exposes nullable, unstable thread path metadata, but this does not prove sandbox export.
No certified worker was changed on that assumption. Clean/interrupted sandbox execution,
clone identity, native hook environment, complete post-removal retention and lifecycle
recovery acceptance remain open. Existing event receipts are explicitly partial.

## Interactive attempt and unintended side effect

Interactive Codex and Claude PTY probes stopped at directory trust screens and did not
establish clean shutdown or authenticated Claude execution. Two early automated exit
keystrokes instead accepted trust, causing native Codex to persist temporary fixture
entries in `~/.codex/config.toml`. This violated the repository boundary. James was
informed; no automated configuration cleanup was attempted. Exact paths and invalid
results are in the [interactive evidence](../discovery/interactive-learning-2026-09-09.md).
All owned probe processes stopped. No further trust-screen interaction is authorized.
The earlier expired-Claude-OAuth observation remains historical, not a current auth check.

## Validation and publication

Harness: 179 hook tests, formatting and declared source checks passed. Python, frontend
and Go consumer gate reports passed, and vendor integrity matched harness `1231a1f`.
Consumer heads: Python `e95622c`, frontend `847b558`, Go `f587d62`.
The [validation report](learning-continuation-gates.json) preserves gate output and caveats.
Factory's four declared gates passed with exit 0: Ruff lint (90 ms), Ruff format
(41 ms), mypy (241 ms), pytest (182,702 ms). Output tails were empty, so no test count
is inferred. No new Python scope was introduced; mypy's path-scope caveat does not
exclude changed Python here. Sandbox behavior is not proven by fake adapters.
The changed learning Mermaid diagram rendered and local documentation links resolved.
No console behavior changed, so the prior browser acceptance remains applicable.

All source and consumer branches remain local and unmerged. Factory vendor integrity
passed at `1231a1f14`; the explicit upstream comparison failed because fetched harness v2
is still `61a509f97`. This is the expected unpublished-feature mismatch, not freshness. James still owns merging and deployment. The
[current handoff](../handoff-ui-learning-docs.md) lists the remaining work and boundaries.
