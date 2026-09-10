# Frontend target acceptance checkpoint

Measured 2026-09-07 UTC (2026-09-06 Toronto). The frontend target's exact existing
production build image installs its merged dependency contract and passes its applicable
gates in a disposable VM-local clone. **Runtime compatibility acceptance remains incomplete:**
no model canary ran because the host hook rejected the names-only credential-environment
probe. This checkpoint does not authorize activation or create a compatibility/isolation
manifest.

## Configuration and measured layout

Registry project `frontend-harness` requires `codex-pnpm:v1`, no kits or static MCP,
clone isolation, a read-write vault mount, and a deny rule for `mcp.linear.app`.
Both existing production identities were started, inspected after startup, and stopped:

- `factory-build-frontend-harness`: image digest
  `sha256:8ab3deaa75f9c10fb0e95d866a57280bc1494950c1a90b2cc636c8b1391fd574`,
  no kits, Codex 0.146.0, Node v22.22.1, pnpm 10.15.1. `/proc/mounts` identifies
  `/Users/james/frontend-harness` as a separate ext4 filesystem. The expected project
  protocol directory and vault are read-write virtiofs mounts. The existing primary
  checkout and historical run data were preserved.
- `factory-review-frontend-harness`: same image digest and tool versions; writable
  protocol root at `/Users/james/factory/state/review/frontend-harness`, secondary
  `/Users/james/frontend-harness` source mount explicitly read-only in `/proc/mounts`.

Both inspect reports contain no proxy-managed capability secrets, as evaluated by the
production `capability_secrets` function after startup. Both sandbox policies contain the
required `mcp.linear.app` deny rule. This inspects configuration; it does not measure an
actual denied network request or enumerate the gateway's registered tools.

The build mount evidence establishes the Linux clone filesystem rather than the host's
macOS dependency directory. This continuation did not repeat the host-written marker
canary because host target edits were excluded from this bounded task. It also did not
exercise simultaneous frontend production attempts or full tracker/Git delivery.

## Dependency and gate results

Read-only public remote resolution pinned merged `v2` to
`87cf15cb0df220c0fd576e32960284cf396d1c58`. A fresh clone was created and detached at that
commit inside the build VM at
`/Users/james/frontend-harness/.factory/runtime-acceptance-20260907`.
The disposable clone remains available inside the stopped VM.

The install argv was read from that clone's `harness.config.json`: `pnpm install`.
It completed successfully, installing 1,053 packages from the existing pnpm cache.
The lockfile remained unchanged. pnpm reported ignored build scripts for esbuild, msw,
and unrs-resolver; no approval setting was changed. This measures installation using
available cache contents, not a cold registry download.

The target's vendored `gate_report.mjs --force --json` reported **pass**. ESLint,
Prettier, TypeScript, Vitest and Vite build each actually ran and exited 0. Playwright
was `not_applicable` under the target's conditional contract, and Lighthouse was
`disabled`; neither is claimed as a measured pass. The final tracked working tree
was clean. A host-side existence check confirmed this disposable clone path does not
exist on the host (`host-clone-absence.json`). Dependency and gate execution occurred only in the disposable Linux clone.

## Runtime boundary and next step

A direct names-only environment probe was rejected before execution by the host
PreToolUse hook: “Refusing tool call - the command references a protected secret
variable.” No secret values were requested or printed. The probe was not repackaged
into an opaque script to evade the refusal. The second, environment channel of the
production credential preflight therefore remains unmeasured in this continuation.
The registry's historical acknowledgement is not a fresh measurement.

The normal `steps.sandbox.preflight` entrypoint needs a run/context/store and includes
check recording, a host-written clone marker, and the protected-path canary. This
bounded task excluded live store writes and host target edits, and did not assemble
a complete scratch run/context for that entrypoint. Calling only its internal
environment helper after the direct rejection would merely repackage the same probe,
so that was not attempted. This is an incomplete preflight, not a claim that the normal
production entrypoint itself was tried and rejected. The initially inspected host
`harness.config.json` informed the rejected names-only probe; dependency installation
and gate selection subsequently used the pinned merged-v2 clone's own contract. The
reviewer's read-only secondary mount points to the existing host checkout, whose commit
was not substituted with the merged-v2 clone.

No model was launched, no worker canary was staged, and no hooks/schema/read-only model
acceptance is claimed for Codex 0.146.0. The earlier passing stock-VM 0.149.1 probes cannot
be transplanted onto this template. A permitted normal production preflight invocation
must establish the environment channel before those model probes proceed. Read-only
mount inspection alone does not prove model write refusal.

Operator evidence is retained under `artifacts/runtime-frontend-acceptance/`:
`acceptance.py`, `results.json`, exact install/gate output, inspect/mount/version records,
policy rows, final stopped inspections, and `sha256.json`. The script deliberately has
no credential-environment or model probe and its fresh-clone destination is non-reusable;
choose a new disposable destination before rerunning. No live database, ticket,
remote write, project setting, user trust store, or credential binding changed.
