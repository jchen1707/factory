# Review disposition handoff

Use this handoff when a blocking review finds a real architecture or scope choice that only
James can decide, and the implementation worker must receive that decision. The handoff is a
repair input. It does not accept findings, approve delivery, refresh requirements, or authorize
deployment, credentials, networking, customer data or any other live effect.

## Procedure

1. Run `uv run factory status <TICKET> --evidence`. Suspend the run if an agent is still
   working. `resume` acts only on a suspended, blocked, resumable or rejected awaiting-human
   run, or on a failed run with `--authorise`.
2. Run `uv run factory review-disposition-template <TICKET>`. It prints a draft that is not
   valid yet. The draft names the exact run, the SHA-256 of the run's current review summary,
   and every finding in review order, including medium and low
   findings. Save the draft outside Factory state. Replace every `REPLACE` value and fill every
   empty field.
3. For each finding, record a disposition of `must-fix`, `invalid` or `deferred`, a bounded
   implementation direction, and observable acceptance criteria. For `invalid` and `deferred`,
   state the restriction that still applies or the evidence in those fields. The command does
   not waive findings.
4. Validate the document. This step writes no Factory state:

   ```sh
   uv run factory review-disposition-check <TICKET> /absolute/path/decision.json
   ```

5. After James separately authorizes resuming this ticket, run:

   ```sh
   uv run factory resume <TICKET> --from implementing --review-disposition /absolute/path/decision.json
   ```

6. Read back the new attempt's `prompt.md` (under `.factory/run/<attempt>/`) and the run's
   status. Confirm that the prompt has the heading `James's audited review dispositions` and
   every source finding, direction and acceptance item before you report that the worker
   received the handoff.

## Document shape

```json
{
  "schema_version": 1,
  "ticket": "BAC-56",
  "run_id": "exact-factory-run-id",
  "source_review_sha256": "sha256-of-current-canonical-review-summary",
  "decided_by": "James",
  "decided_at": "2026-09-10T21:00:00Z",
  "findings": [
    {
      "finding": "- [high] path:line exact review finding",
      "disposition": "must-fix",
      "direction": "The bounded repair decision.",
      "acceptance": ["An observable pass or refusal condition."]
    }
  ]
}
```

The limits are enforced by `src/factory/review_disposition.py`:

- The file is UTF-8 JSON of at most 64 KiB.
- `decided_by` is `James`. `decided_at` has a zero UTC offset and is no more than 5 minutes
  ahead.
- `source_review_sha256` is the digest of `review/review-summary.json` re-serialized as compact
  JSON with sorted keys. Hashing the file itself gives a different value, so copy the digest
  from the template.
- `findings` lists 1 to 32 entries. Each `finding` string matches the review's finding line
  exactly, once each, in review order.
- Each `direction` is non-empty. Each `acceptance` list has 1 to 8 non-empty items.

## What Factory does with it

Factory copies the validated document to `state/runs/<run-id>/handoffs/` under a name that
contains its digest, and writes one `review-disposition-recorded` operator event for the run.
Submitting the same document again changes nothing. A different document for the same review
is refused with `review-disposition-conflict`.

Factory refuses the document before resuming when any of these is true: the ticket or run is
wrong, findings are missing or reordered, the review summary has changed, a field is malformed,
or `--from` is anything other than `implementing`. A refusal leaves the run where it was.

Only an implementer started by a resume, by the recovery ladder or by a verify-failure loop-back
reads the disposition. Its prompt renders it while the run's review summary still has the digest
the document names. A new review makes the old disposition ineligible, so triage the new finding
set from the start. Never edit retained evidence or a generated prompt.
