# Cross-stack CI correction and acceptance

Harness PR [#32](https://github.com/jchen1707/harness/pull/32) is green at
`5f4e3dd76584b4f69e74df63043f61b56eb009d9`: `generate`, `submodules`, and
`cross-stack` passed. [Remote cross-stack run](https://github.com/jchen1707/harness/actions/runs/34080704112)
ran the declared consumer gates after real installs. Nothing was merged or activated.

The original failure reproduced locally with `python3 scripts/cross_stack.py`:
both consumers installed but every applicable gate was `skipped_unchanged`, yielding
`layer A changed but no gate ran`. Git correctly exposed changed shared instructions
and a JSON contract. Those files are outside both consumers’ Stop-hook code filters.

The correction is in the owning layer-A CI caller. Once its existing content-motion
check detects a change, it invokes the delivered `gate_report.mjs --json --force`.
The reporter still owns gate eligibility, disabled gates, opt-in assertions and probes.
Identical content (excluding the SHA-only manifest) still avoids installation. The
zero-executed-gates failure guard is retained. Consumer filters were not weakened.

A real temporary Git source/consumer regression exercises actual sync and the delivered
reporter. It first observes unchanged content executing nothing, then commits only a
shared instruction change and requires an actual declared gate to execute. Disabled and
unasserted integration commands deliberately fail if incorrectly invoked. The test
failed with the original CI symptom before the correction and passes afterward.
Independent bounded review found no concrete defect in the correction.

Validation:

- `python3 -m unittest scripts.check_test.CrossStackVerdict`: 9 tests passed.
- `python3 scripts/check.py --since=028f0c8ea82a7c94d9b83eb38f1be70ec3ef5afb`:
  all shared contract, hook, generation, template and consumer checks passed with both
  pinned submodules checked out. Earlier missing-submodule skips are superseded.
- `python3 scripts/cross_stack.py`: Python lint/format/types/tests and frontend
  lint/format/types/tests/build all ran and passed. Integration and Playwright were
  not applicable; Lighthouse was disabled. Default Python tests exclude DB integration;
  mypy covers its declared files; frontend boundary coverage depends on declared patterns.
- `npx --yes prettier@3 --check .`: passed. The first corrective push had an ADR table
  formatting failure; the exact workflow command reproduced it, and the formatting
  correction is included in the final green commit.

Evidence lives in `artifacts/crud-runtime-test/`: `cross-stack-before.log`,
`cross-stack-regression-red.log`, `cross-stack-regression-green.log`,
`cross-stack-after.log`, `harness-check-ci-fix-final.log`,
`harness-prettier-ci-fix.log`, and `harness-pr32-ci-green.{json,log}`.

Factory PR #83 has no reported check runs because the repository has no `.github`
workflow tree; GitHub also reports an unprotected main branch and no rulesets. Its
recorded local four-gate pass is valid evidence, but there is no remote green-CI claim.
No branch protection or CI configuration was changed during this check.

CRUD PR #1 remains draft with failed freshness until James merges shared source and
its vendor pin is regenerated against the exact merged SHA. The earlier `d2992c8`
pin must not be relabeled as current. Full factory acceptance remains separate from
shared CI success and disposable app completion.
