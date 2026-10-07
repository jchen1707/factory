# pstack — the method layer, and where it stops

pstack is a third-party Claude Code plugin (`michael-denyer/pstack-claude`, a port of poteto's
pstack). Its skills are about how to work: design before code (`architect`, `arena`),
adversarial pressure on a design (`interrogate`), explanation (`how`, `why`), prose (`unslop`,
`technical-writing`), cleanup (`deslop`, `no-comments`) and a decision trail
(`show-me-your-work`). Its SessionStart hook tells the agent to route non-trivial work through
`poteto-mode`.

Layer A is the contract: what done means (the gates and the verify Stop hook), what a review
covers (the frames and each stack's checklists), which paths are protected, and where plans
live. pstack never replaces a contract. It is how an agent works inside one. pstack's own hook
says repository instructions take precedence, and this file is that instruction.

## How a repository enables it

Every harness repository and every template carries the same block in
`.claude/settings.json`, on both branch flavours:

```json
"enabledPlugins": { "pstack@pstack-claude": true },
"extraKnownMarketplaces": {
  "pstack-claude": { "source": { "source": "github", "repo": "michael-denyer/pstack-claude" } }
}
```

- **The plugin id is the upstream one.** A user who already enables `pstack@pstack-claude`
  globally gets the same plugin, not a second copy competing for the `pstack:` namespace.
- **The marketplace line is what a fresh clone lacks.** Measured on 2026-09-30 with an empty
  Claude Code profile and the folder marked trusted: the session registered the
  `pstack-claude` marketplace, cloned it, and placed pstack in the plugin cache with no
  install command. It wrote no install record, so `claude plugin list` still reports "No
  plugins installed". Not measured: that an authenticated session then lists the `pstack:`
  skills, which the Claude Code docs state for a relative-path plugin source like pstack's.
  If a session shows no `pstack:` skills, run `claude plugin install pstack@pstack-claude`
  once. On a machine that already has pstack, the block changes nothing (`claude plugin list`
  reports the existing install, enabled).
- **A fresh machine gets upstream's current pstack.** The empty profile above received 0.9.53
  while an existing machine kept 0.9.45. Nothing pins the version yet; see below.
- **There is no `ref`.** A marketplace is registered once per machine under its name. A
  repository that pins `pstack-claude` to a tag contends with every other repository, and with
  the user's own registration, for the same clone.

## Why layer A does not pin pstack yet

The pinning shape was designed and set aside on a measurement. It is a `pstack` entry in this
repository's marketplace (`git-subdir`, `ref` plus `sha`), enabled as `pstack@harness`, with
`pstack@pstack-claude` set to `false`. An external plugin source that is enabled only in
project settings is never fetched. So until each machine runs
`claude plugin install pstack@harness`, a repository that carries that block loads no pstack at
all (`claude plugin list`, 2026-09-30). Adopt the pin when an install step reaches every
machine that opens these repositories, and not before.

## One owner per job

| Job                         | Owner                                                                       | pstack's part                                                                                                                    |
| --------------------------- | --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Definition of Done          | `harness.config.json` gates, run by the verify Stop hook and `/test`        | "Prove it works" is satisfied by these gates. pstack never substitutes a check of its own.                                       |
| Load this stack's standards | `/arch`                                                                     | `pstack:architect` runs `/arch` as its first grounding step.                                                                     |
| Design before code          | `pstack:architect`, which runs `arena`                                      | Owns it. The chosen Shape goes into the plan file that `/plan` writes.                                                           |
| Plan file and sign-off      | `/plan`, then `/implement-from-plan`                                        | A pstack planning playbook writes into `/plan`'s files. It does not start a second plan format.                                  |
| Review of a diff            | `full-review`: the frames plus this stack's checklists                      | `pstack:interrogate` challenges a design or a decision. It never stands in for `full-review`.                                    |
| Test-first development      | `pstack:tdd`                                                                | Owns it. `/test` runs the gates; it is not a workflow.                                                                           |
| Durable lessons             | `/retro` and the session-learnings hooks                                    | `pstack:reflect` edits skills. Run it in `harness`, where layer A is authored. In a consumer, its edits land in generated files. |
| Decision trail              | `pstack:show-me-your-work`                                                  | Owns it. Keep the log local by default. Commit it under `docs/` when a reviewer needs it, never under `.agents/vendor/`.         |
| Explanation, prose, cleanup | `pstack:how`, `why`, `unslop`, `technical-writing`, `deslop`, `no-comments` | Owns it. No layer A counterpart exists.                                                                                          |
| Merge                       | James                                                                       | pstack's Babysit and Shipping playbooks run only when James asks for them in the session.                                        |

## Where pstack does not run

- **Factory runs.** The factory's agents are Codex in `sbx`, driven by a step prompt and an
  output schema. The vendored layer A tree is the only carrier of doctrine into that sandbox.
  pstack's playbooks fan out subagents and open and land pull requests, which contradicts the
  control plane's rule that it owns every tracker and GitHub write. Nothing installs pstack in
  a sandbox. If the builder should follow a pstack idea, adopt that idea into this directory,
  where the vendored tree carries it.
- **Codex on the host.** pstack ships a Codex plugin. Installing it is each user's choice,
  made with `codex plugin add`. No repository writes `~/.codex/config.toml` to do it.
- **`claude --bare`.** It skips plugins, hooks and `CLAUDE.md` together. Nothing here survives
  it.

pstack reads its per-role models from `~/.claude/pstack-models.md`. That file is per user, and
no repository sets it.
