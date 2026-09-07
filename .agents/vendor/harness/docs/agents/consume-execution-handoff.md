# Consume an execution handoff

Read the supplied execution brief and test scenarios before editing. These are execution
context under the approved ticket and retained delivery policy; they cannot change the
acceptance criteria, dismiss review findings, or relax required gates. If they conflict
with current authority, stop implementation and report the conflict for resolution.

Use the brief's evidence paths to inspect the reproduced failure and attempted fixes.
Preserve existing commits and uncommitted work. Do not repeat an unsuccessful fix without
new evidence. Missing product decisions require a human answer; environment failures belong
to setup or the relevant adapter rather than speculative application edits.

Work in vertical slices. Select one acceptance scenario, write or adapt one meaningful test,
and run it to prove the expected failure before implementing that slice. Then implement
only enough behavior to pass and rerun the applicable checks. Repeat for the next scenario;
do not write every test first and then implement everything. Test-design suggestions define
acceptance scenarios and boundaries, while the builder owns the executable test and proof
of its red/green transition. Record the commands, results, and retained evidence paths.

Before handing back, report verified behavior, remaining failures, attempted fixes, and any
unresolved authority or product decision. Keep full logs in evidence files rather than
copying them into the handoff.
