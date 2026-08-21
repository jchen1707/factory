# P0-14 — red-phase baseline

Seven replays of §15.3 against already-merged commits, three in `python-harness` and four in
`frontend-harness`. Harness: `scripts/redphase_replay.py` (a measurement script, not factory
code). For each commit it worktrees the parent, applies **only** the test-file half of the
diff, and runs the repo's own `kind: test` gate.

## Results

| Repo | Commit | Subject | Outcome |
| --- | --- | --- | --- |
| python | `b40b474` | fix: stop the generator copying a submodule as a file | **RED-REAL** |
| python | `e811653` | fix: persist sandbox MCP configuration | **RED-REAL** |
| python | `867d5ad` | fix: use the Obsidian vault directory | **RED-REAL** |
| frontend | `0a87cd2` | Restructure to feature-sliced (fractal) architecture | **RED-REAL** |
| frontend | `3122162` | fix: give Codex the SessionEnd hook | **RED-REAL** |
| frontend | `0d08a3d` | feat: FRO-5 projects list at /projects | **INCONCLUSIVE** |
| frontend | `2ee6d34` | test: fail when layer A gains a skill with no stub | **PASSES** |

Excluding `2ee6d34` — see below — the baseline is **6 replays, 5 red, 1 inconclusive**:

```
python-harness   0 / 3  inconclusive     0 %
frontend-harness 1 / 3  inconclusive    33 %
overall          1 / 6                  17 %
```

**Recommended `redphase.inconclusive_alarm_pct`: 40**, per repo. It sits above the measured
frontend rate with headroom, and any drift toward "inconclusive is normal" trips it. Three
commits per repo is a small sample and the value should be revisited once the factory has
run a dozen real tickets; the alarm is a smoke detector, not a threshold with a claim behind
it.

## Two findings that change the check itself

### 1. "The failure names a test the diff touched" is not enough

The first classifier used exactly that rule and scored **7/7 red** — including
`0d08a3d`, whose gate failed with:

```
Failed to resolve import … ❯ loadAndTransform … vite/dist/node/chunks/dep-…
```

That is an **import error**, not a red test. It names the test file because the test file is
what failed to load. §15.3's table already puts import and collection errors in the
inconclusive row; a naive "did it name the test" check silently promotes them to red, which
is the exact failure mode the whole replay exists to prevent — a check that did not check,
rounded to green.

The classifier now separates them: an import/collection signature
(`Failed to resolve import`, `Cannot find module`, `ModuleNotFoundError`, `ERROR collecting`,
`No test suite found`, `SyntaxError`, …) with **no** assertion signature is inconclusive;
an assertion failure naming a changed test is red. `steps/verify.py` must implement this
distinction, and it should be a unit test with both tails as fixtures.

The pattern is stack-shaped, not random: in TypeScript a new feature's tests import modules
that do not exist at the base ref, so **new-file features on the frontend path will read
inconclusive by default**. Python fared better because the tests import a package that
already exists and assert on new behaviour inside it. This is worth stating in §15.3 so the
frontend rate is not read as a quality signal about the frontend.

### 2. `behaviour_changed = true` is doing real work as a guard

`2ee6d34` — *"test: fail when layer A gains a skill with no discoverable stub"* — is a
**test-only** commit adding a guard. Replayed, it is green at its base (62 tests pass),
which the table classifies as `test-proves-nothing`: **always blocks, not configurable.**

Blocking it would be wrong. It is a legitimate, valuable commit. The guard that saves it is
§15.3's own precondition — the replay runs only when the agent reports
`behaviour_changed = true`, and a test-only hardening commit does not change behaviour.

So the precondition is load-bearing, not a formality, and two things follow:

- `implement_result.schema.json` must make `behaviour_changed` **required**, not optional,
  and the factory must treat a missing value as `blocked`, never as `false` — defaulting to
  `false` lets any agent skip the replay by omitting a field.
- A commit that adds tests and changes no source is a shape the factory will meet. Worth an
  explicit row in §15.3: tests-only diff + `behaviour_changed = false` → skip the replay,
  record why.

## Reproducing

```sh
python3 docs/discovery/scripts/redphase_replay.py \
  --repo /Users/james/python-harness --pathspec 'tests/' -- b40b474 e811653 867d5ad

python3 docs/discovery/scripts/redphase_replay.py \
  --repo /Users/james/frontend-harness --link node_modules \
  --pathspec ':(glob)src/**/*.test.ts' ':(glob)src/**/*.test.tsx' \
             ':(glob)tests/**' ':(glob)**/*.test.mjs' \
  -- 0d08a3d 0a87cd2 2ee6d34 3122162
```

The `--pathspec` values above are what the `tests` key added to
`harness.config.schema.json` in Phase 2 (§12.1) must hold. Note the frontend needs
`:(glob)` magic for `**` to cross directory boundaries, and that `--link node_modules`
stands in for an install the factory would do properly inside the sandbox.
