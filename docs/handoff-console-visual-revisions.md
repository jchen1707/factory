# Handoff: revise the dark operations console

Date: 2026-09-09. Status: evidence reviewed; revisions required, not implemented.

James reviewed the result of PR #99 and requested a revision handoff because the
implementation still falls materially short of the proposed dark UI. The previous
acceptance record overstated visual completion. Its passing functional, accessibility
and repository checks remain evidence of those checks; they do not establish design parity
or James's acceptance.

## Resume here

- PR #99 is merged: implementation commit `126abb8`, merge commit `ba9ca3c` on `main`.
  Fetch and inspect current `origin/main` before creating a revision worktree; do not build
  on the obsolete pre-#99 base or reopen the merged PR.
- This documentation branch is `docs/console-revision-handoff`, in
  `/Users/james/factory-console-design-parity`. It contains no application changes.
- The approved design is [dark prototype](ui-alternatives/dark/runs.html), driven by
  [prototype.js](ui-alternatives/prototype.js) and [prototype.css](ui-alternatives/prototype.css).
  Preserve those original files as historical design references.
- The earlier approved implementation plan and test plan remain local at
  `.agents/plans/fix-console-design-parity/`. Their `evidence/` directory contains live
  operational screenshots and must remain untracked. This handoff is self-contained and
  links only sanitized committed evidence.
- Do not restart/deploy the console, scheduler or sandboxes as part of this handoff.
  This audit did not inspect a fresh live service or infer what port 7717 currently serves.
  The user may have deployed since the previous turn. Verify process/start/fingerprint
  read-only before making any later live-versus-branch claim.

## Evidence reviewed and its limits

Compared all seven committed desktop implementation/reference screenshot pairs, plus
Runs narrow reference/implementation and Settings narrow implementation. Inspected
reference generation, screenshot ordering, the renderer and relevant CSS. Mobile Projects
and Runtimes had already been inspected during the preceding implementation; this pass
does not claim a fresh complete 70-page manual review.

The [70-page browser report](ui-alternatives/parity-validation/README.md) proves its
assertions ran. It checks reachability, overflow, selected layout properties, accessibility
and fixture interactions. It does not compare the visual design comprehensively.

| View | Proposed composition reference | Implemented desktop | Observed difference |
| --- | --- | --- | --- |
| Runs | [Reference](ui-alternatives/parity-validation/reference-runs-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-runs-desktop.png) | Multi-line diagnostic rows dominate the page; attention items repeat raw states. |
| Projects | [Reference](ui-alternatives/parity-validation/reference-projects-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-projects-desktop.png) | Proposed selected-project editor becomes three closed disclosure bars; fixed-width inventory wastes space and wraps identities. |
| Details | [Reference](ui-alternatives/parity-validation/reference-detail-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-detail-desktop.png) | Narrow retained-evidence rail and dominant attempt/review area are replaced by a wide metadata panel and prominent live log. |
| Timeline | [Reference](ui-alternatives/parity-validation/reference-timeline-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-timeline-desktop.png) | Full-width stacked activity, waterfall, empty tools panel and transition table replace the proposed waterfall-first composition and paired lower panels. |
| Settings | [Reference](ui-alternatives/parity-validation/reference-settings-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-settings-desktop.png) | Technical form copy, contradictory empty admission/action presentation, and current invocation buried behind retained children. |
| Runtimes | [Reference](ui-alternatives/parity-validation/reference-runtimes-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-runtimes-desktop.png) | Primary ownership signal is absent; compatibility summary is a sandbox-name disclosure with little visible result. |
| Configuration | [Reference](ui-alternatives/parity-validation/reference-config-desktop.png) | [Implementation](ui-alternatives/parity-validation/populated-config-desktop.png) | Sidebar displaces the full-width editor; small intrinsic-width inputs float inside large table cells. |

PNG dimensions measured directly from committed file headers (width is 1440 desktop,
390 narrow): Runs desktop reference/implementation heights **1178/1896**, Runs narrow
**1855/5403**, Timeline desktop **1046/1698**, Settings desktop **1504/2190**. These indicate
composition/density problems, not exact parity targets: content is only partly matched,
and the narrow prototype itself clips primary table columns. Do not reproduce that defect.

## Prioritized revision backlog

### R1 — P1: compact, actionable Runs board

Observed: every run carries a separate padded Run signals disclosure, repeated estimate
qualifiers/ceiling, and an aggressively wrapped project name. The attention column repeats
`blocked` or `awaiting_human` as badge and explanation. State chips in the work table use
the same green treatment for implementing, blocked and awaiting human. The narrow board is
5403px tall for ten runs, with five vertically stacked label/value fields per run.

Revise the row composition, not just font size. Keep ticket/title and state prominent;
use compact project identity with a keyboard-accessible full value, concise estimate and
one secondary-details affordance. Put repeated units and scope at column/panel level while
retaining per-row incompleteness. Use human-readable state labels and distinct semantic
styles. Attention should show the recorded reason and a relevant destination (run, existing
approval, or recorded PR); where reason is absent say so once, never invent one. Make the
mobile run summary compact while retaining all five primary values and accessible evidence.

