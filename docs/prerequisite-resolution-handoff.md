# Prerequisite resolution handoff

`factory resume <TICKET> --prerequisite-evidence <FILE>` attaches fresh host-side evidence to
a run before it resumes. Use it when a prerequisite was broken when the frozen ticket or
specification was written, was repaired later, and was rechecked independently. The typical
case is a worker that blocked because the ticket still describes the old failure.

## Document shape

The JSON document is strict, names the ticket, and must be less than 24 hours old:

```json
{
  "schema_version": 1,
  "ticket": "BAC-56",
  "prerequisite": "host-preflight",
  "status": "resolved",
  "verified_at": "2026-09-10T12:00:00Z",
  "verification": {
    "command": ["uv", "run", "factory", "doctor"],
    "exit_code": 0,
    "summary": "Vendor installation repaired; Factory doctor passed"
  }
}
```

The limits are enforced by `src/factory/prerequisite_evidence.py`:

- The file is UTF-8 JSON of at most 64 KiB, with exactly these keys.
- `status` is `resolved`. `prerequisite` is a non-empty name.
- `verified_at` carries a timezone offset (any offset is accepted) and falls between 24 hours ago
  and 5 minutes ahead.
- `verification.command` is a non-empty argv list. `exit_code` is `0`. `summary` is non-empty.

## What Factory does with it

Factory copies the validated document to `state/runs/<run-id>/handoffs/` under a name that
contains its digest, and writes one `prerequisite-resolution-recorded` operator event for the
run. Supplying the same document again changes nothing. Invalid evidence is refused before
the resume runs. The refusal does not block or otherwise move the run.

When a resume, the recovery ladder or a verify-failure loop-back starts an implementer for this
run, its prompt gets a section named "Fresh host prerequisite resolutions". The section tells
the worker to treat older descriptions of that prerequisite as superseded. Factory reads back
only an audited copy whose digest still matches. Earlier `review-finding` blocks appear in the
same prompt as separate, unresolved repair obligations.

## What it does not do

- It adds prompt text and nothing else. It satisfies no host gate and skips no check. A resume
  that starts an agent still goes through the normal approval-mode, project-slot, budget,
  attempt-limit and usage-window checks.
- Factory never runs `verification.command`. The command and its exit code are your record
  of what you ran. Factory checks only that the record says it passed.
- Resuming does not rerun sandbox preflight. Preflight runs when Factory creates a sandbox
  (`src/factory/steps/sandbox.py`). If the repaired prerequisite lives inside the sandbox, only
  a fresh run proves it through preflight.
- Only an implementer prompt reads the evidence. A resume into `planning`, `verifying` or
  `reviewing` records it, but no prompt in those states shows it.
- `--from` is optional. Without it, the run re-enters the state it left, and a resumable run
  follows the recovery ladder.
- Recording the evidence does not edit Linear, the frozen specification, transitions, attempts,
  costs, commits or the worktree. The resume that follows changes the run as any resume does.
