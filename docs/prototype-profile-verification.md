# Prototype profile maintenance verification

Implemented locally on 2026-09-10. No live BAC-56 run, state, captured authority, sidecar,
credential, candidate backup, or artifact was modified. No push, merge, deployment, daemon
start/restart, Factory configuration write, or BAC-56 lifecycle command was performed.

## Source and ownership

- Shared contract: `/Users/james/harness-prototype-review`, branch
  `fix/prototype-review-scheduling`, based on `harness@v2` (`794a3a5`). Commit
  `c1b2b85e0a3539622680a5545a0c43767a01e6af`.
- Product: `/Users/james/nexus-core/nemoclaw-prototype-maintenance`, branch
  `fix/prototype-delivery-review`, commit `ef3fc582d03ad29ef71f65e653a50752cfc37547`,
  based on the existing `origin/james/feat/prototype` ref `02c6681`.
- Factory: `/Users/james/factory-prototype-maintenance`, branch
  `fix/prototype-review-policy`, based on the authoritative local Factory HEAD `25cf383`.
  The same maintenance changes are present in `/Users/james/factory` for inspection.

The original product checkout remains on `james/feat/prototype` at `29384fc`, 53 commits behind
its existing remote-tracking ref, with this maintenance's config/README/vendor changes present
as uncommitted work. Its original untracked human files remain. The original harness branch
and Factory branch remain unchanged. The product config was identical across the 53-commit
gap; the standalone product maintenance commit is based on the newer lineage, not the old
candidate. No live `.factory/worktrees` were used as maintenance targets.

Both consumers were synced using:

```sh
python3 /Users/james/harness-prototype-review/scripts/vendor_sync.py sync \
  --harness /Users/james/harness-prototype-review --target CONSUMER
```

Both now pin `c1b2b85e0a3539622680a5545a0c43767a01e6af`. Prior pins were `b77e861` in
NemoClaw and `4aae43e` in Factory. Manifest contents were verified intact before sync;
post-sync integrity checks passed. The new pin is a local, unpublished source commit.
NemoClaw's larger generated diff includes intervening v2 delivery-policy support needed for
snapshot resolution. No generated file was hand-edited.

## Actual policy and selected axes

`delivery.default` is `prototype`. Only `delivery.profiles.prototype` declares
`reviewAxes: ["standards", "spec"]`. That resolves into the effective policy and bounds
Factory's automatic plan to two independent reviewers, with `tier2: "profile-axes"`.
Core and Hardening omit `reviewAxes` and keep the previous risk-triggered full suite.
An explicit `--full-review` still overrides the profile's narrowing.

Prototype requires functional acceptance, mandatory correctness, mandatory safety,
`bac56-local-release-core`, and all four gates (`ruff check`, `ruff format --check`, `mypy`,
`pytest`). It has one explicit engineering deferral: `subprocess-output-bounds`, assigned
to BAC-60, with no BAC-56 live-execution claim. General production readiness is not a
Prototype requirement. Core requires the deferred engineering item; Hardening also requires
production readiness. Existing ticket-specific applicability remains in the requirement text.

The source-owned `bac56-local-release-core` condition records James's decisions: trusted local
source validation; actual launched application/runtime identity; acquiring and holding the
shared lock across preparation; same-version executable replacement digest tests; and one
trusted shared lock identity. Git source-integrity checks stay credential-free and proxy-free.
No demo VM GitLab credential prerequisite, Secret Manager work, bridge, deployment, live demo,
or candidate import belongs to this work.

A fresh context snapshots this declaration using the existing `authority.snapshot` path.
The builder prompt now includes that validated snapshot's effective requirements and explicit
deferrals. Review selection also reads the captured policy. Candidate policy edits cannot
change either. Old run-bound disposition JSON is neither reused nor edited.

## Verification

