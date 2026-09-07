# Retained Terra builder pricing replay — 2026-09-07

The completed DIAG-1 builder's retained events now produce a complete **$0.1746912 API-equivalent estimate** through the corrected production worker and accounting collector. This is an offline replay of the existing invocation, not a new live model measurement or a change to its original accounting record.

## Defect and correction

The original builder invocation `3aef532aab9d409a:3:implement` recorded complete usage but incomplete pricing: the worker recognized request-level long-context bands only for Astra and Sol. Terra and Luna already had dated rates in `config/prices.toml`, but their requests carried `long_context=null`. Factory commit `9a08440` adds Terra and Luna to that classification. The official [Terra model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-terra) and [Luna model documentation](https://developers.openai.com/api/docs/models/gpt-5.6-luna) were checked on 2026-09-07 for the threshold and rates. The retained red run shows four boundary regressions failing before correction; 56 worker tests and all four factory gates pass afterward. The correction uses the model's 272,000-input-token threshold per request, not the cumulative invocation total.

Corrected worker SHA-256: `4647d194bb59e4b9c1c5d5e2cfd91efc8b3426f2a753eea706e67d0dc2199c1d`. Replayed pricebook SHA-256: `55e78193dcf2683f0209cdd39ec685b560f7f19698407eacca790aa46d96ed71`.

## Measurement

`artifacts/runtime-builder-pricing-acceptance/replay.py` passes all 1,148 retained `factory.runtime` events, with their original timestamps, through production `app_server_worker.run`. A fake transport supplies the already captured server messages and records outgoing requests; it never launches Codex, executes a tool, or connects to a sandbox. The original workdir is read only for hook identity validation. Prompt and schema come from retained copies; outputs go exclusively into the replay directory.

All raw events are byte-equivalent after JSON decoding, the worker exits 0, and the terminal structured output matches the original. Independent wire arithmetic confirms that each of the 12 cumulative usage deltas equals the runtime's corresponding latest usage. The largest individual input request is 28,950 tokens. Every request therefore uses the normal context band even though the cumulative invocation input exceeds 272,000 tokens.

| Usage | Tokens | Normal standard rate per million | Estimated USD |
| --- | ---: | ---: | ---: |
| Uncached input | 36,418 | $2 | $0.072836 |
| Cached input | 256,256 | $0.20 | $0.0512512 |
| Cache-write input | 0 | $2.50 | $0 |
| Output | 4,217 | $12 | $0.050604 |
| Total | | | **$0.1746912** |

Total input is 292,674 tokens. Reasoning output (1,425 tokens) is already included in output, not charged again. The copied dated pricebook applies at the captured event timestamps. Independent decimal arithmetic agrees with production's sum of the 12 request estimates.

Service-tier provenance has a specific limit: the worker reconstructs `turn/start` with `serviceTierForTurn="default"`, and the retained invocation metadata records `standard`. The actual captured `thread/settings/updated` event reports `serviceTier=null`; it does not independently attest a billed tier. Thus the standard rate is the existing factory interpretation of its requested default tier. This result is neither a provider billing receipt nor a claim about Codex account charges.

A **new scratch Store** receives explicitly marked replay metadata (`replay_of` and `evidence_kind`). Production `accounting.collect` runs twice. Both results are identical, with one cost row and no double counting. The original invocation and Store remain unchanged and still retain their honest pre-correction incomplete estimate. Adding this replay projection to the two previously complete Sol estimates would give $1.5295656 for the three known workflow invocations; this is a derived projection, not a rewritten original run total.

## Evidence and boundaries

Evidence lives in `artifacts/runtime-builder-pricing-acceptance/`:

- `result.json`, `replayed-usage.json`, and `request-delta-audit.json`: result and independent per-request comparison.
- `transport.json` and `events.jsonl`: fake transport requests and normalized replay stream.
- `accounting-first.json`, `accounting-second.json`, and `state/factory.db`: new-store accounting and idempotence evidence.
- `original-hashes-before.json` and `original-hashes-after.json`: all 108 retained original files outside `shared-source` have unchanged hashes, including the original Store, protocol, and report result.
- `worker-red.txt` and `factory-gates.json`: source regression and four-gate evidence retained by the source owner.
- `replay.py` and `config/prices.toml`: executable replay and exact rates. An initial replay harness typo used the wrong cache-write field name after accounting had succeeded; its scratch database survives as `state/fixture-first-pass.db`. Correcting the harness and rerunning produced the final evidence above.

No sandbox, model, tracker, original database, or FRO-12 work was changed. This closes the measured Terra normal-band pricing omission and duplicate reconciliation for this retained builder stream. It does not validate a fresh deployment of the corrected worker, actual long-context Terra requests, Luna live events, provider billing, or nested-agent accounting completeness. The DIAG-1 builder had no observed collaboration events; unrelated nested invocation trees require separate evidence.
