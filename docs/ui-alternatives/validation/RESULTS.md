# Prototype browser evidence

Measured 2026-09-08 using local Google Chrome through Playwright 1.63.0 and axe-core 4.13.0. All 21 routes loaded at 1440 × 1000 and 390 × 1000. All five fixture states were exercised on every route at both widths: **210 checks, zero JavaScript errors and zero document overflow failures**.

Axe WCAG 2 A/AA and 2.1 AA checks passed on the 42 populated views; WCAG 2 A/AA also passed on the comparison page. This includes automated text contrast checks, not a claim of complete accessibility certification. Keyboard skip links and detail expansion worked. Fixture settings, invocation approval, lifecycle actions and empty/unavailable retries produced observable local outcomes. Narrow tables deliberately scroll inside their labeled, keyboard-focusable regions.

The first pass found light detail overflow at 390px. Giving the nested grid child `min-width: 0` fixed it; the complete browser check then passed. Desktop dark and narrow light screenshots were also inspected visually. Screenshots and machine-readable results are retained beside the reproducible checker.

These checks establish fixture behavior only. Factory gates exercise production Python code and do not cover this prototype JavaScript. Production implementation remains pending James's design selection.

Factory verification: `node .agents/vendor/harness/hooks/gate_report.mjs --force --json` exited 0 with verdict `pass`: Ruff lint, Ruff format check, mypy and pytest all passed. The report returned empty output tails; the exact JSON is retained in [factory-gates.json](factory-gates.json). Mypy's declared-path caveat applies: it does not inspect these browser files. Pytest's fake-runtime caveat also applies; no real sandbox or external integration claim follows from this gate run. No gates were skipped in this report.
