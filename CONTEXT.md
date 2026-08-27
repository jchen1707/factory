# Domain language

The words this repository uses, and what each one is allowed to mean. `AGENTS.md` is the
rulebook and `SOFTWARE-FACTORY-PLAN.md` is the specification; this file is neither. It
exists so that two functions reading the same input cannot disagree about what it is
called, which is the shape of defect that has cost this project the most.

Architecture vocabulary — **module**, **interface**, **depth**, **seam**, **adapter**,
**leverage**, **locality** — is separate and lives in the `codebase-design` skill. Use
those words for how the code is shaped and these words for what it is about.

## The unit of work

**Ticket** — a Linear issue. The factory reads tickets and never creates one.

**Run** — one attempt to take one ticket from `approved` to a pull request. A row in
`runs`, identified by a run id, holding the state, the attempt counter, the worktree and
the branch. A ticket may have several runs over its life; only one is live at a time.

**State** — where a run is, from §5.1's list. Stored as the string value of `State`.

**Transition** — one legal hop between two states. Legality is decided by `TRANSITIONS`
in `machine.py` and nowhere else; `HUMAN_ONLY` reserves some hops for James. Every hop
performed is recorded in `transitions`, with the rule that allowed or reserved it.

**Step** — the entry action of one state, under `steps/`. Idempotent by construction:
re-entering a state a step has already performed costs a database read.

**Entry action** — the mapping from a state to the step that acts on it. The order of the
pipeline *is* this mapping; it is not a second list written beside the transition table.

**Driver** — the module that decides which step runs next and what happens when one
stops. It holds ordering and no domain judgement: what a step decides belongs to the step.

**Attempt** — one spawned execution of a step within a run, numbered. An attempt has a
directory on disk, a row in `attempts`, and a terminal record — an exit code — unless it
was orphaned.

**Rung** and **ladder** — §16.3's recovery schedule. The rung is derived from the attempt
number and decides whether a dead attempt is resumed, restarted, rewound to planning, or
failed. `recovery.decide` is the only authority; two paths must never answer differently.

## Running and stopping

**Tick** — one pass over everything the factory owns: reap what died, recover what can be
recovered, advance what can move, and only then claim new work. Holds nothing in memory
between calls. `factory daemon` is the same pass in a loop; `launchd` is the same pass on
a timer.

**Reap** — look at an attempt nobody was watching and decide what the filesystem says
happened to it. The tick that asks is never the tick that started the run.

**Orphaned** — an attempt that stopped with no terminal record: nothing is running, and no
exit code was ever written. Distinct from **timed out**, which is an attempt that overran
its state's budget, was signalled, and *did* write one.

**Lease** — the right to act on a run, held with a TTL. One writer per run; expiry is the
only way a lease is lost, so a crashed process never wedges a run.

**Effects ledger** — §16.2. Every external write is committed as `intended` before the
call and `confirmed` after, and is reconciled rather than retried on resume. It is what
makes a crash between a Linear write and its record survivable.

**Blocked** — a run that needs a human, carrying a named reason slug, never prose.

**Resumable** — an attempt that died in a way the next tick can pick up. Not a judgement
about the work; the ladder answers it.

## The machinery a run touches

**Project** — one target repository, in the registry, with its team, path, base ref,
stack and sandbox name.

**Sandbox** — the VM a step runs inside, named `factory-build-*` or `factory-review-*`.
Never a `codex-*` one: that is James's live session.

**Worktree** — the git worktree a run's work happens in. **Clone mount** is its
replacement for a `--clone` project, where the repository the agent sees lives inside the
VM and only an additional `rw` mount reaches the host.

**Gate report** — layer A's `gate_report.mjs`, run in the build sandbox, producing
`gates.json`. The factory holds no gate name of its own; it reads them from the target
repository.

**Red-phase replay** — §15.3's check that a test added by the agent actually fails without
the fix. A green suite is not evidence; this is what makes it evidence.

**Tier 1 / Tier 2** — the review fan-out. Tier 2 is the full pass, fired by the §15.2
trigger rules or forced by `--full-review`.
