# Dark console parity acceptance

Status: implementation, fixture acceptance and independent review complete. Live rollout pending.

This record covers the seven-view design repair following the September 9 live/prototype
audit. It supplements, rather than replaces, the earlier
[production browser evidence](../production-validation/README.md). The approved visual
reference remains the [dark prototype](../dark/runs.html).

## What changed

The console restores the prototype's panel hierarchy, compact five-column runs board,
expandable evidence, current-attempt summary, project inventory, admission-first settings,
and runtime/configuration panels. Full operational details remain available through named
disclosures and bounded, keyboard-accessible evidence regions.

The runtime inventory reads the measured `sbx` status/workspaces schema and distinguishes
an empty successful inventory from unavailable collection. Runtime associations use recorded
identity; missing compatibility evidence is not a certification success. Templates and CSS
are loaded together per app instance, with a visible rendering fingerprint and start time.

## Intentional differences from fixture artwork

- The board shows context **availability**, not an average occupancy across unrelated runs.
  A percentage belongs to a specific current run/role, with freshness and source.
- Token and estimated-cost cards state their scope and evidence completeness. Missing
  observations are unavailable; a partial estimate is a known lower bound. Estimates are
  API-equivalent USD, not account charges.
- Global navigation has four destinations. Run details, timeline and settings require a
  selected ticket and appear as current-run navigation.
- No fixture state selector, fabricated global capacity denominator, unconditional live
  indicator, fake delivery link or task-completion interpretation of a context gauge.
- Historical review disposition and certificate validity are shown only when recorded by
  their owning layer; age alone does not establish expiry or a successful re-review.

## Verification scope

The browser suite uses a temporary Factory home, SQLite database, fake tracker/sandbox and
sanitized runtime fixtures. All control writes target that fixture server. Browser requests
to other HTTP origins are refused. No live Factory database, vault or sandbox is mutated.
The history-heavy fixture covers multiple projects, ten board runs, more than fifty
invocations and long child/certification identities. It reproduces failures that the older
single-run fixture did not expose.

The suite checks desktop and narrow layouts, primary-cell visibility (not merely document
overflow), panel order, accessibility, 320px form/evidence overflow, settings persistence,
and real Store-to-SSE disclosure/focus continuity. Tail updates append to a fixture file;
manual scrolling and opt-in following are measured in the actual browser.

## Browser results and visual review

The final browser run passed all **70 pages**: seven views, five states (populated, empty,
blocked, approval-waiting and unavailable), at desktop and narrow widths. The reports contain
zero assertion failures or accessibility violations. Fourteen additional reference captures
show the original prototype with matched primary fixture fields; other prototype content
remains illustrative, so these are composition references rather than pixel baselines.

Manual screenshot review checked all seven view compositions and caught narrow Projects
columns that overflow checks alone missed. Projects and Runtimes now stack labeled primary
fields on small screens; browser assertions require readable cell widths and visible labels.
The final narrow screenshots were inspected after that correction. Independent data and UI
reviews closed their findings on context scope, partial costs, runtime evidence and SSE
focus/scroll retention.

| View | Desktop | Narrow | Prototype reference |
| --- | --- | --- | --- |
| Runs | [Screenshot](populated-runs-desktop.png) | [Screenshot](populated-runs-narrow.png) | [Reference](reference-runs-desktop.png) |
| Projects | [Screenshot](populated-projects-desktop.png) | [Screenshot](populated-projects-narrow.png) | [Reference](reference-projects-desktop.png) |
| Run details | [Screenshot](populated-detail-desktop.png) | [Screenshot](populated-detail-narrow.png) | [Reference](reference-detail-desktop.png) |
| Timeline | [Screenshot](populated-timeline-desktop.png) | [Screenshot](populated-timeline-narrow.png) | [Reference](reference-timeline-desktop.png) |
| Run settings | [Screenshot](populated-settings-desktop.png) | [Screenshot](populated-settings-narrow.png) | [Reference](reference-settings-desktop.png) |
| Runtimes | [Screenshot](populated-runtimes-desktop.png) | [Screenshot](populated-runtimes-narrow.png) | [Reference](reference-runtimes-desktop.png) |
| Configuration | [Screenshot](populated-config-desktop.png) | [Screenshot](populated-config-narrow.png) | [Reference](reference-config-desktop.png) |

Machine-readable results: [populated](populated.json), [empty](empty.json),
[blocked](blocked.json), [approval-waiting](approval.json), [unavailable](unavailable.json).
The [baseline regressions](before-regressions.json) record failures observed before the fix.

## Repository gate results

The [final gate report](gates.json) records Ruff lint, Ruff format, mypy and pytest passing
with exit 0. The report's absolute checkout path is sanitized; successful gate output is
empty because the reporter retains output tails only for failures. Local document links
and `git diff --check` also passed.

The mypy caveat about unchecked new top-level paths does not apply: Python changes remain
in existing checked source/test paths. JavaScript is exercised by the separate browser run.
The pytest caveat does apply: fake adapters prove projection/control logic, not external
runtime execution. The five browser scenarios skipped by ordinary pytest were explicitly
run with Chrome as documented above. No new runtime certification or live deployment is
claimed by this UI validation.

## Reproduce

From the implementation checkout with dependencies synchronized:

```sh
FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules FACTORY_BROWSER_OUTPUT="$PWD/docs/ui-alternatives/parity-validation" uv run pytest tests/integration/test_console_browser.py
node .agents/vendor/harness/hooks/gate_report.mjs --force --json
```

Browser-only dependencies are Playwright 1.63.0 and axe-core/playwright 4.13.0, installed
outside the repository. The runner uses locally installed Chrome. Ordinary pytest skips
the browser suite when `FACTORY_BROWSER_MODULES` is absent; its explicit run is separate
evidence. The required Python gates do not type-check browser JavaScript.

## Rollout boundary

Fixture acceptance establishes the reviewed UI's behavior, not that a running service
has loaded it. Follow [console operation and revision checks](../../operator-reference.md#console-operation-and-revision-checks)
after an approved update. A separate live read-only smoke check must confirm the new
app start/fingerprint and actual inventory. Do not restart the scheduler or sandboxes for
UI deployment, and do not publish the local live-data planning screenshots.
