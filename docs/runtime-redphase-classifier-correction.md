# Red-phase replay distinguishes assertion diagnostics from test errors

The concurrent workflow audit found three false-positive red-phase records: two
bind-layout replays failed with ImportError, and one clone-layout replay failed with
AttributeError. Their unittest summaries said `FAILED (errors=...)`, which the old
classifier treated as assertion proof. These records remain preserved and are not
valid assertion-level red evidence. Their resource/concurrency measurements remain
independently useful.

Removing the generic failure word exposed a second form of the same problem. An
actual host pytest fixture printed an `assert service.scale(...)` source excerpt,
then raised AttributeError before comparing anything. Matching source expressions
still misclassified that output as red.

The classifier now requires recognizable assertion diagnostics: an AssertionError
diagnostic, pytest's `E assert` diagnostic, or its failed-test assertion summary.
Traceback source excerpts, generic FAILED summaries and unknown output formats are
inconclusive. Genuine pytest, unittest and measured Vitest assertion diagnostics
remain recognized. Mixed genuine assertion/collection-failure precedence and the
configured inconclusive policy are unchanged. Jest matcher-only output without a
recognized diagnostic is an explicit limitation; universal runner parsing is not
claimed.

Regressions failed before each correction; 34 focused tests and full mypy passed
on the final implementation. Exact captured argv/stdout/stderr from all three real
gates were replayed through the corrected classifier, without re-executing those
commands or changing their stores. Each now returns `inconclusive`. Original file
hashes are unchanged. The separate host pytest reproduction also changes from red
to inconclusive, while real assertion failures stay red.

Evidence is under `artifacts/runtime-redphase-classifier-correction/`: `red.txt`,
`source-excerpt-red.txt`, the host fixture and before/after JSON, `replay.py`, exact
captured outputs and hashes in `result.json`, and final `factory-gates.json`.
The earlier summary-only correction and its gates are retained separately; the
source-excerpt follow-up supersedes that partial fix. Independent review found no
remaining concrete finding in the final correction.

No sandbox/model was launched for this fix, no disposable app was hardened, and no
historical positive record was overwritten. These are corrected classification
measurements, not a fresh assertion-level red run of the concurrent fixtures.
The earlier dedicated monorepo replay and DIAG-1 assertion failures remain separate
positive evidence. See [concurrent workflow measurements](runtime-concurrent-workflows-acceptance.md).

Correction `0122e71` passes all four final factory gates in the linked artifact.
