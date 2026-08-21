# P0-8 — Linear credential

```
$ security find-generic-password -a "$USER" -s factory-linear   →  ABSENT
$ security find-generic-password -a "$USER" -s claude-mcp-linear-fro  →  PRESENT
```

**`factory-linear` does not exist. This is the one item still waiting on James:**

```sh
security add-generic-password -a "$USER" -s factory-linear -w
```

## An existing key is already on the machine, under a different name

`~/.codex/config.toml`'s `[mcp_servers.linear]` reads `claude-mcp-linear-fro` from the
keychain. That key was used to run P0-11 (read-only) so the phase could produce a real
answer instead of a blocked row — see `p0-11-intake-dry-run.md`. Despite the `-fro` suffix
it is **not** FRO-scoped: the query returned BAC issues too.

Two options, and it is James's call:

1. **Store a separate `factory-linear` key** — what the plan says. The factory's key can
   then be revoked, rotated or scoped independently of the MCP server, which matters because
   §13.1 gives the factory **five write scopes** the MCP integration does not need.
2. **Point `config/models.toml`'s sibling — the intake config — at `claude-mcp-linear-fro`.**
   One fewer secret, but the factory and the interactive MCP server then share a blast
   radius, and revoking one revokes the other.

Recommend (1), unchanged from the plan. The value of a separate name is exactly that it can
be turned off without breaking James's own tooling.

Whichever is chosen, §13.1's rule holds: the key is read **host-side only**, never injected
into a sandbox, and never printed. Both the probe above and the P0-11 script read it with
`security find-generic-password -w` into memory and never echo it.
