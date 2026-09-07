# Merged shared-source consumer refresh

## Harness #33 follow-up — 2026-09-07

James merged #33 as `8bc104e33e89a325f3db925f215c5742d7a957bb`. The current
refresh uses that exact merged source through `scripts/vendor_sync.py`. The generated
change is the manifest plus the approved diagnosis evidence-identity contract.
No worker code, production setting or existing run authority changes.

Evidence is retained under `artifacts/runtime-consumer-refresh-33/`. Harness merged-v2
generation passes. Meta run `34089401107` fails because both existing stack gitlinks
still point to consumers with stale layer-A pins; the retained failure log names
exactly those two currency errors. After James merges the refreshed stack PRs, update
the gitlinks to the exact merged stack commits and require
`check_submodules.py --pins --current`. Do not pin unmerged branches or waive currency.

Factory PR #83 now includes sync commit `12f7b4e`. All four declared gates ran and
passed, exit 0 with empty output tails (`factory-gates.json`); integrity and freshness
pass. Mypy retains its configured-path limit and pytest establishes fixture-backed
control-plane behavior, not new runtime measurements. The worker hash is unchanged.

Python [#77](https://github.com/jchen1707/python-harness/pull/77) is at
`d105212ec814b1ab927897fa0a19a0650c660be1`; frontend
[#55](https://github.com/jchen1707/frontend-harness/pull/55) is at
`17b6629b7b0ad6eaae00af0b320d9b757657d64c`. Both source and generated-main default
gates and post-commit freshness pass, with clean worktrees. All remote checks pass:
Python Linux/Windows, generation, freshness and integration; frontend Linux/Windows,
generation, freshness, PR body, e2e and Lighthouse. Conditional integration/browser
gates were not asserted locally for this instruction-only diff; remote job results
are recorded separately. Evidence: `source-stacks/python/summary.json` and
`source-stacks/frontend/report.md` under the new artifact directory.

Disposable draft [#1](https://github.com/jchen1707/factory-crud-verification/pull/1)
is at `9a6ef079`. All ten declared API/web gates, three config schema checks, local
freshness and remote freshness CI pass. Evidence: `crud/`. Only the isolated draft
consumer's generated content changed; its product main and FRO-12's candidate,
worktree, frozen authority and blocked state were untouched.

All refresh PRs remain for James to merge. No tracker write, sandbox start, migration,
production setting change or activation occurred in this continuation.

## Previous harness #32 refresh (historical)

James merged harness PR #32 as `f7917ce3a66f916109c5c2d621d4b621bc6fca87`.
All refreshes in this continuation use that exact committed source through
`scripts/vendor_sync.py`; no generated vendor file was edited manually.

Factory’s refresh is commit `4663d49` in PR #83. Integrity and freshness checks
against the merged source pass. Its four declared gates all ran and passed in
`artifacts/runtime-consumer-refresh/factory-gates.json`. Mypy covers declared paths;
the fixture suite proves control-plane logic, with real runtime evidence recorded
separately. No current run’s immutable authority was replaced by this source refresh.

Disposable consumer PR #1 is updated to `f06e2420c5f761813317818db2d3e24a5a51b5fc`.
All ten declared gates, schema checks, generated test pathspec checks and source
freshness pass. Remote freshness CI also passes (run `34082010013`). The PR remains
draft; its product main, FRO-12 run and candidate were not changed.
Evidence: `artifacts/runtime-consumer-refresh/crud/`.

Python [PR #77](https://github.com/jchen1707/python-harness/pull/77) is at
`c162e705c7845b2d4b64e444fedf7cfe987eda41`; frontend
[PR #55](https://github.com/jchen1707/frontend-harness/pull/55) is at
`9980706e772ecadbd72dfe091513fac6def4c501`. Both source and generated-main default
gates pass, as do post-commit vendor freshness checks. Their branches are clean.
Python’s remote Linux/Windows verification, generation, freshness and integration
checks pass. Frontend’s remote Linux/Windows verification, generation, freshness,
PR-body, e2e and Lighthouse checks all pass.
Evidence: `artifacts/runtime-consumer-refresh/source-stacks/refresh-summary.json`,
`python-ci.json` and `frontend-ci.json`. Conditional browser/database gates were not
asserted in the local default runs; remote workflow results are distinct evidence.

The merged harness v2 Meta run `34081926230` correctly reports stale submodule pins:
its mounted consumer commits still vendor the previous shared revision. This is a
merge-sequencing obligation, not permission to silence the check. After James merges
the stack refresh PRs, bump the shared repository’s submodule pins to the exact merged
stack commits and re-run `check_submodules.py --pins --current`. No gitlink will point
to an unmerged consumer branch. Retained failure log:
`artifacts/runtime-consumer-refresh/harness-post-merge-meta-failure.log`.

This work refreshes factory/shared contracts. It does not authorize production
activation, replace FRO-12 authority, advance test tickets, or require CRUD completion.

A subsequent real diagnosis test exposed a shared wording defect, corrected in green
[harness #33](https://github.com/jchen1707/harness/pull/33), commit `36bb716`.
The consumer commits above still pin merged #32. Once James merges #33, regenerate
against that exact merged SHA, preferably updating the still-open stack PRs before their
merge; then finish the exact merged gitlink sequence. Only the isolated diagnosis retry
used unmerged #33, through an explicit scratch authority replacement.
