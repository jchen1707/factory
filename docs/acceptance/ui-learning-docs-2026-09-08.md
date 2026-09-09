# Integrated UI, learning and documentation verification

Measured September 8 EDT / September 9 UTC, 2026. James selected the dark operations
console during implementation. Work was split across independent feature worktrees,
reviewed by another agent, and integrated on `feat/ui-learning-docs`. No deployment,
schema migration, tracker write or remote merge was performed.

## Results

The integrated factory ran its declared gate runner:

```sh
node .agents/vendor/harness/hooks/gate_report.mjs --force --json
```

| Declared gate | Status | Exit | Duration |
| --- | --- | --- | --- |
| Ruff lint | pass | 0 | 96 ms |
| Ruff format check | pass | 0 | 46 ms |
| mypy | pass | 0 | 594 ms |
| pytest | pass | 0 | 211,813 ms |

The [unaltered gate report](ui-learning-docs-gates.json) records `verdict: pass`.
Its output tails are empty; no additional test-count claim is inferred from them.
All four declared gates ran. The mypy scope caveat does not exclude the new production
Python modules or tests: they are under its configured `src` and `tests` paths.
The fake-sandbox caveat still applies to pytest; it does not establish live runtime behavior.

Separately, the integrated tree ran the opt-in production browser suite with temporary
browser dependencies and output outside the repository:

```sh
FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules \
FACTORY_BROWSER_OUTPUT=/tmp/factory-integrated-console-browser \
uv run pytest tests/integration/test_console_browser.py -q
```

It returned exit 0 with all five scenarios passing: seven actual console routes at two
widths, 70 page checks. The same reviewed production source has
[retained screenshots and browser evidence](../ui-alternatives/production-validation/README.md).
Ten focused regressions cover attention ordering, approval versus pending launch,
failed runs, navigation, policy availability and incomplete cost labels.
The original 21 drafts remain [available as approval history](../ui-alternatives/index.html).

All nine workflow diagrams rendered successfully. The integrated documentation check
resolved 136 local Markdown link targets without a missing file. Original discovery
records, the canonical specification and configuration were not rewritten; the three
pre-existing untracked planning/acceptance artifacts remain untracked and untouched.

## Learning delivery and proof

Learning notes are written to the configured Obsidian vault's **Project Learnings**
directory. Repository receipts and probe artifacts are diagnostic evidence, not the note
destination. The host's configured vault alias was compared read-only with the factory
registry and matched; that vault and its Project Learnings directory exist.

Layer A's 159 tests and declared source checks passed. The Python, frontend and Go
consumers passed all applicable declared gates and vendor integrity checks.
The integrated factory's vendor integrity check reported:

```text
note: harness checkout not supplied -- integrity checked, freshness not
OK: vendored layer A matches harness@a3478e0bd
```

[Real runtime measurements](../discovery/learning-repair-2026-09-08.md) establish Codex
capture into a temporary Project Learnings directory, both indexes, and later actual
session recall from another worktree. They also establish the native-hook environment
scope defect and the configured alias repair. No historical vault notes were rewritten.

Limits remain explicit: Claude authentication is expired. Disposable sandbox probes
encountered a nonempty inherited credential and stopped before model execution; full
sandbox transcript export remains unverified. Capture receipts support recollection,
not a durable independent job queue. Summary-based recall can miss body-only terms and
directs the agent to deeper search. See the runtime record for exact observations.

## Feature branches

| Repository / worktree | Branch | Final feature commit |
| --- | --- | --- |
| factory-ui-alternatives | feat/ui-alternatives | b8b1e5f |
| factory-dark-console | feat/dark-operations-console | e804316 |
| factory-learning-repair | fix/learning-lifecycle | ecf22fb |
| factory-documentation | docs/workflow-reference | 3b2deb9 |
| harness-learning-repair | fix/learning-capture-recall | a3478e0 |
| python-harness-learning-repair | fix/learning-recall-sync | ef91d56 |
| frontend-harness-learning-repair | fix/learning-recall-sync | 9213657 |
| go-harness-learning-repair | fix/learning-recall-sync | 3b95d6f |

Shared changes are based on `harness@v2`. Consumer pins reference the prepared feature
SHA; upstream freshness cannot be claimed until James merges the source change. No
branch was pushed or merged remotely during this work.
