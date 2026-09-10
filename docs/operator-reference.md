# Operator reference

Layer D — the control plane for approved software work.

Factory takes a human-approved Linear ticket through isolated implementation,
verification and independent review, then opens a pull request for James to merge.
It owns scheduling, recovery and external effects; the target repository owns its
requirements, gate commands and review instructions.

The implementation includes delivery/model profiles, fresh diagnosis, normalized
telemetry and accounting, isolated concurrency, automatic runtime certification and
controlled child agents. These features are configurable; merging code does not
activate them for existing projects or runs. See the [acceptance report](runtime-certification-completion-acceptance.md)
for measured synthetic coverage and the [rollout procedure](runtime-certification-rollout.md)
for deployment, migration and rollback. Production activation is a separate operator action.

## Where this sits

| Layer | Repository | Responsibility |
| --- | --- | --- |
| A | [harness](https://github.com/jchen1707/harness) | Shared policy, hooks, workflow contracts and review instructions |
| B | python-harness, frontend-harness | Stack presets, applicable guidance and tooling |
| C | Product repository | Product requirements, dependencies and declared gates |
| **D** | **factory** | **What runs, when, and what happens when it fails or dies** |

Factory reads gate commands from the target's `harness.config.json` and shared
contracts from `.agents/vendor/harness/`. It does not maintain a second review
prompt or choose an application's framework. Shared changes originate in `harness@v2`
and reach consumers through the pinned sync process. Stack repositories provide minimal
and existing framework presets with optional components; existing products keep their
architecture until explicitly migrated.

[AGENTS.md](../AGENTS.md) contains contributor boundaries. The original
[software factory specification](../SOFTWARE-FACTORY-PLAN.md), measured
[discovery evidence](discovery/), [runtime rollout history](runtime-rollout.md)
and [operator runbook](runbook.md) provide the detailed contracts and history.

## Start here

```sh
uv sync
uv run factory doctor
uv run factory status --all
uv run factory run BAC-6 --check          # evaluate eligibility without claiming work
uv run factory serve                    # loopback operator console
```

A registered project, working sandbox runtime, approved target authority and configured
host credentials are prerequisites. `doctor --deep` includes a paid model canary;
ordinary inspection is not a substitute for compatibility certification.

## Workflow and delivery policy

The normal path is approved specification → approved vertical ticket → readiness →
implementation → deterministic verification → independent review → PR awaiting James.
Readiness checks acceptance criteria, testing seams, dependencies and current authority.
Missing product decisions return to a human; technical gaps can receive a short execution
brief. `/plan` remains optional. Unattended diagnosis uses a noninteractive contract.

Prototype, Core and Hardening profiles belong to the target's `harness.config.json`.
Deferrals retain the requirement, rationale and revisit condition. Every profile preserves
credentials, human-owned decisions, review isolation and effects accounting. A run freezes
its policy and source authority; candidate changes cannot weaken their own review.
Replacing an existing run's policy is an explicit action that invalidates affected evidence.

Model presets are independent of delivery profiles. Volume and high-confidence presets
route execution briefs/test design, implementation, review, diagnosis and documentation
separately. A configurable test-design role defines scenarios; builders retain vertical
red/green implementation. Verification commands do not need a model. Each invocation
records the actual model, effort and preset, checked against its executing runtime.
Existing routing remains available and is not silently replaced.

Diagnosis distinguishes reproducible code defects, environment failures, stale authority,
ambiguous requirements and disputed findings. Automatic code repair needs a reproducible
failure and allows two attempts per failure episode within lifetime/spend limits. Repeated
failure without new evidence pauses. Scope changes and review disputes remain human decisions.

Automatic/Approval controls apply to projects and runs. Approval holds the next agent
attempt; deterministic observation, verification and accounting can continue. Suspend
separately stops current work while preserving recoverable state.

```sh
uv run factory configure --project PROJECT --delivery-profile prototype --model-preset volume
uv run factory configure --project PROJECT --workflow diagnosis --mode approval
uv run factory configure --ticket BAC-6 --approve-attempt INVOCATION_KEY
uv run factory configure --ticket BAC-6 --replace-policy core
```

These commands change settings. Use the invocation key shown by the console; policy
replacement requires a paused run. Project changes do not rewrite existing run snapshots.

## Runtimes, certification and child agents

Legacy `codex exec` and Codex app-server are separate adapters. Existing runs retain their
selected adapter. Automatic certification is opt-in; absent configuration uses manual
compatibility, and child delegation defaults to disabled.

Astra availability is determined inside the executing sandbox, not inferred from the host
Codex session. Disposable testing found Codex 0.146.0 omitted Astra; 0.153.4 advertised the
exact `gpt-6-astra` model and completed high/xhigh schema-valid turns with unchanged provider
context. This does not authorize an arbitrary runtime/account combination or a model alias.

Automatic certification binds the actual sandbox generation, image, complete runtime/helper
package, mounts, environment, trusted authority/hooks and probe implementation. Six compatibility
checks establish hook enforcement, schema output, isolation, durability, recovery and usage
semantics before application launch. Fresh validation also runs immediately before builder,
reviewer, recovery and child launches. Changed identities or tampered evidence refuse launch.
Controller restart reconciles durable paid intent rather than duplicating probes.

The supported native package includes Codex, `codex-code-mode-host` and its packaged `bwrap`
resource. Copying a standalone executable is insufficient. Zero-turn preparation settles native
initialization in the VM's private Codex home before its first fingerprint, preserves full
configuration identity and refuses explicit distrust. It never writes the host trust store.
See [certification service](runtime-certification-service.md) for the exact host configuration
and [rollout prerequisites](runtime-certification-rollout.md) before selecting it.

Factory-owned child tools pass requests through protected mailboxes to the host broker.
Every child has durable ownership, an invocation, approval/admission, accounting and validated
results. Read-only children receive read-only source mounts; clone children use retained
snapshots, while a bind-mounted source may still change through its parent writer.
Isolated writable children receive private
source and dependency environments; scoped artifacts are checked and integrated with preserved
originals, conflicts and cancellation fences. Children cannot directly write the parent's tree,
expand their authority, or silently dismiss review findings. Independent review still follows
integration; a child result does not constitute delivery approval.

```sh
uv run factory configure --project PROJECT --delegation-mode read-only \
  --max-active-agents 8 --max-children-per-parent 2 --max-delegation-depth 1
uv run factory configure --ticket BAC-6 --delegation-mode inherit --max-active-agents inherit
```

These are explicit configuration examples, not activation recommendations. Only depth one is
supported. Run settings may restrict project permissions/capacity, never expand them. Writable
mode is a separate opt-in after its prerequisites pass.

## Concurrency and source isolation

Project run slots and agent slots are separate limits. Atomic admission coordinates CLI and
daemon processes; agent accounting includes parents, children, reviewers and certification.
The console shows inherited/explicit limits, active work, queues and waiting reasons. Lowering
limits drains admitted work without killing it. Extremely restrictive changes can require an
operator to drain/suspend parents or restore capacity before queued children progress.

Per-run isolation separates worktrees, writable dependencies, temporary files, databases and
ports. Clone layouts keep Linux dependencies in the VM. Shared Git maintenance/integration is
serialized, and diverged branches require verification against the updated integration base.
Use retained isolation evidence before raising concurrency; a larger configured number does
not itself prove safe execution.

Clone snapshots come from the actual VM, preserving commits, index, dirty and untracked source;
the host checkout is not a fallback authority. Supported relative aliases are retained, including
the stacks' `.claude` aliases. External, cyclic or noncanonical links, submodules, unmerged
indexes and oversized snapshots refuse with work preserved. Export is bounded to 100,000 files
and 64 MiB serialized data. Existing clones without protected mailboxes follow an explicit
preservation/drain transition rather than being silently rebuilt.

## Telemetry and costs

Current context, cumulative usage and estimated spend are separate measurements. The console
shows intermediate context occupancy and freshness; absent/stale measurements are labeled.
Cumulative billed tokens are never used as context occupancy. Context policy warns at 70% and
requests safe-boundary compaction at 80%, subject to earlier runtime compaction. Changed authority
and repeated failures require fresh handoffs, not just compaction.

Costs are **API-equivalent estimated USD**, not Codex account charges. Dated pricing accounts
for supported cache, output, long-context and service-tier details; every model invocation is
tracked, including failed attempts, review, diagnosis, certification and children. Replayed
events reconcile idempotently. Missing usage or pricing remains visibly incomplete, with known
cost retained as a lower bound. Historical usage is estimated only where evidence supports it.
Budgets are checked before subsequent attempts, not by killing a writer midway through a change.
The checked-in routing currently declares a $50 run ceiling and $30 warning; inspect effective
configuration rather than assuming those defaults govern every run.

## Learning capture and relevant recall

Learnings are Markdown notes in the configured Obsidian vault's `Project Learnings/`
directory. They are not stored solely in Factory artifacts. Successful capture writes the
note and updates `Project Learnings/_INDEX.md` and `_VAULT_INDEX.md`; session identity
keeps repeated capture attached to the same note. Existing full-session notes are preserved
when Factory only has partial evidence.

For Factory, the host registry's `[vault].path` in `config/projects.toml` selects that vault.
The detached capture worker passes it as `OBSIDIAN_VAULT_DIRECTORY` to the registered host
repository's vendored capture script. Interactive sessions use their own runtime environment;
a missing variable in an ordinary shell does not prove the agent is unconfigured. See the
[shared learning instructions](../.agents/vendor/harness/docs/agents/learnings.md) for
configuration, backend selection and bounded recall policy. Those details belong to layer A.

Native session-end capture reads the runtime transcript. Interrupted sessions can skip that
hook. Separately, Factory collection exports the exact session's native transcript while its
sandbox is still available, before suspension or cancellation archival. The bounded lookup
uses the measured runtime session index; unavailable or unsupported inputs retain the event
fallback. `*.native.jsonl` preserves exported bytes and `*.native.json` records retention.
`*.learning.jsonl` is the distillation snapshot; `*.learning.json` records outcome and retryability.
Sources are labeled `retained-native-transcript`, `retained-native-prefix` for an incomplete
final record, or `retained-events-partial`. Native source does not imply successful session
completion. Shared parsing preserves native user, tool and assistant content.

Export is bounded synchronous collection; model distillation remains detached on the host.
Secret scanning quarantines unsafe evidence before learning processing; replay honors that
quarantine. Partial evidence cannot replace an existing same-session note.

Inspect the receipt to distinguish unavailable input/configuration, failed processing,
unfinished work and a completed result. Recollection can retry failed or interrupted work;
unchanged snapshots with finished non-retryable results are skipped. Detached workers do
not constitute a durable job queue, and old attempts never recollected remain recoverable
gaps. This repair does not run bulk historical recovery.

At task start, shared hooks supply a bounded current-project index. Topic recall before
planning or debugging selects a bounded set of relevant note bodies, including other
projects when relevant, and records their source paths. It does not load the whole vault.
Unavailable retrieval differs from no relevant results. After zero summary/path matches,
the hook searches a bounded set of indexed note bodies. Budget exhaustion reports partial
evidence. Unindexed notes and body details alongside summary hits still require the shared
skill's wider search. Notes are historical evidence, never executable instructions.

[Real-destination measurements](acceptance/learning-continuation-2026-09-09.md) connect a
genuine audit lesson in Obsidian Project Learnings to later recall in another worktree. The [lifecycle acceptance record](acceptance/learning-lifecycle-2026-09-08.md)
separately proves Factory's transport and receipts. Later [interactive measurements](discovery/interactive-learning-completion-2026-09-09.md)
prove Codex terminal shutdown, capture and recall. [Actual sandbox export and host-worker measurements](discovery/native-sandbox-learning-2026-09-09.md)
cover both runtimes and the private-clone topology. Source completeness and runtime
completion remain different facts. Claude
authenticated capture/recall awaits host login renewal.

## Commands and observation

All commands below use `uv run factory`:

| Command | Purpose |
| --- | --- |
| `run BAC-6 [--check] [--plan] [--full-review] [--no-follow]` | Check or drive one approved ticket; optional planning/review controls |
| `tick --once [--verbose] [--no-claim]` | Reap, recover and advance runs, then claim eligible work; `--no-claim` still advances existing work |
| `daemon [--interval 30]` | Repeat ticks; the supplied launchd writer instead schedules `tick --once` |
| `status [BAC-6] [--all] [--evidence]` | States, liveness and retained evidence |
| `logs BAC-6 --follow` | Follow a run's event stream |
| `runtimes` | Join sandbox inventory to factory runs |
| `config models` | Inspect validated routing |
| `serve` | Console: policy, models, approvals, telemetry, certification, child and capacity status |
| `suspend BAC-6` / `resume BAC-6` | Stop current work or resume preserved work |
| `review-disposition-template BAC-6` | Print a non-valid decision draft bound to the latest blocking review |
| `review-disposition-check BAC-6 PATH` | Validate a completed decision document without writing Factory state |
| `resume BAC-6 --from implementing --review-disposition PATH` | Retain James's ticket/run/review-bound finding decisions and render them into a fresh repair prompt |
| `resume BAC-6 --authorise` | Explicitly re-authorize an exhausted run |
| `accept BAC-6 --note "decision"` | Record a human review-escalation decision |
| `cancel BAC-6 --reason "duplicate"` | Cancel owned work and retain anything unsafe to clean |
| `complete BAC-6` | Record the human-merged PR as complete |
| `gc --dry-run` | Preview ownership-aware cleanup |
| `metrics --database PATH [--project PROJECT]` | Evaluate interventions, failure episodes, completion and estimated cost |
| `migrate --database PATH` | Read-only migration preview; application is a separate approved operation |

Eligibility requires the human-owned `ready-for-agent` label, an allowed workflow state and
team, sufficient parent specification and acceptance criteria, no blocking labels or existing
PR, and matching repository tracker configuration. `run --check` reports current conditions.
Read [the runbook](runbook.md) before rewinding, cancelling or cleaning a stuck run.

## State, recovery and boundaries

`state/factory.db` stores runs, snapshots, effects, approvals, invocation accounting,
certification, child requests and capacity ownership. Artifacts retain attempt outputs,
fingerprinted evidence and handoffs; logs remain outside model context. **The database is
not trivially rebuildable from Linear and Git.** Back it up consistently before migration
and preserve evidence alongside it. Use the [rollout procedure](runtime-certification-rollout.md)
for writer shutdown, SQLite backup, schema preview/application and rollback without losing
newly recorded effects.

Detached execution survives controller exit through an independent session holder. It does
not survive every machine failure: reboot, logout, Docker shutdown or stopping the VM ends
execution. Recovery reconciles the recorded process/thread and effects; uncertain ownership
holds rather than authorizing duplicate work. Parent VM/native-thread transfer retains source,
private dependencies and owner checks. Cancel, Suspend and GC target owned child/parent resources
and preserve work they cannot safely remove.

The host records external writes before calling adapters, then reconciles their outcome.
Ambiguous acknowledgements are not permission to retry blindly. Uncertain zero-turn preparation
also retains evidence for operator reconciliation.

James owns ticket/spec approval, the readiness label, merges, deployments, schema migrations,
credential rotation and disputed review decisions. Factory has no merge, force-push or
create-ticket path. It never attaches to `codex-*` sandboxes, writes the host
`~/.codex/config.toml`, hand-edits generated vendor content or creates `~/.factory/`.
Factory-owned sandboxes use `factory-build-*` and `factory-review-*` names. Capability checks
inspect sbx-managed secrets; the narrowly approved project-specific delivery exception and
its proxy boundary are documented in [AGENTS.md](../AGENTS.md).

## Configuration and development

- `config/projects.toml`: project registry, layout, environment, isolation and retention defaults.
- `config/models.toml`: validated role routing, model catalogue and budgets.
- `config/prices.toml`: dated estimate inputs; missing prices remain unknown.
- Target `harness.config.json`: delivery policy, components, capabilities and gate commands.
- Runtime database settings: explicit project/run operator controls and frozen run selections.

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

These four gates are declared in this repository's `harness.config.json` and enforced by its
layer-A workflow. Run them and satisfy any required PR checks before merging. Fake-adapter tests
validate control-plane logic; real disposable measurements validate runtime effects. Neither
replaces the other.

Branches are `<type>/<slug>`. Factory's release branch is `main`; shared/stack harness work
originates on `v2` and generated content is synced. Do not edit `.agents/vendor/` directly.
