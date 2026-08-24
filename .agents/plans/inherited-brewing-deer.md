# Phase 6 — shared layer-A doctrine + vendor synchronisation

## Context

Phase 5 is complete and closed (both exit tickets merged, eight defects recorded). The
handoff's prerequisite — merge #57 (mypy fix) — is already satisfied; `factory@main` is at
`26a490f`, mypy clean on 75 files, 583 tests green.

Phase 6 is **evidence-gated**: the plan (§19) says "only changes that Phases 1–5 *proved*
were needed, each requiring evidence from a real run before it is written." Three candidates
were audited against the Phase 5 run artifacts (BAC-6, FRO-11) and the §19 eight-defect table:

| candidate | verdict | reason |
| --- | --- | --- |
| `factory` review frame | **NO — dropped** | Every real finding across BAC-6/FRO-11 maps to an existing axis (standards, test, spec, a11y, perf, cost, simplicity). The full nine-axis Tier-2 fan-out actually ran in both. Defect 7 was a *trigger/data* problem (fixed in factory#47), not an axis gap; the other seven are control-plane runtime behaviours, not diff-reviewable. The gate ("IF Tier-1 findings show a recurring axis gap") is not met. |
| `gate_report.mjs` `when` flag | **NO — dropped** | The preflight never reads `when` (it asserts enforcement infrastructure). `when` clauses are deliberately fuzzy prose. The FRO-6/FRO-7 failures were argv-construction bugs, already fixed by per-gate `--gate` + `enabled:false`. `requires` (harness#18) already covers the machine-checkable part. A `when` flag would duplicate the agent's job on the machine side. |
| `docs/agents/factory.md` | **YES (narrow)** | Evidence-backed: the registry/prefix-resolution mechanism, the harness-eligible vs factory-eligible distinction (test-project is the first, not the second), the monorepo-dispatch test. NOT a prescriptive "how to make a product repo drivable" section — that path was never exercised and would be speculative filler. |

**Outcome: Phase 6 is two layer-A doctrine files, not three changes.** Candidates 1 and 2 are
not written — writing them would violate the plan's evidence gate. James confirmed the scope
also completes the pending §20.2 `issue-tracker.md` paragraph ("unattended runs: the control
plane writes"), which never landed in Phase 5.

## What ships

All layer-A work lands in **one harness PR on `v2`**; both consumers are then vendor-synced
to the merged sha. Every merge is a human-approval boundary (James).

### 1. New file — `plugins/harness/docs/agents/factory.md` (harness@v2)

Layer-A doctrine on how a repo declares itself factory-eligible. Evidence-backed sections
only (per the candidate-3 audit); no speculative how-to:

1. **What factory-eligible means** — a registry row in the factory's `config/projects.toml`
   whose `team` matches a Linear ticket prefix. Zero matches = not eligible (blocked once);
   two matches = the daemon refuses to start (`RegistryError`). After resolution,
   `harness.config.json` must exist and its `tracker.team` must equal the ticket's team.
   Cross-ref `config.md` (harness.config.json) and `issue-tracker.md` (the team key).
2. **The two kinds of eligible** — *harness-eligible* (has `harness.config.json`, gates,
   `AGENTS.md`, the vendored tree) vs *factory-eligible* (has a registry row + `tracker.team`).
   `jchen1707/test-project` is the first and not the second — the measured proof from Phase 5.
3. **What a monorepo product repo looks like** — root config declares `apps` and no gates,
   no `tracker.team`; each app has its own `harness.config.json`; no `run[0]` cross-check at
   root. Dispatch is layer A's (`dispatch()` resolves gates per app by changed path).
4. **What is not built** — a product repo with no Linear team prefix cannot be driven by the
   factory today. The alternative (resolve by label or Linear project *within* a team, §10.2)
   is a known gap, not a working path. Name it as unbuilt; do not describe how to do it.

**Layer boundary:** this is layer-A prose describing what a repo declares to be *driven by*
layer D. It is not the factory's code, registry, or resolution algorithm (those live in
`src/factory/registry.py`, `config/projects.toml`). It respects "the factory holds no gate
command, no review prompt, no checklist" — it is none of those.

### 2. Edit — `plugins/harness/docs/agents/issue-tracker.md` (harness@v2)

Add one section, "Unattended runs and the control plane" (the §20.2 paragraph): in an
unattended factory run the control plane (layer D) owns every tracker write — claiming the
issue, moving status, applying/removing labels, posting comments — through its effects
ledger; the sandboxed agent writes to the tracker neither directly nor via the Linear API.
So an unattended run's tracker state is authoritative regardless of what the agent's
transcript says. Evidence: Phase 4.5/5 unattended runs; the factory `AGENTS.md` rule
("control plane owns every tracker and GitHub write"); FRO-11's spec-checker could not reach
Linear precisely because the agent does not write the tracker.

This is shared doctrine → it auto-vendors into both consumers'
`.agents/vendor/harness/docs/agents/issue-tracker.md`. The consumers' own per-repo
`docs/agents/issue-tracker.md` files are thin wrappers ("shared doctrine lives in the
vendored copy … this file records only what is true in THIS repo") and are **not** edited for
the shared paragraph.

### 3. Verify the layer-A change (before opening the harness PR)

From `/Users/james/harness` (on the `feat/` branch):
- `python3 scripts/check.py --since=<base>` — the harness repo's own DoD.
- `python3 scripts/cross_stack.py` — syncs the working-tree layer A into both mounted
  consumer submodules (python-harness, frontend-harness on v2) and runs the gates each
  stack declares in its own `harness.config.json`. This is the one question neither stack
  can ask from inside itself: does the layer-A change break a stack? Doctrine prose should
  touch no gate, but run it to prove that rather than assert it.

### 4. Vendor-sync both consumers (after the harness PR merges to v2)

For each of `python-harness@v2` and `frontend-harness@v2`, from `/Users/james/harness`:
- `python3 scripts/vendor_sync.py sync --harness /Users/james/harness --target <consumer>`
  — bumps the pin to the new v2 HEAD, regenerating `.agents/vendor/harness/**` +
  `MANIFEST.json`. This picks up harness#19 (the `requires`-example fix, currently unvendored:
  factory pins `5cdb49f`, v2 HEAD is `a3beb34`) **plus** the new `factory.md` and the
  issue-tracker paragraph.
- `python3 scripts/vendor_sync.py check --target <consumer>` — the freshly synced tree must
  pass its own integrity check.
- Commit on `chore/vendor-gate-report` (per §20.3/§20.4) → PR → `v2`. **James merges.**

The two consumer PRs are independent of each other but both depend on the harness PR merging
first (they pin to its sha).

## Out of scope (noted, not done)

- **The factory's own `.agents/vendor/harness/` pin** (`5cdb49f`) is behind v2 HEAD. §20
  lists only the two consumers as vendor-sync targets, so the factory's pin is not bumped
  here. It is a separate hygiene cleanup (catch up to harness#19); the factory's suite stays
  green because its tests check vendor *integrity against the pinned sha*, not freshness.
- **A repo-specific "this repo is factory-driven" note** in the consumers' per-repo
  `issue-tracker.md` wrappers (§20.3/§20.4 list a consumer `issue-tracker.md` row). The
  shared paragraph reaches readers via the vendored copy; a repo-specific bullet is a
  possible one-line follow-up, not required for the doctrine to land.
- **Candidates 1 and 2** — deliberately not written. If a future run produces the evidence
  the plan requires (a recurring Tier-1 axis gap; a `when` clause the path-check cannot
  resolve), they reopen then.

## Verification

1. `cross_stack.py` green against both stacks (layer A does not break either stack's gates).
2. `vendor_sync.py check` passes in both consumers after sync.
3. `factory@main` suite + mypy stay green (no factory code changes; the factory's vendor pin
   is untouched).
4. The harness PR diff is exactly: one new `docs/agents/factory.md`, one added section in
   `docs/agents/issue-tracker.md` — nothing in `src/`, `hooks/`, `schema/`, or `workflows/`.

## Rollback

Revert the layer-A PR on `v2`; re-sync both consumers to the previous sha
(`5cdb49f`). `vendor_sync.py sync` is idempotent against a pinned sha.