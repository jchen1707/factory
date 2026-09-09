# UI, learning and documentation completion handoff

Current continuation handoff, September 8, 2026. Retire this document after the remaining
acceptance evidence is recorded and publication status is explicit. This carries forward
the approved plan; it does not replace the factory specification.

## Resume here

Read [AGENTS.md](../AGENTS.md), this handoff, the
[integrated acceptance record](acceptance/ui-learning-docs-2026-09-08.md), and the
[learning runtime measurements](discovery/learning-repair-2026-09-08.md). Inspect Git status
and worktrees before changing anything. Implementation and local verification are done;
the original plan's runtime acceptance is **not yet complete**.

James requested separate feature worktrees and parallel agents. He selected the **dark
operations console**, so no further alternative selection is needed. He clarified that
learning notes must land in the Obsidian Vault under **Project Learnings**. The configured
host destination is `/Users/james/Documents/Obsidian Vault/Project Learnings`. Repository
receipts are evidence, not a substitute destination.

The integrated factory branch is `feat/ui-learning-docs` in `/Users/james/factory`.
Before this handoff its HEAD was `f971947`. All implementation is committed locally;
no branches were pushed, no PRs were opened, and nothing was merged or deployed.
Previous agents completed their assignments; use the files rather than their memory.

Preserve these original untracked artifacts exactly:

- `.agents/plans/feat-runtime-certification-and-delegation/`
- `docs/runtime-frontend-acceptance-2026-09-06.md`
- `docs/runtime-nemoclaw-acceptance-2026-09-06.md`

## Completed work and evidence

| Area | Completed | Acceptance still missing |
| --- | --- | --- |
| UI alternatives | All 21 fixture pages, five states, navigation/settings interactions, desktop/narrow layouts; 210 page checks and 43 axe audits | None requiring another design decision |
| Selected production UI | Dark console across seven routes, accurate approval/pending distinctions, incomplete cost and unavailable data, keyboard/contrast/overflow checks; 70 browser checks and 22 screenshots | Deployment is a separate human-owned action |
| Shared learning capture | Missing transcript/config outcomes, nonblocking wrappers, runtime-selected distiller, stable project identity, atomic notes/indexes, duplicate/concurrent processing protection | Actual interactive shutdown and safe sandbox runtime measurements |
| Shared recall | Bounded startup project index, topical retrieval, cross-project selection, provenance and explicit unavailable/no-match outcomes | Body-only query fallback behavior in an actual later task |
| Factory lifecycle | Host capture worker and receipts, recollection/retry, launch/child/recovery/cancellation integration, honest partial-event labeling | Complete sandbox transcript preservation/export and real lifecycle proof |
| Documentation | Current README/operator guide, nine rendered workflow diagrams, classification and two superseded handoffs archived with preserved bodies | Update evidence and guidance as remaining measurements change behavior |

The [integrated gate report](acceptance/ui-learning-docs-gates.json) records all four
factory gates passing: Ruff lint, Ruff format, mypy and pytest. Output tails are empty;
do not infer a pytest test count. The opt-in browser suite separately passed all five
scenarios. See [production browser evidence](ui-alternatives/production-validation/README.md)
and [lifecycle acceptance](acceptance/learning-lifecycle-2026-09-08.md).

Layer A passed 159 tests, formatting and source checks. Python, frontend and Go consumers
passed applicable gates and vendor integrity. Nine Mermaid diagrams rendered and the
integrated documentation links were checked. Fake adapters do not establish native hooks
or sandbox behavior. No need to repeat the full baseline for handoff-only documentation.

## Required next: close the runtime evidence gaps

### 1. Diagnose the disposable sandbox credential discrepancy

This is the first dependency for real sandbox model execution. Three owned disposable
`factory-build-learning-probe-*` sandboxes were created and removed. No model ran.
Fresh sandboxes using both `codex-pnpm:v1` and the default Codex Docker template still
contained a nonempty, non-placeholder GitHub credential environment variable, even with
a sanitized host creation environment. `sbx inspect` exposed only the declared gateway;
`policy.capability_secrets()` returned no capability secrets. Boolean-only in-VM checks
found the discrepancy; no credential values were read or printed.

