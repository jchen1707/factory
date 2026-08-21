# P0-15 / P0-16 — the Codex skill install chain

**Blocking for Phase 2, and the answer is: the chain works, but two of the eight skills the
plan depends on cannot be reached at all.**

## The `~/.agents/skills` → `sbx skills import` chain works exactly as §24.11 predicted

`sbx skills import --help` names its sources verbatim, confirming §24.11:

```
~/.agents/skills   ~/.claude/skills   ~/.copilot/skills   ~/.cursor/skills   ~/.factory/skills
```

`~/.codex/skills` is not among them. `~/.agents/skills` and `~/.claude/skills` did not
exist. The execution set was symlinked into `~/.agents/skills` from the pinned version
directory (`…/mattpocock-skills/1.2.3/skills/engineering/<name>`), and:

```
$ sbx skills import --force
Importing skill "code-review" … "tdd"          (8 of them, and only those 8)
Imported 8 skill(s) into
  ~/Library/Application Support/com.docker.sandboxes/sandboxes/agent-skills
```

Inside a sandbox the store appears as a live **virtiofs mount** at
`/home/agent/.agents/skills` — and it appeared in a sandbox created *before* the import,
so the store is mounted, not copied at creation. `sbx skills import` can be re-run without
recreating sandboxes.

**A sandboxed Codex reads it.** Asked to list its available skills, the agent in the sandbox
returned three groups: its own built-ins (`imagegen`, `openai-docs`, `plugin-creator`,
`skill-creator`, `skill-installer`), the repository's layer-A stubs (`arch`, `context`,
`full-review`, `implement-from-plan`, `lint`, `loop-goal`, `new-project`, `plan`,
`prune-rules`, `retro`, `run`, `search-second-brain`, `test`, `verify`), and the imported
mattpocock skills.

## The blocking finding — `implement` is installed and unreachable

Of the eight imported skills, **six** appeared in the catalog. Two did not:

| Skill | `agents/openai.yaml` | In the catalog? |
| --- | --- | --- |
| `tdd`, `code-review`, `codebase-design`, `diagnosing-bugs`, `research`, `resolving-merge-conflicts` | no `policy:` block | ✅ yes |
| **`implement`** | `policy: allow_implicit_invocation: false` | ❌ **no** |
| **`improve-codebase-architecture`** | `policy: allow_implicit_invocation: false` | ❌ **no** |

Both are present on disk in the sandbox (`/home/agent/.agents/skills/implement/SKILL.md`,
verified by `ls`). They are excluded from what the model can invoke.

Two prompts were tried, both failing:

1. Naming it in plain language — *"Use the implement skill"* → *"The `implement` skill is
   not in the available-skills catalog."*
2. The slash form — *"/implement …"* → the model treated `/implement` as literal text and
   went hunting for the file with `rg` and `sed`. **`codex exec` does not expand a leading
   `/skill` in the prompt.**

Replicated on the host binary (0.147.0) as well as in the sandbox (0.146.0): the same six
appear, the same two do not.

### This refutes the load-bearing sentence of §24.11

> *"So the model cannot reach a user-only skill on its own. Only the invoking prompt can —
> and the factory writes the prompt. `/implement` being user-only is therefore not an
> obstacle: in `codex exec` the prompt is the user turn, which is the correct shape for the
> factory to occupy."*

In Codex, `allow_implicit_invocation: false` removes the skill from the catalog **entirely**.
There is no user turn that reaches it. The distinction the sentence rests on — implicit
model invocation versus explicit user invocation — does not exist on this path.

### The fix, and it is cheap

The factory's prompt builder **inlines the skill body**: read
`~/.agents/skills/implement/SKILL.md` (433 bytes — it is a short directive file, not a
manual), strip the frontmatter, and paste it into the prompt it was going to write anyway.
That is what a skill invocation does; doing it in layer D keeps the routing decision where
§4.5 already puts it, and it removes a dependency on Codex's catalog semantics.

Same treatment for `improve-codebase-architecture` if the factory ever needs it. The other
six are invoked normally, by name.

Recommended: `steps/implement.py` reads the file at run time and records its **sha256** in
the attempt directory, so the evidence names the exact skill text that drove the run — which
is strictly better than a skill name that could mean anything.

## P0-16 — the implicit-invocation policy is honoured, proven more strongly than planned

The step as written asks the agent to *"grill me about this design"* and checks that
`grill-with-docs` is not invoked. That test cannot run here, and should not: §24.11
deliberately **omits** `grill-with-docs` from the install set, so its absence would prove
nothing.

The measurement above is the stronger form of the same proof. Two of eight skills carry
`policy: allow_implicit_invocation: false`; those two, and exactly those two, are absent from
the model's catalog while the other six are present. The sidecar policy is enforced by the
Codex binary — in the sandbox and on the host.

**Verdict: P0-16 passes.** The safety half of §24.11's reasoning is confirmed by
measurement. Only its convenience half is refuted.

## `codex plugin marketplace add` — the UNVERIFIED resolves to "works, but do not use it"

Against an isolated `CODEX_HOME`:

```
$ codex plugin marketplace add https://github.com/anthropics/claude-plugins-official
Added marketplace `claude-plugins-official`.
$ codex plugin list                       # 286 plugins, including:
mattpocock-skills@claude-plugins-official   not installed   https://github.com/mattpocock/skills.git, sha 0ab1b63…
$ codex plugin add mattpocock-skills@claude-plugins-official
Added plugin `mattpocock-skills`. Installed at …/plugins/cache/claude-plugins-official/mattpocock-skills/1.2.3
```

It works. **It is still the wrong path**, for three reasons found by running it:

1. **All or nothing.** It installs the entire plugin — all 40-odd skills, including
   `grill-with-docs`, `to-spec`, `to-tickets`, `triage` and `wayfinder`. §24.11's
   least-privilege decision cannot be expressed. The symlink chain installs exactly eight.
2. **It writes to `~/.codex/config.toml`** — `[marketplaces.claude-plugins-official]` and
   `[plugins."mattpocock-skills@claude-plugins-official"]`. The plan's hard rule forbids the
   factory touching that file.
3. **It does not reach the sandbox.** It populates the *host* `CODEX_HOME`. The sandbox has
   its own `~/.codex`, and skills arrive there only through the `sbx skills import` store.

So it solves the host half of a problem whose sandbox half is the one that matters. Keep the
symlink chain as the delivery mechanism. `factory doctor` compares the pinned `1.2.3`
directory against what `~/.claude/plugins` holds, as §24.11 already specifies.

## State left on the machine

| Path | State | Why |
| --- | --- | --- |
| `~/.agents/skills/` | **created**, 8 symlinks into the pinned `1.2.3` cache | §19 authorises it; Phase 2 needs it |
| `~/.codex/skills/` | 8 symlinks added alongside the pre-existing `.system` | §24.11 step 2 — host-side Codex |
| sbx skills store | 8 skills imported | §24.11 step 3 |
| `~/.claude/skills/` | still absent | not needed; `~/.agents/skills` is scanned first |
| `~/.factory/` | **still absent, and must stay that way** | `sbx skills import` scans `~/.factory/skills`, which is Factory.ai's Droid, not this plan's factory |

Host-side Codex now lists the six reachable skills as `mattpocock-skills:<name>`.
Reverting is `rm` on the symlinks plus `sbx reset` (which also clears every sandbox).
