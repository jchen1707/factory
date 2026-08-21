# P1-1 — the in-image Codex, and the sandbox name that attracts a secret

Measured 2026-08-21, from inside `factory-build-python-harness` created minutes earlier by
`factory run BAC-4`. Both were open hazards at the time; the first is closed by
measurement below and the second by a rename. `p1-3-phase-1-validated.md` is the
validating run.

## 1. The in-image binary is 0.146.0, not the 0.147.0 the parser was pinned to

```console
$ sbx exec factory-build-python-harness /bin/sh -lc 'codex --version'
codex-cli 0.146.0
```

The host runs `codex-cli 0.147.0` and `docs/discovery/codex-events.md` — the fixture
`agent/codex.py::parse_events` is written against — was captured there. `p0-3-toolchain.md`
predicted this from the image tag; it is now confirmed by running the binary.

**Resolved by measurement, 2026-08-21.** The first run to reach `verifying` parsed
0.146.0's output correctly with the parser pinned to 0.147.0's fixture. From
`state/factory.db` for that attempt:

| Parsed from | Event | Value |
| --- | --- | --- |
| `session_id` | `thread.started` | `01a02287-f022-78d1-9b76-a5ab4eb43d9f` |
| `input_tokens` | `turn.completed.usage` | 10,847,616 |
| `cached_tokens` | `turn.completed.usage` | 10,666,496 |
| `output_tokens` | `turn.completed.usage` | 48,981 |
| `outcome` | `item.completed` + exit | `implemented`, exit 0 |

`usd` is NULL, which is §18.3 working rather than a gap: there is no OpenAI price row, and
a zero would read as a free run.

So the two binaries agree on every event shape the parser depends on. **This is a
measurement, not a guarantee** — nothing pins the in-image CLI, so a template bump can
still move it. The fixture and the binary that produced it stay recorded for that reason,
and `events.jsonl` (251 KB) from this run is archived under `artifacts/BAC-4/`.

## 2. One sandbox *name* attracts `github`; nothing else does

`p0-5-sandboxes.md` assumed a factory-created sandbox inherits no secrets. It inherited
`github` and `mcpgateway`, so James re-scoped `github` to the four `csbx` sandboxes by
hand — and a sandbox created *after* that still got it. The obvious reading was that
re-scoping does not work in sbx v0.38.0. **That reading is wrong.** Measured by creating
sandboxes and varying one thing at a time:

| Sandbox name | Workspaces | `secrets` from `sbx inspect` |
| --- | --- | --- |
| `secret-probe` | a scratch directory | `mcpgateway` |
| `secret-probe` | scratch dir, `git init` + GitHub `origin` | `mcpgateway` |
| `secret-probe` | `/Users/james/python-harness` | `mcpgateway` |
| `probe-python-harness` | a scratch directory | `mcpgateway` |
| `secret-probe` | **the factory's exact `create` argv** — `python-harness`, the vault, `--deny-network mcp.linear.app` | `mcpgateway` |
| **`factory-build-python-harness`** | **the same exact argv** | **`github`, `mcpgateway`** |
| `zzz-factory-zzz` | a scratch directory | `mcpgateway` |

The last two rows differ **only in the sandbox name**. Scoping works; the workspace is
irrelevant; a GitHub remote is irrelevant; the word "factory" in the name is irrelevant.
The name `factory-build-python-harness` reproducibly gets a live `GH_TOKEN` in the VM,
across `sbx rm` and re-create.

Nothing in the secret store admits to it:

```console
$ sbx secret ls --sandbox factory-build-python-harness
No secrets found for scope "factory-build-python-harness".

$ sbx secret rm github --sandbox factory-build-python-harness
No secret found for service "github" in scope "factory-build-python-harness"
```

But the daemon knows. The only `github` line in `sandboxd/daemon.log` is a per-VM
credential record under exactly that name, from the moment the re-scope took effect:

```json
{"level":"WARN","msg":"secrets: credential revoked",
 "vm_id":"factory-build-python-harness","service":"github"}
```

**Most likely cause:** the first run created this sandbox on 2026-08-20 while `github` was
still global. A per-name credential binding was recorded then, it survives `sbx rm`, and
`sbx secret ls` / `sbx secret rm` cannot see or clear it. Every recreation under that name
picks it up again.

**Settled 2026-08-21.** Two `factory-*` names with no history, created with the same argv
and removed again:

| Sandbox name | `secrets` |
| --- | --- |
| `factory-build-frontend-harness` | `mcpgateway` |
| `factory-review-python-harness` | `mcpgateway` |

Both clean. The binding is **local to the one name that already carried it**, so neither
the `factory-build-*` / `factory-review-*` scheme nor Phase 3's reviewer sandboxes are at
risk. `factory-build-python-harness` is the only poisoned name on this machine, and it is
inert because the registry no longer names it.

The preflight is right either way, and `policy.capability_secrets()` must not be widened
to get past it — that is the boundary change `AGENTS.md` names, not a fix.

## What was not measured

`--deny-network mcp.linear.app` reaches `sbx create` and the sandbox reports
`"network_policy": {"scope": "sandbox"}`, but whether the rule actually stops gateway
traffic is still untested. The validating run had an empty static MCP set and generated
no gateway traffic to test it against, so the rule remains installed and unproven — it is
item 4 of `p1-3-phase-1-validated.md`'s open list.
