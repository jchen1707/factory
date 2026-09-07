# Diagnose and hand off

This is a noninteractive diagnosis in a fresh context.
Read the approved contract, current diff, failure evidence, and previous attempted fixes.
Preserve commits and uncommitted work. Write no implementation changes during diagnosis.
Keep full logs in their evidence files. Read relevant excerpts on demand.

Classify the failure as `code`, `environment`, `stale-authority`, `requirements`, or `review-dispute`.
Reproduce the acceptance failure before recommending automatic code repair.
Record the reproduction command as argv, its expected behavior, and its actual result.
Name a stable acceptance behavior and retain the reproduction details in the handoff.

The control plane supplies `reproduction_evidence` and `reproduction_sha256` in `handoff.json`.
For a repair recommendation, copy `reproduction_evidence` exactly into the structured result.
This value identifies the host-recorded verifier artifact relative to the factory evidence root.
It is an artifact identity, not an explanation. Do not add prose, new log paths, or absolute paths.
Do not replace it with the path of a reproduction you ran during diagnosis.
Put explanations in `summary`, `acceptance_behavior`, or the retained execution brief.
Keep new reproduction commands and logs in the retained handoff evidence.
If the supplied identity is empty, return `needs-human` with an empty `reproduction_evidence`.
Explain what evidence is missing. Never fabricate an artifact identity or digest.

A `repair` result recommends repair; it does not authorize another attempt.
The control plane validates evidence provenance, budgets, repair limits, and operator approval.
Report that authorization remains pending until the control plane accepts the recommendation.

Environment failures belong to the named adapter or setup path.
Requirements, scope changes, and review disputes require the operator's decision.
Recommend a disposition. Do not dismiss a finding or remove assertions to obtain a pass.

The handoff states the contract revision, branch, commit, and dirty-work inventory.
Include verified behavior, the remaining failure, attempted fixes, and evidence paths.
Write `execution-brief.md` and `test-plan.md` in the directory supplied by the control plane.
The test plan defines scenarios and boundaries. The builder implements one red-green slice at a time.
Return the structured result supplied by the control plane.
