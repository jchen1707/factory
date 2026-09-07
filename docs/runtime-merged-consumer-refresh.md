# Merged shared-source consumer refresh

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
