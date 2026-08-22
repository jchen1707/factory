# Phase 3 handoff — 2026-08-22

Written at the end of the session that validated the python half end to end. Read
`.agents/plans/wild-bubbling-quill.md` first; it is the Phase 3 plan and still accurate
except where this file corrects it. `SOFTWARE-FACTORY-PLAN.md` remains the specification.

## Where Phase 3 stands

**The python path is validated end to end.** `factory run BAC-4` (run `4ab365b526264045`)
drove `approved → awaiting_human` in 17 minutes and opened draft PR
[python-harness#66](https://github.com/jchen1707/python-harness/pull/66):

```
22:06:13  approved -> claimed              22:22:09  verifying -> reviewing
22:06:16  worktree_ready -> implementing   22:23:37  reviewing -> pr_ready
22:22:07  implementing -> verifying        22:23:40  pr_ready -> awaiting_human [delivered]
```

The PR body carries everything §13.2 asks for: `Fixes BAC-4`, the restatement, the full
gate table including the `not_applicable` row and its caveat, the Tier-2 skip rule by
name, a Tier-1 finding, the red-phase result, out-of-scope, artifact path, cost, and the
"merge is a human's" footer. The effects ledger shows exactly one Linear write per state,
all four confirmed.

**What is left.**

| | state |
| --- | --- |
| python path, `approved` → draft PR | validated on a real ticket |
| Tier-2 fan-out **completing** | never succeeded — see below |
| `--clone` | fully calibrated, not built |
| `FRO-<n>` validation | blocked on `--clone` |

Tier 2 has fired its trigger correctly in both directions (ran on run 5's 16-file diff,
skipped as `no-trigger` on run 6's smaller one) but has never returned findings: run 5
died on the output-path bug that #13 fixed, and run 6 skipped it. **The first Tier-2 run
after #13 is unproven code.** Do not report Phase 3 complete on a run where Tier 2 was
skipped.

## Open, waiting on James

- **factory#14** — the preflight env-channel check. Open, green, unmerged.
- **python-harness#66** — BAC-4's own draft PR. This is the deliverable; it needs a human
  review and merge, and the run sits at `awaiting_human` until then.

Everything else from this session is merged: factory #10/#11/#12/#13, harness#15 (strict
findings schema, 0.7.1), python-harness#65 and frontend-harness#38 (vendor sync to
`harness@00d059b`).

## `--clone` — measured, not designed

The plan's §19-Phase-7 design assumed the clone breaks §4.2's filesystem protocol. **It
does not.** Calibration on 2026-08-22 against `sbx` v0.38.0, `frontend-harness`:

| probe | result |
| --- | --- |
| clone location inside the VM | `/Users/james/frontend-harness` — the *identical host path*, and writable |
| isolation | a file written there never appeared on the host |
| extra `rw` workspace | mounted at its identical path; VM writes are visible on the host immediately |
| `sandbox-<name>` remote on the host | appears as `git://127.0.0.1:49153/frontend-harness` — a plain git daemon, **not** the `ext::sbx exec` transport the plan sketched |
| VM commit → host fetch | `git fetch sandbox-<name> <branch>` lands it; also creates `refs/sandboxes/<name>/<branch>` |
| `origin/<base>` inside the clone | resolves — remote-tracking refs come with the clone, so the branch can be cut with no network and no credential |
| untracked/ignored content | **absent** — no `.factory/`, no `node_modules`. The clone carries tracked content only |
| `git push` from the VM | fails: `could not read Username`. §13.2 holds by itself |

So the build is smaller than planned:

1. `steps/sandbox.py:build_spec` — delete the `clone-not-implemented` raise; set
   `spec.clone = ctx.project.requires_clone`; add a **project-stable** `rw` workspace for
   attempt directories. It must be project-stable, not per-run: §9.1 fixes the workspace
   set at creation and the sandbox is named once per project. That mistake already cost a
   run (see `_review_scratch` in `steps/review.py` for the shape that works).
2. `steps/worktree.py` — for clone projects there is no host worktree. Create the branch
   inside the VM: `sbx exec <sandbox> git -C <project.path> checkout -b <branch>
   origin/<base>`. Record `branch`/`base_ref` on the run as today; `worktree` should point
   at the in-VM path, which is the same string.
3. `steps/implement.py` — the attempt dir and the seeded `.factory/context/` move onto the
   `rw` mount, because the clone does not carry untracked files. The host polls it exactly
   as today; path identity still holds.
