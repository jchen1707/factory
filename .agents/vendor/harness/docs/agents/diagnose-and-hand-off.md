# Diagnose and hand off

This is a noninteractive diagnosis in a fresh context.
Read the approved contract, current diff, failure evidence, and previous attempted fixes.
Preserve commits and uncommitted work. Write no implementation changes during diagnosis.
Keep full logs in their evidence files. Read relevant excerpts on demand.

Classify the failure as `code`, `environment`, `stale-authority`, `requirements`, or `review-dispute`.
Reproduce the acceptance failure before recommending automatic code repair.
Record the reproduction command as argv, its expected behavior, and its actual result.
Name a stable acceptance behavior and the evidence file that proves the reproduction.
Environment failures belong to the named adapter or setup path.
Requirements, scope changes, and review disputes require the operator's decision.
Recommend a disposition. Do not dismiss a finding or remove assertions to obtain a pass.

The handoff states the contract revision, branch, commit, and dirty-work inventory.
Include verified behavior, the remaining failure, attempted fixes, and evidence paths.
Write `execution-brief.md` and `test-plan.md` in the directory supplied by the control plane.
The test plan defines scenarios and boundaries. The builder implements one red-green slice at a time.
Return the structured result supplied by the control plane.
