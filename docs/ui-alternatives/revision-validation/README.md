# Dark console revisions — evidence for visual review

Status: implementation revised; James's visual acceptance is pending. This record supersedes
PR #99's visual-completion claim. The [revision handoff](../../handoff-console-visual-revisions.md)
remains the account of the defects that prompted this work.

Open the [side-by-side review page](index.html) to compare all seven views at desktop and
narrow widths. Images are synthetic fixtures, not a capture of the deployed service.
The original proposal remains unchanged.

## What changed

| View | Revision | Visual review finding |
| --- | --- | --- |
| Runs | Compact five-column rows, paired mobile values, semantic states, recorded attention reasons and destinations; secondary evidence stays expandable. | Work is easier to scan; long project identities expand on keyboard focus. |
| Detail | Retained-evidence rail, prominent current attempt, correct context progress, state-appropriate actions, combined verification/review, closed logs/history. | Current work and evidence take priority over metadata and logs. Narrow review columns retain readable findings. |
| Settings | Explicit admission states, approval bound to the pending identity, active/waiting invocations before settings/history, clear labels and inheritance hints. | Removes the contradictory empty approval form and exposes current work despite large child histories. |
| Projects | First/selected editor open, keyboard selection focuses its summary, capacity and effective defaults visible, history secondary. | Longer than the formerly closed-only page because the selected editor is now visible. Mobile inventory uses paired values. |
| Timeline | Measured waterfall first, compact metadata, chronological preview and agent activity in paired supporting panels, full transition evidence expandable. | Real tools and transitions remain populated; wide timing/tool evidence has keyboard-accessible horizontal scrolling on narrow screens. |
| Runtimes | Ownership in scan rows, compact missing-layout details, historical compatibility outcome before full payload. | Retained reports remain distinct from current identity certification; operator-owned sessions have no controls. |
| Configuration | Full-width model fields, grouped budget/save action, subordinate sources, role-level validation messages, paired model/effort fields on mobile. | Editing remains prominent and labels no longer wrap into narrow character fragments. |

The screenshot review caught and repaired a fractional-context/percentage mismatch in the
new Detail progress bar. A regression asserts that 0.46 renders as 46%, with a progress
value of 46 rather than 0.46. Other behavior tests cover stale and duplicate approval,
automatic/no-pending states, policy replacement after a concurrent state change, control
eligibility, selection focus, and partial-cost lower bounds.

## Evidence tracks and limits

- `original-*`: unchanged original prototype, with its original illustrative data and
  fixture controls. This is the visual design reference, not a matching-data baseline.
- `matched-*`: a separate comparison renderer using the committed fixture manifest and
  original composition. Real timing, tools, gates, findings and artifacts replace the
  prototype's invented data. This reconstruction is not itself approved production UI.
- `before-*`: the previous implementation at `ba9ca3c`, using the same fixture generator.
- `populated-*`, `stress-*` and other scenario files: revised implementation captures.
  `*-first.png` records the first viewport; other baseline captures are full-page.
  `*-expanded.png`, `*-saved.png` and `*-invalid.png` document interactions separately.

Normal and stress fixtures are separate: normal uses three runs and representative evidence;
stress retains ten runs, long identities and over fifty invocation records. Each viewport
gets a fresh database, app and fixture home. Time and generated IDs are fixed. Baseline
screenshots precede mutation tests. Manifests record the fixture values and source identity.
Temporary fixture paths are normalized; no credentials or live operational screenshots are
published.

The original narrow prototype clips some primary table columns. The revised mobile layout
intentionally preserves readable values instead of reproducing that clipping. Four global
routes plus per-ticket navigation remain intentional. Real role keys, settings fields,
recorded evidence and policy eligibility take precedence over invented prototype values.
No pixel-equality claim is made.

## Reproduce

Use installed Chrome and an external directory containing `playwright` and
`@axe-core/playwright`; the measurement packages are not production dependencies.

