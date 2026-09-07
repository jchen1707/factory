# CRUD recovery acceptance

Measured 2026-09-07 UTC on isolated FRO-12 run `47515078d97246e9`, using the
supported factory CLI and production adapters. This is the disposable CRUD project;
production writers, existing products, and their tickets were untouched.

## Completed readiness and approval

The run initially had completed schema-valid readiness but was blocked because the
collector had required an unnecessary test-plan file. After the correction,
`factory resume FRO-12` recollected the original evidence and returned to planning
at attempt 1, preserving the branch and original schema/manifest/accounting archive.
A subsequent targeted drive reported `approval-required` for `1:implement:1`;
no second planner invocation was launched. Explicit CLI approval allowed the first
builder to start, and its transcript read the retained execution brief.

## Supported suspend and resume

While the builder had a modified API entry point and untracked implementation/test
files, `factory suspend FRO-12` recorded a terminal exit of 143, retained the session,
and stopped its sandbox. Before/after snapshots matched exactly for HEAD, branch,
tracked diff, status, and every untracked file digest, including existing plans.

An unapproved resume safely prevented another model launch but exposed an uncaught
`AgentApprovalRequired: 2:implement:1` CLI exception. That presentation defect is
now corrected for CLI and console resume: approval and slot holds return a normal
waiting message without a tracker block or launch. Six regressions reproduced the
failure first; all four factory gates pass in `factory-gates-resume-holds.json`.
The corrected held CLI path is regression-tested; it was not induced a second time
in this live run. The original exception did not bypass approval or discard work.
After explicit approval of that key, normal `factory resume FRO-12` started attempt 2
using `codex exec resume` with original session `01a079c0-a16b-7ff3-b80a-82cb727dd3ba`.
The immediate post-resume snapshot again matched the parked snapshot exactly.

Evidence is retained under `artifacts/crud-runtime-test/`: readiness/approval/launch
records; `before-suspend.json`, `after-suspend.json`, `after-resume.json`;
`suspended-inspect.json`; suspend/resume CLI output; and both attempt directories.
The original HEAD was `d99be91abc8603192f8af8aa6f24ddee66116441`. There was no new
builder commit yet: this measurement proves preservation of the existing committed
base and actual dirty implementation work, not a later delivery commit.

The resumed implementation was still running at this checkpoint. This is evidence
for supported preservation and session reuse, not a completed CRUD change, diagnosis
repair episode, app-server factory run, or simultaneous full ticket workflows.