Acceptance: paired normal/stress fixtures; measure collapsed row heights and first-viewport
information before/after. All five desktop columns fit and narrow values remain readable.
James must accept the density comparison; a shorter page obtained by clipping data fails.
Real SSE disclosure/focus retention must continue passing.

Entry points: `_board_table`, `_estimate_cell`, board attention rendering in
[src/factory/console/app.py](../src/factory/console/app.py), `board.html`, `.board-table`,
`.operations-grid`, `.attention-item`, `.chip` and `.ops-strip` in
[console.css](../src/factory/console/templates/console.css).

### R2 — P1: restore the detail-page hierarchy

Observed: the proposed narrow evidence rail is absent. Branch/heartbeat metadata and a
policy-internals warning dominate Current attempt; a large live tail is open by default;
Verification and Review are separate large empty cards. Controls all have equal primary
visual weight. Run title/navigation hierarchy differs across detail/timeline/settings.

Restore a compact retained-evidence rail and a dominant current-attempt area, with a coherent
verification/review summary below it. Keep actual missing evidence explicit, compact and
visually subordinate. Move full logs and long identities to intentional drill-downs; preserve
bounded scrolling and opt-in following. Replace implementation-oriented copy such as
`actor="human"` with concise operator consequences. Style destructive/secondary controls
distinctly, and derive availability from existing policy/state behavior without duplicating
the transition policy in presentation code. Do not add action capabilities.

Acceptance: inspect active, blocked, awaiting-human and missing-evidence states. A first-view
reader can identify ticket, current activity/state, evidence status and available next action.
Current attempt remains before history in accessible reading order; logs and metadata do
not dominate the default view. Test allowed/refused controls through existing endpoints.

Entry points: detail renderer in `app.py`, `run_detail.html`, `controls.html`, `.detail-grid`.

### R3 — P1: settings as an operator workflow

Observed: “No invocation awaiting admission” appears alongside an empty editable Next
invocation ID and a primary “Approve next attempt” button. Policy replacement is presented
while the populated run is implementing. This is a misleading affordance, not evidence that
the backend permits either action. Recent-invocation rendering takes `invocations[-3:]`,
which shows retained child attempts 53–55 while the active builder is hidden in Earlier
invocations. Labels expose `max active agents`, `false`, and mixed capitalization.

Present admission as an explicit state: no pending invocation, automatic launch pending,
awaiting approval, or already approved. Bind an actionable approval to the actual pending
identity; do not require routine manual ID entry. Surface policy-replacement eligibility and
reason from existing authority. Put active/waiting invocation summaries before chronological
history, independent of insertion order. Group human-readable fields with effective value,
override and inheritance explained together. Preserve posted field names and enum values.

Acceptance: all four admission cases plus stale/invalid submission and disallowed policy
replacement. Active builder/reviewer remain visible with 50+ retained children. Desktop and
narrow screenshots use the same effective settings. All existing validation/save/reload
semantics remain tested.

Entry points: `_settings_form`, `_invocation_cards`, `run_settings` in `app.py`; existing
operator-controls/runtime-store interfaces own eligibility and effective values.

### R4 — P1: repair the comparison and acceptance method

Observed in [console-reference.mjs](../tests/browser/console-reference.mjs): reference
adaptation replaces timeline waterfall contents, empties tool rows, and substitutes generic
text into every `details p`. Project limits and form/model values are not fully matched.
These captures cannot establish parity of populated workflow/evidence presentation.
In [console.mjs](../tests/browser/console.mjs), each viewport's screenshots precede a settings
save to approval mode, but both viewports share the same fixture server. Therefore desktop
Settings shows automatic while narrow shows approval. Clock/elapsed values also vary.

Create two explicit evidence tracks: (1) unchanged original design captures, labeled
illustrative; (2) composition comparisons using the same deterministic data/state in both
renderers. Reset/isolate the fixture for each viewport and separate interaction mutations
from baseline capture. Preserve real-shaped populated gates, ranked review findings, tool
calls, waiting approvals, current/historical certificates and artifacts; ten rows plus mostly
missing evidence does not exercise these panels. Keep a separate stress fixture with long
names and large histories. Do not erase the proposed waterfall to make data fit.

Acceptance: reproducible fixture manifest and capture environment; full-page plus first-
viewport images at 1440 and 390, stress checks at 320. Record per-view verdicts for hierarchy,
density, typography, alignment, navigation, actions, empty/error states and expanded evidence.
Use layout/region checks for agreed invariants and human side-by-side review for visual
quality. Do not use axe/HTTP 200/no overflow as a visual approval proxy. Preserve old reports
as historical evidence and obtain James's visual acceptance before calling parity complete.

### R5 — P2: Projects selected editor and information widths

Observed: all defaults are closed in the default screenshot, unlike the proposed visible
selected-project editor. A fixed-layout five-column table allocates identical-sized areas
regardless of content, wrapping project names while numeric cells have spare space.

