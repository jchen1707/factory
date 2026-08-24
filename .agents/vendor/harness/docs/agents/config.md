# `harness.config.json` — what layer A asks of a repository

Layer A is authored once and is byte-identical everywhere it lands. That is only possible if
nothing in it names a toolchain, a directory, a team key or a command. Everything it needs to
know about the repository it is running in comes from one file at that repository's root.

`plugins/harness/schema/harness.config.schema.json` is the contract. This file is why it has
the shape it does.

## The rule

> **If a shared file would have to state a fact true in only one stack, that fact becomes a
> config key instead.**

It is the layer rule from `AGENTS.md` applied to executable content. Prose could split — the
doctrine here, the fact there, with a pointer between them. A command cannot: `/lint` has to
actually run something. So the shared half runs _what the config names_, and the naming is the
stack's own.

## Why the gates are one list

`gates` is the Definition of Done written as commands, and it is deliberately the only place
that list exists. Four things read it:

- `/lint` runs the `lint`, `format` and `types` gates;
- `/test` runs the `test` gates, and the `e2e` and `integration` gates on request;
- the verify hook runs them as the Stop gate;
- the meta-repo's cross-stack CI job runs them against a freshly synced layer A.

Before this file, each of those had its own copy of the same four commands, in a different
notation, in a different repository. A gate added to CI and forgotten in the command is the
ordinary failure — and it fails in the direction that looks green.

`kind` is what lets one list serve all four. Without it `/lint` would either run the test
suite or need a second list, which is the copy again.

## Why commands are argv, never strings

A gate is `["uv", "run", "ruff", "check", "."]`, not `"uv run ruff check ."`.

A string has to be parsed by something before it can be run, and every reader would have to
agree on quoting, on globbing, and on what happens to a path with a space in it. Two of the
four readers are JavaScript and two are Python; the odds of them agreeing are poor and the
disagreement would be silent. argv has one meaning.

## Why the hooks read this file too

`hooks` is the second half of the same idea, and it arrived a phase later on purpose. While
the guards were four implementations across two stacks, the right shape for their config was
not knowable — and declaring keys nothing read would have been a claim the repository could
not check. They are one implementation now, so the keys are real.

Four hooks read four groups:

- `gatedPaths` / `gatedFiles` / `gatedExtensions` — what the Stop gate watches. This is a
  filter, not a list of what matters: a turn that touched only prose ends freely, because
  gating prose burns the 8-consecutive-block override budget on writing work. Include this
  repo's own harness wiring, or a broken edit to the thing that enforces the gates is the one
  change the walk-away gate can never catch.
- `protected` / `allowed` — what an agent must not touch, and the exception. `scope` is the
  interesting field: `write` refuses writes and leaves reads alone, because a write to a
  generated file is a mistake the author can undo. `secret` refuses both, because a read puts
  the value in the context window, the transcript on disk and the API request in one step and
  only rotation undoes that.
- `secretVars` — variable names whose appearance in a shell command is enough to refuse it.
  The environment dumps and the inline interpreters need no config; a single-variable
  expansion can only be recognised from the name.
- `formatters` — what to run after an edit, per extension, in order.

**The `.env` rules are not in your config, and cannot be removed from it.** `protect_paths`
carries them as a built-in floor. A guard whose config goes missing and quietly protects
nothing is worse than no guard, because the repository still reads as protected.

**A `why` is the whole message an agent receives.** `"regenerate with \`uv lock\`, never
hand-edit"`ends the attempt;`"protected"` invites a retry with a different tool.

## One config per app, and what the root one is for

A monorepo is where this file stops being one file. `apps/api` and `apps/web` have different
gates, different formatters and different lockfiles, and nothing sensible can be said about
both at once — so each declares its own, and the root config declares only `apps`, naming
them.

That is what makes the gates dispatch. A turn that touched `apps/web/src` runs the web gates,
in `apps/web`, and does not run pytest. It is the monorepo's one real cost — the note's §11
called it out as the mechanism the whole option depends on — and it is paid here rather than
in a CI pipeline, because the Stop gate has to make the same decision the pipeline does and
would otherwise make it differently.

Three consequences worth knowing before you write one:

- **Nothing new says which paths belong to which app.** An app's own `gatedPaths` already
  does. A path that belongs to neither — `packages/contracts`, the schema both sides
  generate from — is named by _both_ apps' `gatedPaths`, which is how a contract change comes
  to run both suites. A key mapping paths to apps would be a second authoring of the same
  fact.
- **A `gatedPath` may point outside its app; a `protected` glob may not.** The first is a git
  pathspec, so `../../packages/contracts` means what it says. The second is matched against
  the path relative to the config that declared it, so a `../` glob can never match anything
  — a shared file that must not be hand-edited is protected by the **root** config, which is
  the one whose scope actually contains it.
- **The guards read every config, the gates read one.** A gate answers "is this app green",
  which is a question about one app. A guard answers "may I touch this file", and an app
  declaring rules of its own must not cancel the root's — so `protected`, `allowed` and
  `secretVars` accumulate from the root down, with each app's globs read relative to that
  app. `uv.lock` in `apps/api/harness.config.json` means `apps/api/uv.lock`.
- **An app named in `apps` with no config of its own is an error, not an empty set.** The
  config search walks upward, so that app would resolve to the root config and run the whole
  repo's gates from the wrong directory — green, and measuring the wrong thing. The Stop gate
  names it instead.

The root config still carries what is true of the whole tree: the tracker key, the review
settings, and the protected paths that are nobody's app in particular.

## Adding a key

The test is the rule at the top. A key earns its place when a _shared_ file needs it. A fact
only this repository's own code reads is not config — it belongs wherever that code is, and
putting it here just moves it further from its reader.

## The vault variable has two scopes, one per layer

The Obsidian vault path travels under two names, and neither is wrong — they live in two
different scopes, and the harness is the translator between them.

- **`OBSIDIAN_VAULT_DIR`** — the **host shell** variable, owned by `dotfiles`. The factory
  reads the host value from it (falling back to `OBSIDIAN_VAULT_DIRECTORY`, then to a path
  declared in the project registry), and if none resolves it blocks rather than proceeding
  with the second brain silently unmounted.
- **`OBSIDIAN_VAULT_DIRECTORY`** — the **in-sandbox** variable, owned by the harness. It is
  the name `session_learnings.mjs`, `vault_index.mjs` and the `search-second-brain` skill
  read, and it is the only name the factory emits into a sandbox. A repo's own `AGENTS.md`
  that documents the variable for its agents names this one, because the agent runs inside
  the sandbox where this is the canonical name.

One translator, one canonical name per scope: the host keeps its name, the sandbox keeps
its name, and no layer redefines the other's.
