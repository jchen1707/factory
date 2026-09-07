# Runtime correction review

This continuation reviewed the local corrections following `41f1eef` against the
approved rollout and shared contracts. Independent read-only reviewers examined
readiness collection, supported recovery, test-design routing and provenance, and
monorepo red-phase replay. This was a focused review, not a claim of completing
every axis of the repository's full-review workflow.

The existing private-clone refresh and readiness serialization corrections had
already received independent review and regression validation before compaction.
The new recovery path revalidates completed readiness before an operator-audited
transition, retains the same attempt, archives its original schema/manifest and
accounting record, and preserves the next builder approval hold.

Review found and corrected these issues:

- Child replay configuration initially came from the candidate checkout. It now
  comes from immutable authority; candidate removal of test rules cannot govern
  its own replay or assertion-removal check.
- Recovery initially admitted only the historical request contract name. It now
  recognizes the bounded readiness and test-design names as well.
- Test-design outputs initially trusted the writable request. The complete selected
  contract is now retained in immutable invocation metadata and compared during
  collection. Artifact names are revalidated before file I/O.
- A malformed legacy request containing JSON null initially skipped structured
  result validation. Any present request must now be an object; genuinely absent
  historical requests retain their original file-based behavior.

Regressions were demonstrated failing before their corrections. Factory gate
reports taken while parallel edits were in progress include superseded failures;
the final stable-tree report is
`artifacts/crud-runtime-test/factory-gates-release.json`. Consult its verdict rather
than interpreting earlier overlapping reports as a final result.

Shared contracts and generated test-path declarations are proposed in
[harness PR #32](https://github.com/jchen1707/harness/pull/32), source
`d2992c8be12edd9da3d39f70a433c2cb5157b477`. Consumer regeneration and an explicit
operator authority refresh are needed before those declarations govern the current
CRUD run. Passing local regressions does not establish full workflow acceptance;
the remaining requirements are in [the inventory](runtime-acceptance-inventory.md).
