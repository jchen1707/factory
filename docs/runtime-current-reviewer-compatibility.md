# Final worker refresh — 2026-09-07

The final worker SHA256
`f2b3751989c9810250554ec4b35dfcb9eec07b00d11fc60b0a6f81e9168b93be`
passed fresh actual reviewer hooks, schema output, read-only filesystem enforcement,
detached interruption and same-thread recovery on the same isolated sandbox/runtime.
The six-check manifest is now
`artifacts/runtime-final-compatibility/review/final-worker/factory-review-final-compat-20260907.json`.
It passes production `validate_compatibility`; the earlier manifest remains unchanged.

The fresh detached holder was adopted by PID 1 and still running at 38 seconds.
Targeted process-group interruption produced exit 143; recovery completed on the same
thread with exact retained-baseline subtraction. Full counters and source hashes are
in `final-worker/verified-summary.json`; raw attempts are under
`protocol/final-worker/`. The target remained clean and the sandbox was stopped.

Compaction/model-change calibration below is explicitly reused prior-worker evidence
from this exact runtime, not a claim that those turns ran again under the final worker.
The fresh worker now disables nested agents; direct enforcement tests for start and
resume are documented by the separate nested-agent acceptance work. This reviewer
pass made no deliberate nested-agent request.

## Previous worker measurement — preserved evidence

Six compatibility checks passed for fresh sandbox `factory-review-final-compat-20260907`,
Codex CLI 0.146.0, image `sha256:8ab3deaa75f9c10fb0e95d866a57280bc1494950c1a90b2cc636c8b1391fd574`,
and exact source worker SHA256
`4647d194bb59e4b9c1c5d5e2cfd91efc8b3426f2a753eea706e67d0dc2199c1d`.
The old manifest passed production `validate_compatibility` at measurement; current worker binding now refuses it because its worker SHA is stale. Neither measurement activates an adapter.

Evidence root: `artifacts/runtime-final-compatibility/review/`.
Manifest: `factory-review-final-compat-20260907.json`; assertions: `finalize.py`;
summary: `verified-summary.json`; digests: `evidence-sha256.json`.
Older evidence and manifests remain intact.

- The unchanged current worker discovered the project hooks and observed an actual
  `apply_patch` refusal on the protected generated OpenAPI contract. Its structured
  output was `{"ok":true}`, exit 0. The target repository remained clean.
- The target was a separate readonly fixture clone, alongside a writable protocol
  mount, without shared skills or static MCP. Direct `touch` returned the actual
  read-only filesystem error. Neither attempted canary file exists. The runtime omits
  command output notifications in this version, so direct mount evidence is retained
  separately from the model's report.
- A detached worker survived its launching process: holder PPID 1, elapsed 32 seconds,
  production poll `running`. After the actual sleep command began, production
  `kill_group` terminated its recorded process group with exit 143.
- A second current-worker invocation resumed that same thread. Its retained baseline
  was 22,566 input tokens; resumed total was 34,153. The complete invocation delta was
  exactly 11,587 input tokens, avoiding rebilling the preceding attempt.
- A bounded raw JSON-RPC probe using the exact current worker's hook helpers measured
  successive cumulative input totals 11,177 → 22,381 → 28,892 → 40,295 → 55,308 → 70,348.
  Explicit compaction emitted both a context-compaction item and completed turn;
  latest usage reset separately from cumulative usage. A Sol-to-Terra turn and
  same-thread restart completed. Model availability and effort metadata are retained.

This fixture does not induce automatic model rerouting, natural 80% occupancy, or
worker-triggered compaction. Explicit runtime compaction is a raw-protocol measurement;
full factory recovery orchestration and concurrency acceptance are separate evidence.
All model attempts stayed within this new sandbox; no CRUD VM, tracker, production
service, delivery action, user trust store, or worker source was changed.

The inherited authentication probe printed an invalid-token failure. Its sbx transport
status nevertheless reported success, so actual failure text, rather than transport
status, was used for the capability check. The only inspect secret was the existing
MCP gateway exception, with Linear network access denied. No valid credential was
introduced. The sandbox was stopped after validation; `stopped-inspect.json` records it.
