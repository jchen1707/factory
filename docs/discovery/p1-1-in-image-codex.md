# P1-1 — the in-image Codex, and the secret a fresh factory sandbox still gets

Measured 2026-08-21, from inside `factory-build-python-harness` created minutes earlier by
`factory run BAC-4`. Both facts were open hazards in `docs/handoff-phase-1-validation.md`;
neither is now.

## 1. The in-image binary is 0.146.0, not the 0.147.0 the parser was pinned to

```console
$ sbx exec factory-build-python-harness /bin/sh -lc 'codex --version'
codex-cli 0.146.0
```

The host runs `codex-cli 0.147.0` and `docs/discovery/codex-events.md` — the fixture
`agent/codex.py::parse_events` is written against — was captured there. `p0-3-toolchain.md`
predicted this from the image tag; it is now confirmed by running the binary.

**Nothing has yet parsed 0.146.0's output**, because no run has reached `implementing`. The
prediction stands as written: if `parse_events` returns an empty or partial transcript, this
is the first thing to suspect, and the fix is to re-capture the fixture from inside the VM
or pin the in-image CLI — not to bend the parser around unlabelled output.

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

**Untested, and it would settle it:** create `factory-build-frontend-harness` — a
`factory-build-*` name that has never existed. If it comes up clean, the binding is local
to the one poisoned name and nothing about the factory's naming scheme is at fault.

The preflight is right either way, and `policy.capability_secrets()` must not be widened
to get past it — that is the boundary change `AGENTS.md` names, not a fix.

## What was not measured

`--deny-network mcp.linear.app` reaches `sbx create` and the sandbox reports
`"network_policy": {"scope": "sandbox"}`, but whether the rule actually stops gateway
traffic is still untested — the run never got far enough to generate any. Hazard 2 of the
handoff stands.
