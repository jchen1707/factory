# Factory eligibility — what a repo declares to be driven end-to-end

A repository can be a perfectly good harness repo — gates dispatch, the Stop gate enforces,
`/code-review` runs — without the factory ever touching it. This file is the difference
between that and a repo the control plane drives from a Linear ticket to a merged pull
request with no human at the keyboard.

This is shared harness doctrine. It describes what a repo _declares_ to be eligible; the
resolution itself runs in the factory (layer D), against its project registry. Nothing here
is a gate command or a review prompt — the factory holds none of those — and nothing here is
the factory's own code. It is the layer-A half of the same boundary `config.md` draws: that
file is what layer A asks of a repository, this one is what layer A asks of a repository that
wants to be _driven_.

## The rule

> **A repo is factory-eligible when the factory's project registry has a row whose `team`
> matches the Linear ticket's prefix, and that repo's `harness.config.json` carries a
> `tracker.team` equal to it.**

Resolution is by prefix alone. A ticket `BAC-412` splits at the hyphen into team `BAC`, and
the factory matches it against every project's `team` in its registry:

- **zero matches** — the ticket is not factory-eligible. It is blocked once and logged; no
  row is invented. A row whose key matches nothing parses, runs and reads exactly like a
  working row — inert configuration that looks live, which is worse than no row because it
  silences the "not eligible" signal that would otherwise fire.
- **two matches** — the daemon refuses to start. Two projects claiming one team is a
  `RegistryError`, caught at load time so it never reaches a run.

After the prefix resolves, `harness.config.json` must exist at the project root and its
`tracker.team` must equal the ticket's team key, or the run blocks at intake. See `config.md`
for the file and `issue-tracker.md` for the team key.

## Two kinds of eligible

A repo can be one without the other, and confusing them is the expensive mistake:

- **harness-eligible** — the repo carries `harness.config.json`, gates, an `AGENTS.md`, and
  the vendored layer-A tree. The Stop gate enforces, `/code-review` runs, dispatch works. Any
  repo scaffolded `--agnostic` is this.
- **factory-eligible** — the repo has a registry row and a `tracker.team`, so the control
  plane can drive a ticket through it unattended.

A monorepo scaffolded `--agnostic` is harness-eligible and **not** factory-eligible: its
gates dispatch and a CSS-only commit runs the web gates and skips the api gates — measured —
but it has no registry row and no `tracker.team`, because no Linear team prefix exists for a
product repo on a workspace tier that already has a harness repo per team. It is the proof
that the two are separable, and that a repo can be a fully working harness repo the factory
never drives.

## What a monorepo product repo looks like

A product repo scaffolded `--agnostic` as a monorepo declares `apps` at the root and **no
gates of its own**. Each app carries its own `harness.config.json` with its own gates; the
root config carries only `apps`, the hooks, and the review settings. There is no
`tracker.team` at the root, and no toolchain cross-check — the factory's stack check skips a
monorepo root, because the root runs no gates to cross-check.

Dispatch is layer A's, not the factory's: a change under `apps/web` runs the web gates in
`apps/web` and skips pytest, because `dispatch()` resolves gates per app by changed path. The
factory never re-states that; it reads the gate report layer A produces. See `config.md`,
"One config per app", for the dispatch mechanism and its one real cost.

## What is not built

A product repo with no Linear team prefix cannot be driven by the factory today. The
registry resolves by prefix, and a product repo on a team that already has a harness repo
would collide: two projects, one team, `RegistryError`. The alternative — resolve a project
by a label or a Linear project _within_ a team, so a ticket on an existing team resolves to
the product repo instead of the harness — is **not built**. It is a known gap, named here so
a repo that needs it is not told to configure a row that would make the daemon refuse to
start.

If the factory is ever to drive a layer-C product on this tier, the change is to the
registry's resolution, not to the repo: a row with an invented key is inert, and adding one
is the failure this section exists to prevent.
