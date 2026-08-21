# Handoff — Phase 2 step 2, the verify step

Written 2026-08-21, at the end of the session that landed layer A. Read `AGENTS.md`
first, then `docs/handoff-phase-2.md`, then this note. This note is temporary: retire it
into the evidence once a real run reaches `verifying → reviewing` (or loops back), the
way `p1-3` retired the Phase 1 handoff.

## Where things stand

Layer A is done and merged. PR #13 (`feat(layer-a): machine-readable gate report`) merged
into `jchen1707/harness@v2` at commit `3621178` on 2026-08-21; the plugin version is now
`0.6.0`. `main` regenerated itself via `generate-main.yml`; do not hand-edit it.

What shipped, all under already-vendored subtrees (no `vendor_sync.py` `VENDORED` edit):

| File | Change |
| --- | --- |
| `plugins/harness/hooks/gate_report.mjs` | **New.** §12.1 — the machine-readable gate report |
| `plugins/harness/hooks/hooks.test.mjs` | +381 lines: `unavailable`, `not_applicable`, `skipped_unchanged`, monorepo dispatch, `verdict: incomplete`, exit codes 0/1/3 |
| `plugins/harness/schema/review-findings.schema.json` | **New**, extracted from `full-review.js` |
| `plugins/harness/schema/harness.config.schema.json` | The optional `tests` key (§15.3, for Phase 3's red-phase replay — **not** step 2) |
| `plugins/harness/workflows/full-review.js` | Reads the schema file instead of the inline constant |
| `plugins/harness/skills/verify/SKILL.md` | One paragraph on the machine-readable form |
| `plugins/harness/docs/agents/config.md` | The two-scope vault-variable rule (§9.2) |

`gate_report.mjs` reuses layer A — `dispatch`, `gatedChange`, `STOP_KINDS` from
`verify.mjs`; `loadConfig`, `runArgv`, `repoRelative`, `tail` from `lib.mjs` — and copies
no classification logic. The factory must not re-derive which gates apply in Python; it
reads this document.

## What step 2 is

One new step, `src/factory/steps/verify.py`, that runs `gate_report.mjs --json` inside the
build sandbox, writes `gates.json` into the attempt directory, applies the §15.1
evidence-mismatch rule, and transitions out of `verifying`. This is the third of the three
independent verification signals in §15.1 — the Stop hook, the agent's `gates_run` claim,
and this report — and the state machine advances on the third, cross-checked against the
first two.

## The verify step's job

1. **Run the report in the build sandbox.** Invoke the vendored
   `.agents/vendor/harness/hooks/gate_report.mjs --json` (and `--all` when the opt-in gates
   ran — see below) via `ctx.sandbox.exec_sync` in `ctx.project.build_sandbox`, with the
   worktree as `workdir`. It is an **argv list, never a shell string** — `shell=True`
   appears nowhere. Read stdout as JSON; the exit code is the verdict
   (`0` pass, `1` fail, `3` incomplete) but the JSON `verdict` field is authoritative.
2. **Write `gates.json`** into the implement attempt's `AttemptDir` (beside
   `last-message.json`), and `record_check` it — pass or fail.
3. **Cross-check `gates_run` vs `gates.json`** (§15.1). Read `gates_run` from the
   implementer's `last-message.json` (already schema-validated by `implement.py`). For
   every gate the agent claims it ran, the report must show that gate with a status of
   `pass` or `fail`. A claimed gate the report marks `unavailable`, `not_applicable`,
   `skipped_unchanged`, or absent is a disagreement → `Blocked("evidence-mismatch", ...)`.
   An honest `gates_run` containing a failure is a better outcome than an optimistic one.
4. **Transition.** Recommended, from §19's "reaches `verifying → reviewing` or loops back
   to `implementing` on a real failure":

   | report `verdict` | evidence-mismatch? | next state |
   | --- | --- | --- |
   | `pass` | no | `advance(ctx, State.REVIEWING)` |
   | `fail` | no | `advance(ctx, State.IMPLEMENTING)` — loop back for a real failure |
   | `incomplete` | no | `Blocked` — incomplete is never a pass; an unavailable gate or a missing app is an environment/manifest problem, not a code failure |
   | any | yes | `Blocked("evidence-mismatch", ...)` |

   `fail` outranks `incomplete` in the report, so the `fail` row is reached before the
   `incomplete` row. The `incomplete`-without-mismatch case is the one design decision step
   2 has to make: I recommend a `Blocked` with a reason slug (the plan names
   `evidence-mismatch` canonically; `gates-incomplete` is new but `Blocked` accepts any
   slug — `machine.py:179`). If the next session prefers to loop `incomplete` back to
   `implementing` instead, that is defensible too, but it sends the agent back to fix code
   for a toolchain that could not start, which is the wrong loop. Flag the choice in the
   PR.

## The `--all` decision