Sandbox-scoped removal reported no GitHub binding and did not resolve it. Determine the
provisioning source (image, daemon or another mechanism) with read-only inspection and
safe disposable probes. Do not repeat the same failed probes without a new hypothesis.
The [archived probe artifacts](discovery/artifacts/learning-repair/README.md) preserve
sanitized scripts/results; temporary `/tmp` scripts are not durable dependencies.

Do not weaken policy, admit credentials to a model sandbox, touch `codex-*` sandboxes,
rotate credentials, or remove global bindings. This new fresh-name observation does not
invalidate the previously settled workaround for `factory-build-python-harness`.
If remediation requires James's reserved credential action, report the measured action
needed and continue independent work.

### 2. Measure both actual factory runtime paths safely

After obtaining a clean disposable sandbox, test supported legacy and app-server paths
with isolated transcripts, fixtures and a temporary vault:

- Clean completion and interruption, including actual recollection/resume behavior.
- Worktree and clone project identity.
- Native hook process environment and transcript availability at completion.
- Transcript retention/export after sandbox exit and removal.
- A known lesson producing one note and both index entries, replay without duplication,
  and later relevant recall from another worktree without unrelated initial context.
- Relevant child/cancellation recovery paths and observable failed or interrupted capture.

Current factory fallback invokes the **registered host repository's** vendored layer A,
not candidate worktree code. It retains complete JSON event lines but labels evidence
`retained-events-partial`: app-server events especially omit full user/tool conversation.
Do not promote this to full-transcript proof. If transport needs repair, factory owns
lifecycle/export coordination; shared distillation, indexing and recall stay in harness.
Changing a certified worker requires fresh certification evidence.

Capture receipts permit retries when an attempt is recollected. There is no independent
durable capture queue: a terminal attempt never recollected can remain a recoverable gap.
Measure and report that boundary; do not invent a new queue requirement or automatically
bulk-recover the historical backlog.

### 3. Complete interactive and real-destination evidence

Host Codex CLI 0.153.4 headless exec and the actual factory app-server worker fired native
Start/End hooks on clean completion. Killing their process groups fired Start but not End;
transcripts survived host process exit. Actual Codex distillation into a temporary vault,
duplicate replay, and actual later-session recall in another Git worktree passed.
These measurements do **not** prove human interactive session shutdown or sandbox export.

Measure real interactive Codex and Claude session completion/interruption independently.
Claude 2.1.259 hook registration and transcript timing were measured, but its model call
failed because OAuth expired and could not refresh. James must renew authentication;
do not request or log secrets. Alternate distillation backends do not prove Claude's
native authenticated runtime works.

The actual configured vault was inspected read-only: 16 notes, both indexes, no `_hook.log`
at the initial inspection. No historical notes were changed. All successful write probes
used temporary vaults. After appropriate activation, capture a genuine new session lesson
to the configured **Project Learnings** directory and verify its note, indexes, provenance
and later recall. Do not pollute the real vault with synthetic probe notes or claim the
read-only path comparison proves a live write.

### 4. Verify the remaining recall edge case

Initial recall deliberately searches bounded summary/path information. An actual
body-only query (`zebra-reconcile-83 rollback`) missed a lesson while broader
`queue delivery recovery` matched. Verify the documented deeper `search-second-brain`
fallback occurs before planning/debugging when needed. If it does not, repair the minimal
shared retrieval behavior and test a later session. Do not load the entire vault or
introduce a vector store merely to close this case.

## Load-bearing implementation facts

- Process `OBSIDIAN_VAULT_DIRECTORY`, when present, is authoritative even if empty or
  invalid. Otherwise layer A accepts process `OBSIDIAN_VAULT_DIR`. The host alias matched
  the factory registry in a read-only comparison.
