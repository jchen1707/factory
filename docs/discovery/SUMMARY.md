# Phase 0 — summary

Run 2026-08-20. All fifteen steps attempted, **all fifteen answered, and every human item
closed.** Nothing was committed, pushed, opened as a PR, or landed on `main`.

**Follow-up, same day.** James stored the `factory-linear` key (P0-8 now passes, verified
against the API). At his instruction, **BAC-4 was re-opened from `Canceled` to `Todo`** — the
one Linear write this phase made, and the only one. Phase 1's target is settled: see
"Phase 1 runs against BAC-4" below.

## Step results

| Step | Verdict | One line |
| --- | --- | --- |
| P0-1 branch heads | ✅ | Matches §1.1 exactly, all six refs, clean worktrees |
| P0-2 `sbx login` | ✅ | Already authenticated — the human step is not needed |
| **P0-3 toolchain** | ✅ **not blocking** | `node v22.22.1` **and** `uv 0.9.26` in the stock image → no new Python template |
| P0-4 egress | ✅ | 194 allow rules, 6 prunable groups; filesystem is `**` and not CLI-editable |
| P0-5 sandboxes | ✅ | Four `codex-*`; the `factory-*` namespace is free |
| **P0-6 Codex trust** | ✅ **answered** | An untrusted project is **not** refused; hooks silently do not fire |
| P0-7 event shape | ✅ | Captured, 6 fixtures; three claimed fields **refuted** |
| P0-8 Linear key | ✅ | `factory-linear` stored by James and **verified working** against the API |
| P0-9 stale hook trust | ✅ | Re-confirmed; `frontend-harness` is unprotected today |
| P0-10 gate timing | ✅ | Whole Python suite < 3 s; a shared bind mount breaks both stacks |
| P0-11 intake dry run | ✅ | 14 labelled; **3 eligible at the time, all FRO** — all 9 BAC issues were `Canceled`. BAC-4 has since been re-opened |
| P0-12 model cache | ✅ | Fresh, `client_version` matches; three corrections to §4.5's table |
| P0-13 vault | ✅ | 162 files / 15.7 MB / **22 ms** — confirms §8.5 |
| P0-14 red-phase | ✅ | **17 % inconclusive** over 6 replays → `inconclusive_alarm_pct = 40` |
| **P0-15 skills** | ⚠️ **works, with a hole** | The chain delivers 8 skills; **`implement` is unreachable** |
| P0-16 invocation policy | ✅ | Honoured, and proven more strongly than the step asked |

## §19's gate: "No phase after this may proceed while P0-3, P0-6 or P0-12 is unanswered"

**All three are answered. The gate is open.**

- **P0-3** — the highest-value check, and the answer is the good one. `node ≥ 22` is present,
  so no layer-A `.mjs` hook silently fails to spawn, and `verify.mjs` will not return 0 on a
  missing runtime. No template work.
- **P0-6** — `codex exec` does **not** refuse an untrusted project, so §8.6's contingency
  config override is unnecessary. But at an untrusted path hooks do not fire **and do not
  say so**: a protected-path edit succeeded, exit 0, in silence.
  `--dangerously-bypass-hook-trust` is mandatory, not prudent.
- **P0-12** — cache fresh, every §4.5 routing target still exists with the efforts claimed.

## Blocking for Phase 2 — one item

**`implement` and `improve-codebase-architecture` are installed but unreachable.** Codex
treats `policy: allow_implicit_invocation: false` as *"remove from the catalog"*, not
*"model may not invoke it"*. No prompt reaches them — not by name, not as `/implement`.
§24.11's claim that "the prompt is the user turn" is refuted by measurement.

Cheap fix, no new mechanism: the prompt builder **inlines `SKILL.md`** (433 bytes) and
records its sha256 in the attempt directory. Details and evidence in `p0-15-skills.md`.

## Phase 1 runs against BAC-4

`BAC-4 — Application skeleton: Settings, structured logging, app factory` was re-opened to
`Todo` and now **passes all nine §7.1 conditions**. It was chosen over the other six
candidates on three grounds:

- Its own description says **"Blocked by: None — can start immediately"**, and that it runs
  in parallel with BAC-3 because they touch different files.
- It needs **no Docker, no database and no network**. The acceptance criteria specify
  testing the health endpoint through `httpx.AsyncClient` against the app object with no
  server started, so the five standard gates cover it exactly and the `integration` gate —
  which needs Postgres — never has to run.
- It is real implementation code with ten concrete, checkable acceptance criteria, rather
  than a decision reserved for a human.

**BAC-3 was rejected deliberately.** It is a dependency-approval ticket: it edits
`pyproject.toml`, runs `uv lock`, and touches `uv.lock` — a **protected path** in
`harness.config.json`. Approving dependencies is James's call, not the factory's, and a
first run that immediately collides with `protect_paths` proves nothing useful. BAC-5
through BAC-9 are all blocked by BAC-3.

### ⚠️ One wrinkle Phase 1 must handle

`origin/feat/BAC-4-application-skeleton` **already exists** and carries a complete
implementation — commit `5e7afb8`, 15 files, 598 insertions, dated 2026-08-07. It was never
merged into `v2`, and BAC-4 is the only BAC ticket with a stale branch.