§12.1: opt-in is not optional, and the factory makes the `--all` decision "from the
agent's structured answer plus a deterministic path check against `harness.config.json`'s
own `gatedPaths` — never by pattern-matching the diff." The faithful reading: pass `--all`
iff the agent's `gates_run` names a gate whose `kind` is `e2e` or `integration`
(cross-check by name against `ctx.harness.gates`). Without `--all`, those gates come back
`not_applicable`, which is correct when the agent did not claim them.

## The `gate_report.mjs` output shape — the contract the schema and step must match

```json
{
  "schemaVersion": 1,
  "root": "/abs/path/to/worktree",
  "targets": [{ "name": "python-harness", "dir": "." }],
  "missingApps": [],
  "gates": [
    {
      "name": "ruff check",
      "kind": "lint",
      "status": "pass",
      "exit": 0,
      "durationMs": 1200,
      "caveat": null,
      "when": null,
      "outputTail": ""
    }
  ],
  "verdict": "pass"
}
```

- `gates[].status` ∈ `pass | fail | unavailable | not_applicable | skipped_unchanged`
- `verdict` ∈ `pass | fail | incomplete`; `incomplete` when any gate is `unavailable` **or**
  `missingApps` is non-empty; `fail` outranks `incomplete`.
- exit codes `0` / `1` / `3` for pass / fail / incomplete.
- `outputTail` is `""` on `pass`; the tail of stdout+stderr (or the spawn error) otherwise.
- `caveat` and `when` are always present (possibly `null`) — they name how a green result
  proved nothing, which matters beside a `pass` too.

## The factory APIs to write against

Citations are against `factory@main` as of this writing; confirm line numbers before
relying on them.

- **Step signature** — mirror `implement.py` / `context.py`:
  `def run(ctx: Context) -> None`, `__all__ = ["run"]`, module docstring naming the
  transition. State changes only through `advance(ctx, State.X)` (`factory.steps.advance`).
  Failures are `Blocked(reason, detail)` (needs a human) or `Resumable(reason, detail)`
  (this attempt died). Canonical `Blocked` reasons are listed at `machine.py:179`;
  `evidence-mismatch` is one of them.
- **Attempt directory** — reuse the **implement** attempt's `AttemptDir` so `gates.json`
  sits beside `last-message.json`. `implement.py:51` computes `attempt = ctx.run.attempt + 1`
  and `AttemptDir.create(worktree, attempt)`. Do the same to resolve the same directory;
  read `gates_run` from `attempt_dir.last_message` and write `gates.json` to
  `attempt_dir.root`. (Confirm `ctx.run.attempt` semantics at write time — the implement
  step records the attempt, so verify should resolve the directory the same way implement
  did.)
- **Sandbox call** — `ctx.sandbox.exec_sync(name, argv, *, workdir, env, timeout, stdin)
  -> Completed` (`sandbox/base.py:109`). `Completed` has `.returncode`, `.stdout`,
  `.stderr`, `.ok`. Pass `name=ctx.project.build_sandbox`, `workdir=str(ctx.worktree)`,
  `env=dict(ctx.project.env)`. The argv is the report invocation as a list.
- **Harness config** — `ctx.harness` (a `HarnessConfig`, set in `context.py:36`). Fields:
  `.gates: tuple[Gate, ...]` (each `Gate` has `.name`, `.kind`, `.run`), `.gated_paths`,
  `.tests`. Use `.gates` to resolve `--all` by name→kind. The `tests` field is Phase 3's
  (red-phase replay), not step 2.
- **Recording a check** — `ctx.store.record_check(run_id, attempt, name, status, detail=,
  artifact=)` (used at `implement.py:232,297,319`). Record the gate report as a check
  (`"gate_report"`, `"pass"`/`"fail"`), and record `evidence-mismatch` as a `"fail"` check
  when it fires, with the disagreeing gate names in `detail`.
- **Logging** — `ctx.log("verify.report", verdict=..., ...)` for the audit stream, the way
  `implement.py` logs `implement.finished`.

## Wiring

- **`src/factory/cli.py`** — `_drive` (`cli.py:195`) ends at `implement_step.run(ctx)`.
  Add `verify_step.run(ctx)` after it, and the import at the top with the other step
  imports (`cli.py:38-45`). The clean-run integration test's expected hop list
  (`test_pipeline.py:173`) currently ends at `verifying`; step 2 extends it to `reviewing`
  (or to the loop-back / blocked shape the new tests add).
- **`schemas/gate_report.schema.json`** — **New**, a copy for validation only, sourced
  from layer A. The factory validates the report against it before trusting the verdict.

### The schema constraint that will bite

The factory does not use a real JSON-Schema library. `validate_against_schema`
(`agent/base.py:148`) refuses any keyword outside `_SUPPORTED` (`agent/base.py:117`):
`$schema, $id, title, description, type, enum, const, required, properties,
additionalProperties, items, maxLength, minLength, minimum, minItems`. **No `oneOf`,
`anyOf`, `pattern`, or `$ref`.** Author `gate_report.schema.json` with only those
keywords: express `status` and `verdict` as `enum`, nest `gates[]` under `items`, and
inline every subschema (no `$ref`). Mirror the house style of
`schemas/implement_result.schema.json` (`$schema` draft 2020-12, an `$id` pointing at the
GitHub path, `title`, `description`).