- Codex `-c shell_environment_policy.set.OBSIDIAN_VAULT_DIRECTORY=...` did **not** bind
  native hook environment in either measured host runtime. Alias-only process binding
  passed actual capture and later recall. Do not write `~/.codex/config.toml` to fix this.
- The factory worker injects registry vault configuration into its host process environment.
  Native sandbox vault binding still needs separate proof.
- Distillation defaults to the originating runtime; `LEARNINGS_DISTILLER` overrides it.
  Direct capture without runtime information defaults to Claude. Codex distillation is
  read-only/ephemeral, disables hooks/tools as implemented, and guards recursion.
- Shared note updates use atomic writes and a same-session lock across read/model/rename.
  Stale-lock reclamation is serialized; foreign/unknown ownership is conservatively refused.
  Partial evidence must not replace an existing same-session note.
- Preserve note identities/content and historical evidence. Historical bulk recovery remains
  outside this initial repair.

## Worktrees and publication order

All paths below are under `/Users/james/`; verify current heads before use.

| Worktree | Branch | Last recorded head |
| --- | --- | --- |
| factory | feat/ui-learning-docs | f971947 before this handoff |
| factory-ui-alternatives | feat/ui-alternatives | b8b1e5f |
| factory-dark-console | feat/dark-operations-console | e804316 |
| factory-learning-repair | fix/learning-lifecycle | ecf22fb |
| factory-documentation | docs/workflow-reference | 3b2deb9 |
| harness-learning-repair | fix/learning-capture-recall | a3478e0bd704a1fc5772a97f5f59bd6491c8a0cc |
| python-harness-learning-repair | fix/learning-recall-sync | ef91d56 |
| frontend-harness-learning-repair | fix/learning-recall-sync | 9213657 |
| go-harness-learning-repair | fix/learning-recall-sync | 3b95d6f |

Shared work was rebased onto fetched `origin/v2` at `61a509f`. The original local v2 was
stale; do not resume from it or the unrelated original harness checkout branch. Source
changes belong on harness v2. Never hand-edit generated main or `.agents/vendor/`.
Use vendor sync from source and standalone consumer worktrees; avoid editing the user's
stack submodules inside the harness checkout.

Consumer pins currently reference the unmerged feature SHA. Integrity passed; upstream
freshness is **not** established. Prepare reviewable source changes first, then update
consumer pins to the landed source and follow the applicable stack/submodule workflow.
James merges. The integrated factory branch already contains feature cherry-picks; avoid
opening duplicate overlapping factory PRs from every worktree.

Publication and deployment are separate from missing runtime acceptance. Dark theme
selection authorizes the chosen implementation, not a live service restart or deployment.
After James approves deployment, smoke-test the seven actual routes read-only. No schema
migration is assumed or authorized. Do not change live settings merely to inspect the UI.

## Verification after further changes

Run each changed repository's declared gates. Factory requires:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

For console changes, also run the opt-in browser suite using the setup in its
[validation README](ui-alternatives/production-validation/README.md). Previous invocation:

```sh
FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules \
FACTORY_BROWSER_OUTPUT=/tmp/factory-integrated-console-browser \
uv run pytest tests/integration/test_console_browser.py -q
```

Temporary browser dependencies may need recreation; they are not production dependencies.
For shared changes, run source checks, meaningful capture/recall regressions and consumer
vendor checks. Repeat actual runtime probes when the affected behavior changes. Check
local documentation links and render changed Mermaid diagrams. Add dated evidence with
explicit passed/failed/unavailable statuses; preserve prior measurements.

Close this handoff only when the remaining runtime capture/recall matrix is evidenced or
its human/external blockers are explicitly accepted, the real Obsidian destination has
been proven, and publication/activation status is unambiguous. Do not call fake tests,
headless-only hooks, temporary-vault writes or partial event receipts proof of the missing
behaviors.