```sh
FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules \
FACTORY_BROWSER_OUTPUT=/tmp/factory-console-review \
uv run pytest tests/integration/test_console_browser.py

FACTORY_BROWSER_MODULES=/tmp/factory-ui-browser/node_modules \
node tests/browser/console-reference.mjs "$PWD" /tmp/factory-console-review /tmp/factory-console-review

node .agents/vendor/harness/hooks/gate_report.mjs --force --json
```

The browser matrix checks six scenarios × seven views × two viewports (1440×1000 and
390×844), plus 320px stress overflow, keyboard navigation, project selection, settings
save/validation, real Store→SSE updates, disclosure/focus/scroll preservation and opt-in
log following. It uses an isolated local app; all nonlocal requests are blocked.

Passing HTTP, accessibility and functional assertions do not establish visual approval.
Repository tests also use fake adapters: they do not certify live `sbx`, Codex, GitHub or
Linear behavior. No new runtime integration claim or live rollout is part of this UI change.

## Review and rollout

Review the comparison page, then the relevant expanded/error-state captures. James decides
whether to accept the visual result and merges the PR. A live console rollout remains a
separate operator step: identify its process, source and asset fingerprint before restarting
it, then inspect the seven live views. This revision did not restart the console, scheduler
or any sandbox.

## Recorded checks and measurements

Implementation and capture harness: `90a832222baa5033e0203346b99fe005f5fae342`.
The before implementation is `ba9ca3c`, exercised with that same capture harness.
All before/after normal and stress fixture manifests match semantically after source metadata
is excluded. Desktop/narrow fixtures match after viewport metadata is excluded.

[Browser summary](summary.json): **12 cases, 84 pages, zero failures** in Chrome 152.0.7977.83.
All baseline and expanded accessibility scans reported zero violations.

| Matched Runs fixture | Before height | Revised height | Collapsed row heights before → revised |
| --- | ---: | ---: | --- |
| populated · desktop | 1161px | 1048px | 105–152px → 69–109px |
| populated · narrow | 2784px | 2175px | 356–393px → 206–247px |
| stress · desktop | 1896px | 1540px | 105–152px → 69–109px |
| stress · narrow | 5459px | 3834px | 356–393px → 206–247px |

Narrow Work in progress remains below the first viewport; the attention heading is now
visible there. Height is a density measurement, not the visual acceptance criterion.
Project pages intentionally grow because their selected editor is open. Configuration
uses additional vertical space on narrow screens to preserve readable model/effort labels.

Manual review covered the seven populated views at both widths, the stress Runs composition,
and representative blocked Detail, approval Settings, unavailable Runtimes and empty Runs.
It inspected hierarchy, density, typography, alignment and navigation; automated checks cover
actions, empty/error route behavior and expanded evidence across the matrix. This is not a
claim that every one of the 84 pages received a separate manual review. James’s verdict for
every view remains pending.


[Canonical gate report](gates.json), run on the same source commit:

| Gate | Status | Exit | Output tail |
| --- | --- | ---: | --- |
| Ruff lint | pass | 0 | Empty on success |
| Ruff format check | pass | 0 | Empty on success |
| mypy | pass | 0 | Empty on success |
| pytest | pass | 0 | Empty on success |

No declared default gate was skipped. The mypy path-discovery caveat does not exclude
these changes: all changed Python files are within the configured source/test paths.
The fake-adapter pytest caveat does apply; the separate real-Chrome fixture matrix proves
browser behavior, not a production runtime. Browser tests skip in the default pytest run
unless the explicit external-browser environment is set; they ran separately here.

[Fixture equality checks](fixture-validation.json) and [capture hashes](capture-hashes.json)
record the comparison provenance. The comparison viewer was exercised through all 84
view/layout/fixture/reference combinations; both images loaded and no script errors occurred.
Local links and diff whitespace checks pass.