Note the distinction: `implement_result.schema.json` requires *every* property to also be
in `required` because OpenAI structured outputs reject a response format where it does
not, and that schema is handed to `codex exec --output-schema`. `gate_report.schema.json`
is **not** handed to a model — the factory validates its own copy of the report with
`validate_against_schema` — so the "every key required" rule does **not** apply. Use
`required` only for the fields the step actually reads (`verdict`, `gates`, `schemaVersion`).

## Fixtures under `tests/fixtures/`

Three, matching `handoff-phase-2.md`'s list:

1. **A deliberately broken gate** — a gate whose `run` exits non-zero → a `fail` row,
   `verdict: fail` → the step loops to `implementing`.
2. **A gate whose binary is absent** — `run` references a command not on `PATH` →
   `unavailable` → `verdict: incomplete` → the step blocks (no evidence-mismatch, since the
   agent did not claim it).
3. **A two-app monorepo** — root config declares `apps`, each app has its own gates; a turn
   that touched one app shows `skipped_unchanged` for the other's gates and dispatches
   correctly.

The integration tests use `FakeSandbox` (`tests/integration/conftest.py:58`), which writes
canned files in `exec_detached`. For step 2, extend `FakeSandbox.exec_sync`
(`conftest.py:92`) to recognise the `gate_report.mjs` argv and return a canned
`Completed(stdout=<report json>, returncode=<verdict exit>)` — keyed off `argv`, the way
the existing `protect_paths.mjs` and `HARNESS_SKIP_VERIFY` branches are. `GOOD_RESULT`
(`conftest.py:41`) already claims `gates_run: ["ruff check", "mypy", "pytest"]`, so the
default fake report's gate names must include those three or the clean run hits
`evidence-mismatch` — align them.

## Files to touch

| File | Change |
| --- | --- |
| `src/factory/steps/verify.py` | **New** — the step |
| `src/factory/cli.py` | Import + one line in `_drive` |
| `schemas/gate_report.schema.json` | **New** — validation copy, `_SUPPORTED` keywords only |
| `tests/fixtures/` | Three new fixtures (broken / absent-binary / two-app monorepo) |
| `tests/integration/test_pipeline.py` | Extend `_drive` (line 154) with `verify_step.run(ctx)`; extend the expected hops (line 173); add fail-loop, incomplete-block, and evidence-mismatch tests |

Branch off `origin/main` in `jchen1707/factory`. The factory holds no gate command and no
review prompt — a gate name in `src/` is a review failure. `verify.py` invokes
`gate_report.mjs` by path; it does not name `ruff`, `pytest`, or any other gate.

## Validate

```sh
# in /Users/james/factory
python -m pytest tests/integration/test_pipeline.py -q
python -m pytest tests/unit -q           # if a verify unit test is added
ruff check . && ruff format --check .
mypy src/factory
```

Then open the PR to `main` and stop for James to merge.

## Boundary constraints (from AGENTS.md — preserve)

- The factory holds no review prompt, no gate command, no checklist. A gate name in `src/`
  is a review failure.
- Never write to `~/.codex/config.toml`. Never edit anything under `.agents/vendor/`
  (generated, pinned by sha — edit in harness and re-sync).
- Argv lists, never shell strings; `shell=True` appears nowhere.
- `policy.requires_human()` is the only function human-only rules relax in.
- Sandboxes are `factory-build-*` / `factory-review-*` — never attach to, stop, or remove a
  `codex-*` sandbox (James's live session).
- Never create `~/.factory/` (belongs to Factory.ai's Droid; this factory lives at
  `~/factory`).

## What comes after, and what is still James's

- **Step 3 — vendor-sync both consumers** on `chore/vendor-gate-report`, after James merges
  step 2's PR. Step 2's unit/integration tests use fakes and do not need the vendored
  `gate_report.mjs`; a **real** verifying run (step 4) does, so step 3 must precede step 4.
  ```sh
  python3 /Users/james/harness/scripts/vendor_sync.py sync \
    --target /Users/james/python-harness --harness /Users/james/harness
  # commit .agents/vendor/harness/** and MANIFEST.json only; same for frontend-harness
  ```
- **Step 4 — a real run** reaching `verifying → reviewing` (or looping back) with a
  `gates.json` carrying a `verdict`. Nothing short of a real run demonstrates Phase 2.
- **§8.7 egress chore** — `sbx policy rm network --id default-cloud-infrastructure`.
  Explicitly James's, not an agent's command (policy scope is global; it affects every
  sandbox including live `codex-*` sessions).
- **Stale `github` credential binding** on `factory-build-python-harness`; the
  `needs-info` label group — neither blocking.