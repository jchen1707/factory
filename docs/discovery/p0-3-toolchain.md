# P0-2 / P0-3 — template inventory and toolchain probe

## P0-2 — `sbx login`

Not needed. `sbx template ls` and `sbx create` both succeeded against the stored
credentials, and `sbx inspect` reports `auth_mode: oauth · openai` with
`daemon_version v0.38.0`, uptime 17 h. **The human step in §19 P0-2 is already satisfied.**

## P0-3 — template inventory

`sbx template ls`:

| Repository | Tag | Image ID | Flavor | Age |
| --- | --- | --- | --- | --- |
| `docker.io/docker/sandbox-templates` | `codex-docker` | `8dab63bd802d` | `codex-docker` | 5 days |
| `docker.io/library/codex-pnpm` | `v1` | `8ab3deaa75f9` | `codex-docker` | 4 days |

**Correction to the plan's wording.** §8.3 calls the stock image `codex`; the real tag is
`codex-docker`, and that is what `sbx create codex` resolves to
(`docker/sandbox-templates:codex-docker`,
digest `sha256:8dab63bd802d33e0ccf518e5afd0445c480f22b7fc63e7fbcd403ef2ea10908c`).
`codex-pnpm:v1` already exists — the frontend workaround from `zshrc` has been baked into a
local template, so `frontend.template` in the §10.1 registry has a real value to point at.

## P0-3 — toolchain probe

Probe sandbox `factory-probe-py` created from the stock image over
`/Users/james/python-harness`, then removed.

```
Ubuntu 26.04 LTS · uid=1000(agent), groups sudo, docker
```

| Tool | Path | Version |
| --- | --- | --- |
| `node` | `/usr/bin/node` | **v22.22.1** |
| `uv` | `/usr/local/bin/uv` | 0.9.26 |
| `python3` | `/usr/bin/python3` | 3.14.4 |
| `git` | `/usr/bin/git` | 2.53.0 |
| `npm` / `npx` / `corepack` | `/usr/bin/…` | npm 9.2.0 |
| `jq`, `gh`, `rg`, `docker` | `/usr/bin/…` | present |
| `codex` | `/usr/local/share/npm-global/bin/codex` | **0.146.0** |
| `pnpm` | — | **MISSING** |
| `python` (unsuffixed), `cargo`, `rustc`, `fd` | — | missing |

### The decision rule in §8.3 resolves to its first branch

`uv` **and** `node ≥ 22` are both present.
→ **No new Python template.** `python.template` in the registry stays `null`.
This is the answer to the highest-value check in the phase: the layer-A `.mjs` hooks
(`protect_paths`, `format_edited`, `verify`, `gate_report`) can spawn, so the enforcement
layer does not silently disappear inside the sandbox. **P0-3 is not blocking.**

### Two follow-ups this raised

1. **`pnpm` is absent from the stock image, `corepack` is present.** Confirms why
   `codex-pnpm:v1` exists. The frontend row of the registry points at `codex-pnpm:v1`;
   a `corepack enable pnpm` kit step is the cheaper alternative if that template drifts.
2. **The in-image `codex` is 0.146.0 while the host is 0.147.0.** Every event-shape fact in
   `codex-events.md` was captured on the host binary. The factory drives Codex *inside* the
   sandbox, so the parser is reading a **different build than the one that was measured**.
   Phase 2 must re-run the `codex-events.md` capture against the in-sandbox binary before
   trusting the schema there, or pin the in-image CLI. Recorded as a Phase 2 precondition.

## Blocking finding — `uv sync` against a bind-mounted workspace destroys the host venv

The §8.3 procedure runs, verbatim:

```sh
sbx exec -w /Users/james/python-harness factory-probe-py sh -lc 'uv --version && uv sync'
```

`sbx create` **bind-mounts the host path at the identical path inside the VM**, so this
`uv sync` wrote a Linux venv straight over the host's `python-harness/.venv`:

```
home = /home/agent/.local/share/uv/python/cpython-3.12.12-linux-aarch64-gnu/bin
```

`.venv/bin/python` became a broken symlink on the host. Repaired by re-running `uv sync` on
the host; `python-harness/.venv` is healthy again (`cpython-3.12-macos-aarch64`, 3.12.14).

**Consequence for the design, and it is not cosmetic:** any factory step that runs an
install or a gate inside the sandbox against a bind-mounted worktree corrupts the host's
environment for that repo — and the reverse, a host gate run after a sandbox run, corrupts
the sandbox's. Silently, and with a confusing failure mode. The factory must pick one of:

- set `UV_PROJECT_ENVIRONMENT` to a VM-local path (e.g. `/home/agent/venvs/<project>`) in
  every sandbox exec — cheapest, one env var in `SandboxSpec.env`; or
- use `sbx create --clone`, which mounts the workspace read-only and gives the agent a
  private in-container clone — stronger, and it also removes the whole class of
  host-filesystem side effects, at the cost of the `sandbox-<name>` git remote dance; or
- never run gates on the host once a sandbox exists for that project.

This belongs in §8.5 and in the `SandboxSpec` of §8.4. Same hazard applies to
`node_modules` on the frontend path.