4. `steps/deliver.py` — before the host-exec guard, fetch the agent's branch back:
   `git -C <project.path> fetch sandbox-<name> <branch>`, then push *that* from the host.
   The host keeps push authority.
5. redphase and review need the branch on the host, so the fetch-back has to happen
   **after verify and before redphase**, not at deliver as the plan says. Everything from
   `reviewing` onward then works unchanged against a host-side branch.
6. `FakeSandbox` needs a clone mode: no host worktree, attempt dir on the mount, and a
   fetch-back that the fake can satisfy.

## Boundary finding: a token the preflight could not see

Every factory sandbox — build, reviewer, and a fresh clone sandbox — carries a `GH_TOKEN`
env var holding a 40-character `gho_` value, while `sbx inspect` reports only
`mcpgateway`. `preflight:no-secrets-in-vm` reads `inspect`, so it passed all three.

Bounded by two measurements: `gh auth status` inside the sandbox says the token is
**invalid**, and the VM cannot push anyway (no credential helper). `sbx create` has no
flag to suppress it and `sbx secret ls` scopes it to no factory sandbox, so **there is
nothing to remove from this side — the fix is sbx's**, and asking them why a sandbox with
no github secret in scope receives one is still an open action for James.

factory#14 adds `no-capability-env`, which reads the other channel using the repository's
own `hooks.secretVars`. The name is acknowledged per project in `config/projects.toml`
(recorded as a `warn` every run); an unacknowledged name blocks. The plan's §8.7 claim
that an in-VM env scan "finds nothing and looks green" is measured false.

## How to work on this

**Run in the background, always.** `nohup uv run factory run <TICKET> > logs/<ts>.log 2>&1
< /dev/null & disown`. A foreground run is orphan-prone.

**Tag before cancelling.** `factory cancel` deletes the run's unpushed branch. Every
attempt from this session is preserved:

```
archive/BAC-4-6e2681471d4c485c   archive/BAC-4-1effc543d83a459a
archive/BAC-4-73f500d22e894d9a   archive/BAC-4-b1a82af148b441e5
archive/BAC-4-2efa19065ce6476e
```

**There is no resume.** A blocked run cannot go back to `reviewing` — `machine.py:104`
makes `BLOCKED → REVIEWING` illegal and unblocking is human-only. Either drive the steps
directly against the existing run row (there is a scratch script pattern for this: rebuild
the `Context` the way `cmd_run` does, then call the steps), or `cancel` and re-run, which
costs a fresh implement.

**Probe before you spend.** Five of tonight's six runs died on factory plumbing, each
after a ~20-minute implement. Every defect after the second was findable in about a
minute with a direct probe: `sbx create` with the real spec, one `codex exec` with the
real schema, one `sbx exec` write test. The calibration above cost no model credits at
all. Codex credit ran out once tonight; the builder is now `gpt-5.6-sol` at `high` (down
from `xhigh`) and the reviewer is `gpt-5.6-terra` at `high`.

**Fakes should model the constraint that broke.** `FakeSandbox` now writes an axis's `-o`
file only when that path is inside a writable workspace, because that constraint broke
twice. Do the same for the clone path rather than letting a real run find it.

## Defects found and fixed tonight, in order

Each is a merged commit with a test; listed because the pattern is the useful part.

1. `verify.py` discarded the gate report's raw stdout on a parse failure — the one failure
   mode that destroyed its own evidence. (#10)
2. `repo._git` stripped the trailing newline off every patch, so the red-phase replay's
   `git apply` could never succeed: `corrupt patch at line <last>`. (#10)
3. A `GitError` out of a step escaped to `main`'s catch-all, leaving the run row mid-flight
   with an empty `blocked_reason` and the tracker untold. (#10)
4. `sbx create` refuses a `:ro` **primary** workspace; the reviewer sandbox was specced
   that way and could never be created. (#11)
5. `codex exec review --base` cannot take a prompt, and the prompt is the axis. (#11)
6. Nothing in the reviewer's sandbox spec may carry a run id or a ticket — §9.1 fixes the
   workspace set at creation. (#11)
7. The vendored `review-findings.schema.json` was not a strict structured-output schema,
   so the reviewer's turn failed before reading the diff. Layer A; fixed in harness#15.
8. Tier 2 kept its own copy of the argv and wrote to a path the sandbox cannot reach. (#13)
