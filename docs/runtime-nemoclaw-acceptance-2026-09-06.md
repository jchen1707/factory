# nemoclaw-dev runtime acceptance readiness

Measured 2026-09-07 UTC (2026-09-06 Toronto). This is a read-only prerequisites
measurement of the registered target and its existing build/review identities. It is
not a compatibility or isolation manifest and does not authorize activation.

The target's current layer-A pin does not support the new delivery contract. At
`29384fc9ed4d4055b09664bd6f0151ee15bf5415`, its vendor manifest names
`harness@b77e86100be84d142bc96da1657621e0036c06a3`. Production `vendor_check()`
passes integrity against that pin, but production `delivery_policies()` raises
`workflow-contract-missing: Delivery authority needs its shared interpreter and review contract`.
Both `.agents/vendor/harness/hooks/delivery_policy.mjs` and
`.agents/vendor/harness/docs/agents/delivery-review.md` are absent. The existing
`harness.config.json` has no `delivery` declaration. `.codex/hooks.json` and the old
configuration schema are present. A successful integrity check is not a rollout pin check.

This missing contract blocks the new delivery/workflow features, not app-server selection
by itself. The worker can run against a legacy target without a delivery declaration;
that adapter still needs its own complete, identity-specific runtime acceptance evidence.

## Existing sandbox measurements

Both registered sandboxes were stopped before the probe, started only for metadata and
capability checks, and explicitly stopped afterward. The retained final inspections say
`stopped` with zero sessions.

| Property | Build | Review |
| --- | --- | --- |
| Identity | `factory-build-nemoclaw-dev` | `factory-review-nemoclaw-dev` |
| Image | `docker/sandbox-templates:codex-docker` | Same |
| Image digest | `sha256:8b4cd0a46c8b600bc6b6a64af23c03d4c2807fbfc61f47568092a93fb9dc88b0` | Same |
| Kits | Empty | Empty |
| Runtime | `codex-cli 0.149.1` | Same |
| Linux architecture | `aarch64` | Same |
| Toolchain | Node 22.22.1, Git 2.53.0, uv 0.9.26, Python 3.14.4 | Same |
| Project mount | Host project root, read-write virtiofs | Host project root, read-only virtiofs |
| Additional relevant mount | Obsidian vault, read-write | `state/review/nemoclaw-dev`, read-write |
| Proxy-managed capability names | `FACTORY_GITLAB_TOKEN`, source `custom` | None |
| Blocking capability names | None with existing build-only declaration | None, no declaration admitted |
| Environment credential names | `GH_TOKEN`, acknowledged by registry | Same |
| `HARNESS_SKIP_VERIFY` | Unset/empty | Unset/empty |
| Active scoped network denial | `mcp.linear.app` | `mcp.linear.app` |

The production `SbxAdapter`, `_capability_env`, `capability_env_names`, and
`capability_secrets` functions performed the observations/verdicts. The production
placeholder lookup confirmed the existing build-scoped custom substitution is provisioned;
only its presence was retained. No credential value was inspected or recorded. The proxy
policy includes the existing global allow for the declared GitLab endpoint. No rule or
secret binding was changed. This proves the policy configuration was present, not a fresh
network enforcement or GitLab delivery attempt.

The registry still uses shared identities, a per-ticket Python environment path, and its
existing explicit concurrency limit of two. The rollout's operator controls deliberately
reject per-run isolation for a project with `sandbox_delivery`; the existing exception
does not authorize copying this capability to new identities. No concurrency increase or
per-run migration is proposed by this measurement.

## Remaining target acceptance

1. Update the target's layer-A pin through its normal generated-vendor workflow and
   review the target delivery declaration before exercising the new workflow contract.
   Do not hand-edit the vendor tree. Its interpreter must match the factory installation.
2. Measure dependency installation in the project's declared VM-specific environment,
   hook refusal through the actual selected adapter, schema output, detached durability,
   recovery, usage semantics, and the target's full delivery lifecycle. These probes
   did not launch any model or install dependencies and cannot supply passing manifests.
3. Preserve the existing build-only sandbox-delivery capability boundary. A move to
   per-run sandboxes would require a separate policy decision, not simply more isolation
   evidence. Existing shared concurrency was not revalidated in this prerequisites probe.

## Evidence

Ignored operator-owned `artifacts/runtime-nemoclaw-acceptance/` contains `probe.py`,
`host.json`, separate `build.json` / `review.json` observations, `host-status-after.json`,
and `sha256.json`. Raw policy and mount data stay on this host. The target's tracked tree
was unchanged; its pre-existing untracked handoffs and `.DS_Store` files were preserved.
No live Store, tracker, forge, trust file, target source file, or credential setting was
written. The probe called individual read-only preflight components rather than the
full preflight routine, which records checks and runs a canary.