| Checkout / command | Result |
| --- | --- |
| Harness: `python3 scripts/check.py` | All checks passed |
| Harness: delivery-policy Node tests | 9 passed |
| Harness: config-contract tests | 8 passed |
| Factory: `uv run ruff check .` | Passed |
| Factory: `uv run ruff format --check .` | Passed; 259 files formatted |
| Factory: `uv run mypy` | Passed; 201 source files |
| Factory: `uv run pytest` | 1,460 passed, 5 skipped, 5 failed in 376.03s |
| Factory: final profile/prompt regression selection | 6 passed |
| Real NemoClaw config, isolated snapshot/builder/reviewer fixture | Passed |
| Original NemoClaw: all four declared gates | Passed; 32 tests, 15 mypy source files |
| Newer product lineage: `uv sync --frozen` | Passed |
| Newer product lineage: lint / mypy / pytest | Passed; 131 tests, 32 mypy source files |
| Newer product lineage: format check | Failed on two untouched baseline files |

The final profile regressions use the actual shared review-axis manifest. They prove a large
diff produces two Prototype axes, eight Core/Hardening/default axes, and eight axes under
explicit full review. A candidate change cannot alter the captured selection. A separate
builder test proves captured requirements and deferrals reach the fresh prompt.
The actual product-config fixture additionally checks every BAC-56 core direction and all four
required gates in the generated builder prompt, and records the two-axis review plan. These
checks use fake model/tracker adapters and temporary SQLite/git fixtures, not a live worker.

All five Factory failures reproduce against untouched Factory HEAD `25cf383`:

- `tests/integration/test_candidate_handoff.py::test_retained_candidate_requires_cancel_then_restores_exact_tree_and_prompt`:
  `illegal-transition: approved -> worktree_ready`.
- `tests/integration/test_console.py::test_intermediate_context_is_shown_without_waiting_for_a_completed_turn`:
  `KeyError: gpt-6-astra` in the test's model catalogue.
- `tests/unit/test_routing.py::{test_the_shipped_table_validates,test_the_shipped_table_records_all_eight_measured_models,test_the_context_denominator_uses_context_window_not_the_larger_figure}`:
  configured `gpt-6-astra` is absent from the tests' supplied catalogue.

The newer product format failures are `src/app/demo_run/gitlab.py` and
`tests/test_gitlab_adapter.py`. Their bytes match `02c6681` exactly. They were not reformatted.

The pre-existing candidate-handoff implementation remains unchanged. Without
`--candidate-from-run`, `cmd_run` does not validate/copy an old candidate; `restore` returns
without a retained candidate directory and `prompt_section` returns without a restored receipt.
The ordinary fresh fixture confirms no candidate prompt or retained candidate directory.
The failing candidate-specific baseline test is not hidden or repaired by this maintenance.

## Supported activation and fresh-run procedure — not executed

The orchestrator first independently reviews these commits and integrates the product commit
into the intended `james/feat/prototype` source used for fresh worktrees. Publish/integrate the
shared pin through its owning v2 workflow. Reconcile the older registered product checkout's
maintenance edits while preserving its human files; do not reset it. Ensure the registered
product policy and the fresh base carry the reviewed declaration/vendor pin, and the existing
Factory process has loaded the reviewed Factory source. Do not create a second daemon.

The orchestrator owns preservation and cancellation of old run `7f369db1702e482e`, and resolving
any intake conditions before a fresh run. The commands for the new selection/start are:

```sh
cd /Users/james/factory
uv run factory configure --project nemoclaw-dev --delivery-profile prototype
uv run factory run BAC-56 --check
uv run factory run BAC-56 --no-follow
```

Do not add `--full-review` or `--candidate-from-run`. The profile is selected with `configure`;
`factory run` itself has no `--delivery-profile` option. Do not replace the old run's captured
policy as a shortcut. In the new run, verify the captured profile/requirements/deferral and
builder prompt first, then `review-plan.json` must show exactly Standards and Specification.
Code and local configuration being present do not establish that a live run uses this policy.
