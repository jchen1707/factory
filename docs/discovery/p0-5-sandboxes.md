# P0-5 — sandbox inventory

`sbx ls`, before Phase 0 created anything:

| Sandbox | Agent | Status | Workspace |
| --- | --- | --- | --- |
| `codex-dotfiles` | codex | stopped | `/Users/james/dotfiles` |
| `codex-factory` | codex | stopped | `/Users/james/factory`, `…/Obsidian Vault` |
| `codex-frontend-development-harness` | codex | **running** | `/Users/james/frontend-development-harness` **(missing)**, `…/Obsidian Vault` |
| `codex-python-harness` | codex | stopped | `/Users/james/python-harness`, `…/Obsidian Vault` |

**No collision.** Every existing sandbox is `codex-*`; the factory's `factory-build-*` and
`factory-review-*` namespace is free. §8.5's hard rule — never attach to `codex-<project>` —
has nothing to trip over, and a unit test asserting the prefix is sufficient.

Two observations worth carrying forward:

- `codex-frontend-development-harness` was **running** against a path that no longer exists.
  Same root cause as the stale hook trust in `p0-6-codex-trust.md`: the rename from
  `frontend-development-harness` to `frontend-harness` was never propagated.
  ✅ **Removed 2026-08-20**; `csbx` will recreate one at the correct path on next use.
- `codex-factory` already exists over `/Users/james/factory`. The factory repository is
  therefore itself already a `csbx` workspace — worth knowing before Phase 1 adds
  `factory-build-factory` to the same directory.

`sbx inspect <name> --json` is undocumented in `sbx --help` but works, and returns exactly
what the adapter's `inspect()` needs:

```json
{"name":"codex-python-harness","agent":"codex","kits":[],"state":"stopped",
 "image":"docker/sandbox-templates:codex-docker","image_digest":"sha256:8dab63bd…",
 "auth_mode":"oauth · openai","workspace":"/Users/james/python-harness",
 "network":"codex-python-harness","network_policy":{"scope":"global"},
 "proxy":"172.17.0.2:3128","secrets":[{"name":"github","source":"uploaded"},
 {"name":"mcpgateway","source":"uploaded"}],"mcp_gateway":true,"sessions":0,
 "daemon_version":"v0.38.0","daemon_uptime":"17h 50m"}
```

⚠️ **`secrets` is non-empty on the existing sandboxes** — `github` and `mcpgateway` are
uploaded into `codex-python-harness`. §8.7 requires **no credentials in the VM** for factory
sandboxes. Since the factory creates its own sandboxes it does not inherit these, but the
preflight assertion in §8.7 should read `inspect(...).secrets == []` and fail the run
otherwise, rather than assuming it.
