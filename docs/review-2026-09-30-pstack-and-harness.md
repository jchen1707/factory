# pstack integration and a harness review, 2026-09-30

An unattended run on 2026-09-30 integrated pstack with Claude Code across `factory`, `harness`
and the three stacks, and reviewed all five repositories against harness and software-factory
practice. This file records what shipped, how the design was chosen, and what is still open.
The decision trail is `docs/audit/2026-09-30-pstack-integration.tsv`.

## What shipped

| Repository         | Pull request                         | Change                                                                                                                                    |
| ------------------ | ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `harness` (v2)     | jchen1707/harness#48                 | Consumer block in the repo and both templates; `plugins/harness/docs/agents/pstack.md`; `CLAUDE.md` importing `AGENTS.md`; plugin 0.14.3. |
| `python-harness`   | jchen1707/python-harness#86          | Consumer block; the main-transform block updated in step.                                                                                 |
| `frontend-harness` | jchen1707/frontend-harness#65        | Consumer block.                                                                                                                           |
| `go-harness`       | jchen1707/go-harness#8               | Consumer block; transform block; `CLAUDE.md` importing `AGENTS.md`.                                                                       |
| `factory`          | the pull request that adds this file | Consumer block; `CLAUDE.md` imports `AGENTS.md`; first CI workflow; this report.                                                          |

The consumer block, identical everywhere:

```json
"enabledPlugins": { "pstack@pstack-claude": true },
"extraKnownMarketplaces": {
  "pstack-claude": { "source": { "source": "github", "repo": "michael-denyer/pstack-claude" } }
}
```

pstack does not enter a factory sandbox. The factory's agents are Codex in `sbx`, and the
vendored layer A tree stays the only carrier of doctrine into an unattended run. The harness
doctrine file gives the reasons and the one-owner-per-job table.

## How the design was chosen

The design ran as a pstack `architect` sketch through an `arena`: three runners (Opus, Fable,
Sonnet) on the same brief, and a Fable cross-judge scoring six criteria.

- **Late entry.** The Sonnet runner finished after the judge had scored, so it was read but
  not scored. It also pinned `pstack@harness`, enabled per user rather than per repository.
- **Convergence, then a measured veto.** Opus and Fable independently chose the same core:
  pin pstack as a `git-subdir` entry in the harness marketplace, enable `pstack@harness`, and
  set `pstack@pstack-claude` to `false`. The judge preferred the Opus package (24 to 21) and
  marked both down for one failure. `claude plugin list` confirmed it on this machine. An
  external plugin source that is enabled only in project settings is never fetched, so that
  block leaves a repository with no pstack until each machine runs
  `claude plugin install pstack@harness`.
- **Base.** The measured-working shape: the upstream plugin id plus its marketplace
  declaration. On a machine that already has pstack it changes nothing and loads no second
  copy. On an empty profile with the folder trusted, it registered and cloned the
  marketplace and cached pstack 0.9.53 with no install command (measured). That an
  authenticated session then lists the `pstack:` skills is documented, not measured, and
  the fallback is one `claude plugin install pstack@pstack-claude`. The pinned shape would
  need that same one-time install on every machine, and until then it switches off the
  copy each machine already has.
- **Grafted.** The one-owner-per-job table, from both candidates, with the Fable package's
  correction that `pstack:tdd` owns TDD. James's user settings turn `mattpocock-skills:tdd`
  off, so the Opus routing would have left no TDD owner. Also the `CLAUDE.md` import finding
  (Opus) and the rule that nothing writes Codex configuration for pstack (both).
- **Rejected.** A `gh pr merge` deny in the stacks, because James asked for agents to merge
  there. `multi_agent = true` in the stacks' `.codex/config.toml`, because the factory's
  sandboxed Codex reads that file. Switching `show-me-your-work` off, because James asks for
  decision logs in his unattended runs. A per-machine staleness hook that reads Claude Code's
  internal plugin records, which is more surface than one pin warrants today.
- **Rerunnable.** The stack edits were made by `docs/audit/2026-09-30-settings_edit.py`, a
  text edit that keeps each stack's main-transform blocks in step with its settings. Rerun it
  with `python3 docs/audit/2026-09-30-settings_edit.py <stack checkout> --spec
docs/audit/2026-09-30-pstack-consumer-spec.json`. A second run changes nothing.
- **Verified.** Every stack's `main` generator still runs, and the generated `main` settings
  enable both `harness@harness` and `pstack@pstack-claude`. The exact new factory settings
  file, placed in a scratch repository on this machine, reports the existing user-scope
  install enabled. That proves no conflict, not a fresh-machine load. A cross-model review
  of the decision trail (Fable) caught that gap, and the empty-profile measurement above
  answers most of it.

## Improvements, ranked

Ranked by value over cost. Evidence was measured this session unless marked otherwise. Vault
paths are relative to `~/Documents/Obsidian Vault`.

