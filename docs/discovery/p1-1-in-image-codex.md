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

## 2. A fresh factory sandbox is still given `github`, after the secret was re-scoped

`p0-5-sandboxes.md` assumed a factory-created sandbox inherits no secrets. It inherited
`github` and `mcpgateway`, so James re-scoped `github` to the four `csbx` sandboxes by
hand. The store now agrees:

```console
$ sbx secret ls
SCOPE                    TYPE      NAME     SECRET
codex-dotfiles           service   github   (stored)
codex-factory            service   github   (stored)
codex-frontend-harness   service   github   (stored)
codex-python-harness     service   github   (stored)
(global)                 service   openai   (oauth configured)

$ sbx secret ls --sandbox factory-build-python-harness
No secrets found for scope "factory-build-python-harness".
```

**The sandbox gets it anyway.** `sbx inspect` on the sandbox this run created:

```json
"secrets": [
  { "name": "github",     "source": "uploaded" },
  { "name": "mcpgateway", "source": "uploaded" }
]
```

and inside the VM, `GH_TOKEN` is set in the agent's environment (alongside
`MCP_SENTINEL_TOKEN_NAME`, which is the shape of a proxy sentinel rather than a bearer
token — the value was not read, and whether it authenticates was not tested).

So **re-scoping a service secret does not stop `sbx create` uploading it** in v0.38.0. The
scope list and the sandbox disagree, and the sandbox wins. `sbx create` has no
`--no-secrets` flag; `--profile` is the only creation-time governance lever and is
unexplored.

This is the §8.7 failure the preflight exists to catch, and it caught it twice — once on a
sandbox created while `github` was global, and once on one created after it was not.
`policy.capability_secrets()` is unchanged and must stay that way: the exclusion list holds
`GATEWAY_CREDENTIAL` alone, and adding `github` to it would be the boundary change
`AGENTS.md` names, not a fix.

**Consequence: Phase 1 cannot reach `implementing` on this machine until a factory sandbox
can be created without `github`.** That is a credential-scoping decision, which is James's.

## What was not measured

`--deny-network mcp.linear.app` reaches `sbx create` and the sandbox reports
`"network_policy": {"scope": "sandbox"}`, but whether the rule actually stops gateway
traffic is still untested — the run never got far enough to generate any. Hazard 2 of the
handoff stands.