Keep the scalable multi-project inventory, but make selected-project editing evident: a
clear selection/anchor opens the corresponding grouped editor with a visible selection
state and meaningful effective defaults. Put runtime history behind secondary details.
Tune column widths to content; preserve readable mobile labels without turning every short
value into an unnecessarily tall block. Show inheritance/source where it affects decisions.

Acceptance: default, selected, keyboard-selected, saved and invalid editor captures; project
selection must reveal/focus the right editor. No fields disappear and no registry-only field
becomes editable. Existing collapsed defaults were an earlier approved scalability choice;
record this revised interaction explicitly rather than pretending the old code omitted forms.

### R6 — P2: Timeline composition and real activity evidence

Move the measured waterfall into the leading content position, with agent/tool activity and
execution history arranged as coherent supporting panels on desktop and sensibly ordered
on mobile. Replace the long inline state/cost sentence with readable labeled metadata.
Keep actual actor/rule/time evidence available; a compact chronological presentation can
expand to the full transition table. Avoid a large empty tool section when no calls exist.

Acceptance: real-shaped fixture with multiple attempts, completed/active roles and timed
calls; unknown tool durations remain unknown. Verify collapsed/expanded history, measured
waterfall scale and SSE focus/horizontal-scroll preservation. Do not copy invented timing
or the adapter's stripped-down reference timeline.

Entry points: `_timeline_head`, `_timeline_html`, `run_timeline_view` and related helpers
in `app.py`, plus `run_timeline.html`.

### R7 — P2: Configuration and Runtime finishing

Configuration: restore the proposed editor's visual emphasis and usable model-field width;
place explanatory sources below or otherwise subordinate to editing. Align model/effort
controls, use meaningful Save copy, and show validation near affected fields. Preserve
actual configured role keys and supported values rather than copying fictional prototype
roles/models. No hard-coded model catalog solely to obtain a dropdown.

Runtimes: include ownership alongside state in the scan view, compact unavailable reasons
into accessible secondary evidence, and show a useful compatibility summary before full
payload details. Current certification versus historical recorded evidence must remain
explicit. Inspect a populated mix of factory/operator-owned, active/stopped, bound/clone,
unknown/failed/historical-pass rows. No controls for `codex-*` sessions.

Acceptance: full-width readable editor inputs, aligned labels/actions, useful runtime scan
summaries, keyboard access to complete evidence and unchanged configuration POST semantics.
Entry points: config/runtimes renderers, templates, `.inventory-table` and form/table CSS.

### R8 — P2: shared visual consistency

Normalize page-title/subtitle/run-tab placement, panel spacing, border nesting, readable
label vocabulary and primary/secondary button treatment. The existing append-only CSS
composition overrides and global `details`/form/table rules make local components inherit
unintended padding and borders. Consolidate component rules while preserving keyboard focus.
Use the original dark spacing/typography tokens as the baseline; document necessary deviations.
Keep four global destinations plus ticket-specific navigation (an intentional accepted
routing decision). Do not copy fixture state controls or add orphan global run links.

## Constraints to preserve

This is a layer-D presentation repair, not a factory-spec redesign. Keep the measured sbx
schema/failure handling, recorded runtime associations, immutable per-app assets/start marker,
context occupancy versus cumulative usage, known partial cost lower bounds, and explicit
missing evidence introduced in #99. Preserve note/vault work and runtime certification work.
No new API/database migration, vendor edit, tracker write, merge capability, trust-store edit,
or sandbox credential/control expansion is justified by visual fidelity.

## Execution sequence for the next agent

1. Read AGENTS/spec and this handoff; inspect current main and live identity read-only if
   comparing the service. Create a fresh `fix/console-visual-revisions` worktree from main.
2. Fix deterministic visual fixtures/comparison capture (R4) and capture the current baseline
   before changing UI. Establish populated and stress examples per view.
3. Resolve shared shell/components, then Runs, Detail and Settings (R1–R3/R8). Review those
   screenshots against the original design before propagating choices to remaining views.
4. Finish Projects, Timeline, Configuration and Runtimes (R5–R7). Record every deliberate
   departure and whether it is necessary for truthful data or merely a design choice.
5. Preserve/add focused behavior regressions; rerun the browser matrix, expanded evidence,
   keyboard/SSE checks and the canonical gates:
   `node .agents/vendor/harness/hooks/gate_report.mjs --force --json`.
   Browser command/environment are in the existing acceptance record.
6. Publish a revision evidence index with exact source commit and fixture manifest. Report
   functional acceptance and visual acceptance separately. James's review is outstanding.
7. Commit reviewable revisions and prepare the PR under the user's publication authorization.
   James merges. Live console-only rollout and read-only seven-route smoke remain a separate
   explicit operator step; identify the actual service before restarting it.

## Handoff verification

Documentation-only handoff. Compared the evidence and inspected the source paths above;
checked local Markdown links and diff whitespace. No application gates were rerun and no
new claim is made about live behavior. The prior gate/browser results remain historical.