1. **`factory` main is red, and nothing noticed.** On a clean origin/main (17705fe), unit
   measured 3 failed and 899 passed. Integration measured 2 failed, 57 passed, 12 skipped and
   554 errors. `config/models.toml` routes `planner` and `builder` to `gpt-6-astra` (also
   `src/factory/execution.py:24-28` and `config/prices.toml`). Neither the committed model list
   nor this machine's Codex catalogue contains that model (`src/factory/routing.py:192`). The
   three unit failures are the shipped-table tests in `tests/unit/test_routing.py`.
   `tests/integration/conftest.py:917` loads the same routing into its shared fixture, and all
   556 integration failures and errors raise that one `RoutingError` (measured). The
   model entered with 25cf383 (PR #101, 2026-09-10). PRs #101 to #103 merged with zero status
   checks, because the repository had no CI. This pull request adds CI. The model choice is
   James's, so the routing is left as it is and jchen1707/factory#104 tracks it. Expect both CI
   jobs to stay red until that is decided. Cost: 10 minutes once a model is chosen.
2. **Claude Code sessions in `factory` never loaded `AGENTS.md`.** `CLAUDE.md` was a Markdown
   link, which Claude Code does not follow; only an `@` import loads a file. The binding rules
   (James merges, never write `~/.codex/config.toml`, never edit `.agents/vendor/`) reached a
   session only when the agent chose to open the file. Fixed here. `go-harness` v2 had no
   `CLAUDE.md` at all, fixed in go-harness#8. `harness` v2 had none either, fixed in harness#48.
3. **A gate report that ran nothing can read `pass`.** `gate_report.mjs --json` returned the
   verdict `pass` while all four gates were `skipped_unchanged`; only `--force` actually ran
   Ruff, formatting, mypy and pytest (`Project Learnings/2026-09-11 nemoclaw-test 01a09125.md`,
   read this session). A ticket also reached Done with no delivered commit
   (`Project Learnings/2026-09-18 nemoclaw-test 01a0abfd.md`, read this session). The vault
   explorer counted about 27 of 98 September retros citing missing acceptance evidence (its
   tally, not rechecked). Change: in layer A `gate_report.mjs`, give a report whose gates were
   all skipped a verdict other than `pass`, so a reader can tell "nothing ran" from "everything
   passed". Check first whether the factory treats that verdict as success on purpose. Cost: 1
   hour.
4. **Installed plugins go stale in silence.** The installed `harness@harness` plugin is 0.1.1
   (`gitCommitSha` 118ba3b, installed 2026-08-19). Layer A is 0.14.2, and the `harness`
   marketplace clone has not refreshed since 2026-08-19 (`~/.claude/plugins/installed_plugins.json`,
   `known_marketplaces.json`). Any session on a stack's generated `main` runs six-week-old
   hooks. Immediate fix: `claude plugin marketplace update harness`. Durable fix: one freshness
   line in `factory doctor` and in layer A's SessionStart output. Cost: 1 hour.
5. **A fresh clone of a stack's `main` cannot resolve `harness@harness`.** No repository
   declares the `harness` marketplace, so the `enabledPlugins` line resolves to nothing until
   someone runs `/plugin marketplace add jchen1707/harness` (the recorded defect 2). Change: add
   `extraKnownMarketplaces.harness` to each stack's main-transform settings block, so only
   `main`, where the plugin is enabled, carries it. Cost: 20 minutes.
6. **No file declares the sandbox skill list.** It is set by a symlink loop in the plan, six
   names hard-coded in `src/factory/steps/implement.py:504-506`, and a version check against
   mattpocock-skills in `doctor.py:587-615`. Change: one config file names each sandbox skill,
   its source plugin and version, and the prompt list and doctor check derive from it. Cost:
   1 to 2 hours.
7. **Session learnings are lost without a trace.** `Project Learnings/_hook.log` records 16
   "claude exited 1" and 13 "transcript unavailable" (counted this session). The factory and python-harness wire
   `codex_session_learnings.mjs --claude` on SessionEnd with a 3-second timeout, while the
   agnostic template gives `session_learnings.mjs` 300 seconds. Unverified: whether the first
   script detaches before its timeout. Check that before changing a number. Cost: 1 hour.
8. **`frontend-harness` carries two differing copies of two skills.** `delivery` and
   `preflight` exist in both `.agents/skills/` and `.claude/skills/` and differ, where
   python-harness symlinks one to the other. Cost: 20 minutes.
9. **Pin pstack in layer A when an install step exists.** The shape is recorded in
   `plugins/harness/docs/agents/pstack.md`. pstack upstream moved from 0.9.45 to 0.9.52 in two
   days, so a floating plugin changes agent behaviour between sessions without a diff. Before
   adopting it, widen `harness` `scripts/check.py:96-97`, which rejects any marketplace entry
   whose `source` is not a string (found by the Sonnet runner, verified).

**Retracted during the run.** An explorer reported that every stack's generated `main` had
been stale since 2026-09-09. It had not. The 2026-09-23 `generate-main` push runs were green,
and `main` did not move because that `v2` change touched only paths the generator drops.
