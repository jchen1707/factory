> Superseded handoff (2026-08-21). Phase 2 was validated; see
> [the retained measurement](../discovery/p2-2-step-4-validated.md) and
> [current workflows](../workflows.md). Original text follows unchanged.

# Handoff — starting Phase 2

Written 2026-08-21, at the end of the session that resolved the two items parked in
`AGENTS.md`. Read `AGENTS.md` first, then `docs/discovery/p1-3-phase-1-validated.md`.
This note is temporary: retire it into the evidence once Phase 2 is validated, the way
`p1-3` retired the Phase 1 handoff.

## Where things stand

Phase 1 is validated and nothing is parked any more.

- **Defect 6 is fixed.** `cancel` derives the worktree path from the ticket and finds the
  branch by scanning local branches, so a run that dies inside `git worktree add` no
  longer leaves debris that blocks every later run. Two refusals bound it, and whatever
  it refuses to remove, it names. `docs/defect-6-cancel-orphan-cleanup.md`.
- **§4.2's durability guarantee holds.** A sandbox auto-stops 30 s after its last session
  disconnects — sessions, not idleness, with no keep-alive flag on v0.38.0 — but the
  session is held by an ordinary host process, so `start_new_session=True` puts it
  outside the factory's process group and pid 1 adopts it. The factory process is out of
  the run's TCB; the machine is still in it. `docs/discovery/p1-2-detached-exec.md` §3
  tabulates the three cases Phase 4's daemon, `resume` and `recovery.py` must handle.

`P0-15`'s prerequisite is satisfied: eight skills are symlinked into `~/.agents/skills`,
host-side Codex lists them, and `sbx skills import` has run. Note the factory **inlines**
the `implement` skill rather than invoking it — Codex removes a skill marked
`allow_implicit_invocation: false` from the catalog entirely — so §19's "the documented
workflow" clause is met by a different mechanism than the plan anticipated
(`docs/discovery/p0-15-skills.md`).

## One chore before Phase 2, and it is James's

§8.7's table says to audit `sbx policy ls` and remove broad entries **before Phase 2**.
It has not been done. Measured 2026-08-21:

```console
$ sbx policy ls local-policy --wide
local  all  local-policy  default-cloud-infrastructure  network  allow  **.amazonaws.com:443
                                                                        **.googleapis.com:443
```

P0-4 already chose the fix and the reasoning: the group is exfiltration-shaped — any
bucket, any project — and neither stack's gates need it.

```sh
sbx policy rm network --id default-cloud-infrastructure
```

**Not an agent's command to run.** Policy scope is global on this version, so it changes
egress for every sandbox on the machine, `codex-*` sessions included.

## Phase 2 starts in `harness`, not here

`plugins/harness/hooks/gate_report.mjs` does not exist on `v2`, there is no
`feat/gate-report` branch, and no PR is open in either repo. §19's file list is the
contract; the order below is what the dependency implies.

**1. Layer A — `feat/gate-report` off `jchen1707/harness@v2`.**

| File | Change |
| --- | --- |
| `plugins/harness/hooks/gate_report.mjs` | **New.** §12.1 |
| `plugins/harness/hooks/hooks.test.mjs` | `unavailable`, `not_applicable`, monorepo dispatch, `verdict: incomplete`, exit codes 0/1/3 |
| `plugins/harness/schema/review-findings.schema.json` | **New**, extracted from `full-review.js` |
| `plugins/harness/schema/harness.config.schema.json` | The optional `tests` key (§15.3) |
| `plugins/harness/workflows/full-review.js` | Read the schema file, not the inline constant |
| `plugins/harness/skills/verify/SKILL.md` | One paragraph on the machine-readable form |
| `plugins/harness/docs/agents/config.md` | The two-scope vault-variable rule (§9.2) |

Validate with `python3 scripts/check.py`,
`node --test plugins/harness/hooks/hooks.test.mjs`, `npx --yes prettier@3 --check .`.
Open the PR to `v2` and **stop**. James merges it — that merge republishes the plugin for
every Claude Code consumer at once, which is exactly why it is a human decision. `main`
regenerates itself via `generate-main.yml` in all three repos; never hand-edit it.

**2. In `factory`, in parallel** — this needs the schema, not the merge:

- `src/factory/steps/verify.py`
- `schemas/gate_report.schema.json`, a copy for validation only, sourced from layer A
- the evidence-mismatch rule (§15.1)
- fixtures under `tests/fixtures/`: a deliberately broken gate, a gate whose binary is
  absent (→ `unavailable`), and a monorepo shape with two apps

**The factory holds no gate command.** It reads them from the target repository's
`harness.config.json`. A gate name appearing in `src/` is a review failure.

**3. After the merge.** Vendor-sync both consumers on `chore/vendor-gate-report`:

```sh
python3 /Users/james/harness/scripts/vendor_sync.py sync \
  --target /Users/james/python-harness --harness /Users/james/harness
# commit .agents/vendor/harness/** and MANIFEST.json only
```

Same for `frontend-harness`; each repo's own CI (`vendor-freshness.yml` + `ci.yml`)
validates it. Never edit anything under `.agents/vendor/` by hand.

**4. Then a real run.** `gates.json` in the attempt directory carrying a `verdict`, and a
run that reaches `verifying → reviewing` or loops back to `implementing` on a real
failure. That is Phase 2's expected output, and nothing short of a real run demonstrates
it — a green suite proved nothing about any of the nine defects that stopped Phase 1.

## Still James's, neither blocking

- The stale `github` credential binding on `factory-build-python-harness` (the `-2` name
  is clean and the registry uses it) — `docs/discovery/p1-1-in-image-codex.md`.
- The `needs-info` label group that makes eligibility condition 7 inert.