Two consequences:

1. **Branch-name collision.** Phase 1 creates `feat/BAC-4-<slug>`; §11.2's `git worktree
   add -b` will fail or silently reuse it depending on the slug. Either delete the stale
   branch (`git push origin --delete feat/BAC-4-application-skeleton`) or make Phase 1's
   branch creation refuse to reuse an existing remote branch — which it should do anyway.
2. **It is a free oracle.** The abandoned branch is an independent implementation of the
   same ticket by the same author. Diffing the factory's output against `5e7afb8` is the
   cheapest possible check on whether the first run produced sane work.

The proposed tenth intake condition (`git log --grep` on the base ref) does **not** catch
this, because the commit is not on `v2`. The condition should search remote branches too.

## Nothing is outstanding

Both remaining items were completed by James on 2026-08-20 and **verified by measurement,
not by inspection**:

- **Codex hook trust in `frontend-harness` is live.** `~/.codex/config.toml` now carries
  four `[hooks.state]` keys for `/Users/james/frontend-harness/.codex/hooks.json`
  (`pre_tool_use`, `post_tool_use`, `session_end`, `stop`) and a `[projects]` entry for the
  repo. That is one *more* than the stale set it replaced, which had no `session_end`.
  Proven by canary: `codex exec` asked to append a line to `pnpm-lock.yaml` was **blocked**
  by `protect_paths.mjs` with the config's own reason — *"regenerate with `pnpm install`,
  never hand-edit"*. The repo was clean afterwards and no new `[projects]` stanza appeared.
- **The rename leftovers are gone.** Zero `frontend-development-harness` references remain
  in `~/.codex/config.toml`, the `p0-7-scratch` stanza is removed, the file still parses,
  and `sbx ls` no longer lists `codex-frontend-development-harness`.

**Phase 0 is closed.** Every step is answered, every human item is done, and the machine
state is what this document says it is.


## Changes Phase 0 measured into the plan

Each of these is a correction to a written decision, not a new idea.

| § | Change | Evidence |
| --- | --- | --- |
| §24.11 | `/implement` is unreachable from `codex exec`; inline `SKILL.md` instead | `p0-15-skills.md` |
| §8.4 / §8.5 | A bind-mounted workspace cannot be shared with the host. `UV_PROJECT_ENVIRONMENT` for Python; **`--clone` is mandatory** for the frontend | `p0-3-toolchain.md`, `p0-10-gate-timing.md` |
| §8.6 | Drop the project-trust override — an untrusted project is not refused | `p0-6-codex-trust.md` |
| §18.5 | The live context percentage must come from `models.toml`, not the event stream | `codex-events.md` |
| §15.3 | Import errors must be split from assertion failures, or the replay rounds itself to green | `p0-14-red-phase.md` |
| §14.2 | `behaviour_changed` must be **required**; a missing value blocks | `p0-14-red-phase.md` |
| §7.1 | Add a tenth condition: the identifier must not already appear in the base ref's log **or on a remote branch**. Condition 8's `gh pr list --search` is fuzzy and will false-positive | `p0-11-intake-dry-run.md` |
| §4.5 | Eight models, not seven (`gpt-reserve`); efforts live under `supported_reasoning_levels`; use `context_window`, not `max_context_window` | `p0-12-models.md` |
| §8.3 | The image tag is `codex-docker`, not `codex`; `codex-pnpm:v1` already exists | `p0-3-toolchain.md` |
| §8.7 | Filesystem policy is `**` and cannot be narrowed from the CLI — mounts are the only control | `p0-4-egress.md` |
| Phase 2 pre | The in-image Codex is **0.146.0**; the event shape was measured on 0.147.0. Re-verify or pin | `p0-3-toolchain.md` |
| — | `codex exec` hangs for ever unless stdin is closed | `codex-events.md` |

## Machine state left behind

| Created | Removed | Kept, deliberately |
| --- | --- | --- |
| `factory-probe-py`, `factory-probe-fe` sandboxes | ✅ both | — |
| worktree `python-harness/.factory/worktrees/p0-6-probe` | ✅ removed + pruned | — |
| `python-harness/.venv` clobbered by a sandbox `uv sync` | ✅ repaired on the host | — |
| `~/.agents/skills/` (8 symlinks) | — | Phase 2 needs it (§19 authorises it) |
| `~/.codex/skills/` (8 symlinks beside `.system`) | — | §24.11 step 2 |
| sbx skills store (8 skills) | — | §24.11 step 3 |
| `[projects]` stanza for a scratch dir in `~/.codex/config.toml` | — | for James; agents do not edit that file |

Both consumer repos are clean and on `v2` at their original heads. `~/.factory/` was **not**
created and must never be — `sbx skills import` scans `~/.factory/skills`, which belongs to
Factory.ai's Droid.

## Stop here

Phase 0's boundary is Phase 0. Phase 1 creates the `jchen1707/factory` repository and its
first code, and it now has a settled target: **BAC-4**. Because that is a Python ticket, the
frontend `--clone` work stays in Phase 2 where the plan put it.
