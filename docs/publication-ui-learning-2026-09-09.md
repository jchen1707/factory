# UI and learning publication — September 9, 2026

James authorized pushing the prepared changes and opening PRs. These are draft PRs:
implementation is reviewable, while Claude authenticated capture/recall still awaits host
login renewal. No merge or deployment was performed.

| Repository | PR | Base |
| --- | --- | --- |
| Shared harness | [#42](https://github.com/jchen1707/harness/pull/42) | v2 |
| Factory | [#98](https://github.com/jchen1707/factory/pull/98) | main |
| Python harness | [#81](https://github.com/jchen1707/python-harness/pull/81) | v2 |
| Frontend harness | [#59](https://github.com/jchen1707/frontend-harness/pull/59) | v2 |
| Go harness | [#4](https://github.com/jchen1707/go-harness/pull/4) | v2 |

Merge the shared source first, then regenerate consumer pins to the landed SHA and rerun
freshness checks. Consumer freshness currently fails because upstream v2 has not incorporated
the feature. Do not weaken that check or pin back to the old source merely to make it green.
The source and Factory branches included their current bases; each consumer integrated the
newly fetched v2 commits before publication. Generated conflicts were resolved by vendor sync.

## Pre-push test isolation repair

Frontend's first actual pre-push run exposed a defect absent from ordinary gate execution:
inherited Git environment variables redirected fixture Git commands into the consumer's
repository. The hook failed and prevented the push, but fixtures changed its local HEAD and
`core.bare` configuration. The intended head `6bc19ce` and clean worktree were restored;
fixture-only branch/worktree registrations were removed. Local user identity configuration
and the origin URL were unchanged. Accidental fixture commits remain under the local-only
backup ref `backup/prepush-fixture-38816e5`; that ref was not pushed.

Harness `e2ebc34` isolates fixture Git subprocesses and makes learning project/context
queries honor their explicit directory rather than a calling hook's Git environment.
A disposable outer-repository regression fails on the old code, passes on the repair,
and verifies that HEAD, index and configuration remain unchanged. The full source check
ran in an isolated recursive clone with all three stack mounts. The shared/delivery suites
reported 172 passing tests. All consumer gates and integrity checks passed after sync.

The corrected actual frontend pre-push hook passed tests and build; no hook was bypassed.
The branch was then pushed successfully. Python's hosted Windows check also exposed a
platform-specific fixture failure: URL.pathname constructed an invalid `D:\D:\...` module
path. Harness `4aae43e` uses `fileURLToPath` and a portable Node distiller stub. The
wrapper preserves full failure output, and no Windows coverage was skipped. Local source
and consumer gates passed. Python hosted Windows, Ubuntu and integration jobs all
[passed on the final commit](https://github.com/jchen1707/python-harness/actions/runs/34361832478).

Frontend Windows also exposed the outer Vitest test's five-second default timeout
while running the entire shared suite. Commit `fed64d9` aligns that one wrapper with
the existing 300-second subprocess budget, with five seconds for reporting, and
preserves full failure output. Local gates and the actual pre-push hook passed.
The final hosted Windows, Ubuntu, e2e and Lighthouse jobs
[passed](https://github.com/jchen1707/frontend-harness/actions/runs/34362162488).
The PR-body and generation checks also passed.

Shared harness cross-stack, submodule and generation checks passed; Go gates and
generation passed. Only the three consumer freshness checks remain red because the
shared source PR has not landed. Authenticated Claude acceptance still requires host
login renewal; these passing checks do not replace that runtime measurement.

Factory publication validation passed Ruff lint, Ruff format, mypy and pytest after the
Git-environment repair ([gate report](acceptance/publication-gates-2026-09-09.json)).
The final source update changes only the portable test fixture; the final vendored
shared/delivery suite passed all 172 tests with no skips. Vendor integrity and changed
local documentation links passed.

For implementation evidence and the remaining authentication check, see the
[current handoff](handoff-ui-learning-docs.md) and
[native learning acceptance](acceptance/native-learning-completion-2026-09-09.md).
