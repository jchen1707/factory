# Documentation refresh verification — 2026-09-08

Historical verification record for this documentation change. No production services,
tracker state, runtime configuration or database were changed.

The README now links a detailed operator reference retaining its prior operational
content. The runbook corrects database reconstructibility, the seven console views,
context/accounting semantics, fixed historical retry-count advice and stale gate-bypass
instructions. Only the two Phase 2 handoffs with an explicitly satisfied retirement
condition were archived; forwarding documents preserve inbound links. Discovery records,
acceptance records, the canonical specification and untracked work were not rewritten.

Validation:

- Checked 119 local links across the README, index, workflow reference, operator reference,
  runbook and forwarding documents: all targets exist. Historical evidence bodies were
  preserved; this is not a claim that every historical external URL still resolves.
- Parsed 28 documented command forms with `factory.cli.build_parser().parse_args`, including
  configuration, approvals, delegation, recovery, cancellation, metrics and migration:
  all accepted. Parsing did not dispatch commands or perform their effects.
- Matched all seven console view routes to `src/factory/console/app.py`.
- Rendered all nine Mermaid sources in `workflows.md` using Mermaid in headless installed
  Chrome through Playwright; every render succeeded. The checked-in SVGs are the rendered
  artifacts, not placeholders. SVGs expose diagram titles/labels as text.

Reproduction: extract each fenced Mermaid block from `workflows.md`, pass it to
`mermaid.render`, and write the returned SVG to its adjacent `docs/diagrams/` image path.
Rendering used temporary npm dependencies outside the repository and the installed Chrome;
no new application dependency was added.
- Compared both archived handoff bodies with their original tracked files: byte-for-byte
  identical after the added historical status banner. `git diff --check` passed.

Declared gates ran via `node .agents/vendor/harness/hooks/gate_report.mjs --force --json`:

| Gate | Exit | Status | Duration |
| --- | --- | --- | --- |
| Ruff lint | 0 | pass | 819 ms |
| Ruff format check | 0 | pass | 89 ms |
| mypy | 0 | pass | 4,556 ms |
| pytest | 0 | pass | 246,766 ms |

The runner returned `"verdict": "pass"`; all four gates ran and none were skipped.
Its `outputTail` fields were empty, so no test count is inferred.
[Raw report](documentation-refresh-gates.json) retains the actual result and caveats.
The mypy path caveat does not affect this documentation-only change. The pytest caveat
applies: fake-adapter tests do not establish external runtime behavior or documentation
rendering; the separate parser, link and browser-render observations above cover the
changed documentation surfaces.
