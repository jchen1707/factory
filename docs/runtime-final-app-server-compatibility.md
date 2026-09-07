# Final app-server compatibility — 2026-09-07

Fresh build and reviewer checks passed against worker SHA256
`f2b3751989c9810250554ec4b35dfcb9eec07b00d11fc60b0a6f81e9168b93be`
and Codex CLI 0.146.0. Both dedicated sandboxes are stopped. This is compatibility
measurement, not adapter activation approval or a claim about an unmeasured sandbox.

| Scope | Sandbox | Fresh manifest |
| --- | --- | --- |
| Build private clone | `factory-build-final-worker-4647d194` | `artifacts/runtime-final-compatibility/build/factory-build-final-worker-4647d194.json` |
| Reviewer read-only target | `factory-review-final-compat-20260907` | `artifacts/runtime-final-compatibility/review/final-worker/factory-review-final-compat-20260907.json` |

Both manifests pass production `validate_compatibility`, including its new binding to
the actual worker bytes. The build sandbox name retains the previous hash suffix;
the manifest and every final attempt contain the final worker digest above.
Previous manifests and raw evidence remain preserved and cannot authorize this worker.

The build used production sandbox specification, immutable authority mount and full
preflight. A marker written into its private clone remained absent on the host.
Actual model `apply_patch` hit the protected harness configuration hook and was
refused; candidate HEAD, status and diff remained unchanged. Both canary and recovered
worker returned schema-valid `{"ok":true}` with exit 0.

The build detached holder remained running under PID 1 after 32 seconds. Targeted
process-group interruption yielded exit 143. The final worker resumed the same thread
and its normalized usage matched the resumed cumulative total minus the retained
interruption baseline. Exact counters, raw events and hashes are in
`build/verified-summary.json`, `build/final-interruption.json`,
`build/protocol-final/` and `build/evidence-sha256.json` under the artifact root above.
The reviewer independently measured actual read-only mount refusal, hook refusal,
schema, detached survival at 38 seconds, interruption and same-thread recovery;
[reviewer evidence](runtime-current-reviewer-compatibility.md) records its details.

The fresh and resumed build prompts requested a child-agent tool, and no child-start
event was observed. Structured output constrained the commentary, so this probe does
not infer tool availability from model text. The separate nested-agent acceptance
measured the final `agents.enabled=false` configuration on both start and resume.

Raw usage, explicit compaction reset, Sol-to-Terra model change and raw thread resume
were measured earlier in these exact sandboxes and runtime using worker helper SHA
`4647d194…`. Those retained events are explicitly distinguished from final-worker
execution. Compaction completed, latest-window input reset while cumulative accounting
remained nonzero, and cumulative input remained monotonic. Final-worker recovery
separately proves invocation-baseline subtraction. Natural 80% compaction and a
worker-triggered compaction were not forced by this refresh.

One initial build sleep probe returned its final answer while the shell sleep session
was pending; it was preserved and received no interruption credit. A clarified prompt
required waiting for the shell session and supplied the successful measured
interruption. This refresh makes no broader descendant-cleanup claim.

No product source, tracker, forge, production database, production routing, or FRO-12
state changed. No activation, migration or delivery occurred. Full factory recovery
orchestration and workflow completion remain separate acceptance evidence.
