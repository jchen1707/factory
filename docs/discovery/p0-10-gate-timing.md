# P0-10 — gate timing baseline

Both stacks, host and sandbox, warm caches. These set the state timeouts in §5.1.

## `python-harness` — `uv`

| Gate | Host | Sandbox (`UV_PROJECT_ENVIRONMENT` set) | Warm re-run |
| --- | --- | --- | --- |
| `uv sync` | — | 0.14 s | — |
| `uv run ruff check .` | 0.70 s | 0.07 s | — |
| `uv run ruff format --check .` | 0.04 s | 0.02 s | — |
| `uv run mypy` | 1.76 s | 1.02 s | 0.07 s |
| `uv run pytest` | 0.92 s | 0.52 s | 0.37 s |

**The sandbox is faster than the host**, roughly 2×, on every gate. The virtiofs passthrough
is not the bottleneck at this repository size; macOS process spawn is. The whole Python gate
suite is **under three seconds** either way, so §5.1's `verifying` timeout is dominated by
the model, not the gates. A 10-minute timeout is generous by two orders of magnitude.

## `frontend-harness` — `pnpm`

| Gate | Host | Sandbox (`codex-pnpm:v1`, host `node_modules`) |
| --- | --- | --- |
| `pnpm lint` | 3.09 s | **fails, 4.04 s** |
| `pnpm format:check` | 0.78 s | — |
| `pnpm typecheck` | 0.83 s | 1.08 s ✅ |
| `pnpm test` | 1.38 s | **fails, 0.33 s** |
| `pnpm build` | 1.80 s | — |

Also confirmed: `pnpm test` (vitest) **does** collect `tests/*.test.mjs` alongside
`src/**/*.test.tsx` — 42 tests across 3 files — so the `kind: test` gate covers the
harness's own node:test suite. `.agents/vendor/**` is excluded on purpose, with the reason
written into `vite.config.ts`.

## The finding that matters — a bind-mounted workspace cannot be shared

`sbx create` bind-mounts the host path at the **identical path** inside the VM. That makes
the per-project dependency tree a shared, single-platform resource:

- **Python.** Running `uv sync` in the sandbox wrote a `linux-aarch64` venv over
  `python-harness/.venv` and left `.venv/bin/python` a broken symlink on the host. See
  `p0-3-toolchain.md`. **Mitigation measured and confirmed working:** exporting
  `UV_PROJECT_ENVIRONMENT=/home/agent/venvs/<project>` before every sandbox command. Two
  full gate suites ran in the sandbox afterwards and the host `.venv` stayed
  `cpython-3.12-macos-aarch64`, healthy.
- **Frontend.** Worse, and with no equivalent escape hatch, because `node_modules` must sit
  beside `package.json`. The host's macOS `node_modules` is simply **not loadable** in the
  Linux VM:

  ```
  Error: … eslint-import-resolver-typescript/lib/index.cjs:30:31
  ✖ 13 problems (13 errors, 0 warnings)
  ```

  `pnpm typecheck` passes (pure TypeScript, no native binaries); `pnpm lint` and `pnpm test`
  both fail. Running `pnpm install` in the sandbox to fix it would overwrite the host's
  `node_modules` with Linux binaries — the Python failure, in reverse. **That experiment was
  deliberately not run**; the Python case already proves the mechanism, and reproducing the
  damage on a repo with native esbuild/rollup binaries buys nothing.

### Consequence for §8.4 / §8.5

- **Python path:** add `UV_PROJECT_ENVIRONMENT` to `SandboxSpec.env`. One line, measured.
- **Frontend path:** `sbx create --clone` becomes effectively **mandatory**, not optional.
  It mounts the workspace read-only and gives the agent a private in-container clone,
  reachable on the host as the `sandbox-<name>` git remote. That is the only shape that
  keeps `node_modules` VM-local.
- Either way the factory must **never** run a gate on the host for a project that has a live
  sandbox, and `factory doctor` should check both venv platforms match their host.

Given the frontend needs `--clone` regardless, adopting `--clone` for **both** stacks is the
simpler design: one code path, and it removes the whole class of host-filesystem side
effects that `p0-4-egress.md` shows the policy engine cannot contain (filesystem read and
write are `**` and not CLI-editable).
