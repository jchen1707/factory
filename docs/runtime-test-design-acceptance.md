# Separate test-design role acceptance

Real role probe, 2026-09-07 UTC. This is a bounded acceptance fixture for factory
orchestration, not completion of the CRUD workload. It uses a new local synthetic
`ACCEPT-1` run in `artifacts/runtime-test-design-acceptance/state/factory.db`.
There is no corresponding tracker ticket and no tracker/forge adapter is available
to the experiment. FRO-12 and the production store/settings remain untouched.

## Fixture and source

`accept.py` under that artifact directory prepares, launches and collects the
experiment using production `plan.start`, app-server transport, detached execution,
`plan.collect`, accounting, and the next `implement.start` approval boundary.
The disposable repository/protocol is under the existing build VM's writable
clone-protocol mount, in its own `test-design-acceptance/repo` subdirectory.
The existing private CRUD clone and its dependency environment are not the fixture.

Shared contract source is local **unmerged** PR32 commit
`5f4e3dd76584b4f69e74df63043f61b56eb009d9`, generated into the disposable consumer
with `vendor_sync.py`; no generated vendor file was hand-edited. The fixture was
committed locally and has no remote publication. The source includes the separate
`test-design` contract requiring nonempty `test-plan.md`.

The scratch run is initialized at `worktree_ready`, so this experiment does not
claim intake, provisioning, or worktree lifecycle acceptance. It uses no immutable
delivery snapshot: the current committed scratch repository and explicit local
acceptance contract govern this role-only probe. Immutable authority behavior is
covered separately. Model preset `volume` selects Sol/high for `test_designer`.

The preflight checks the exact reused `factory-build-crud-20260907` runtime,
capability names and invalid inherited template authentication before launch.
Existing exact-version compatibility evidence is retained separately. Nothing in
this experiment authorizes production activation.

## Result

The actual `plan.start` first held `1:plan:1` without creating an attempt or model
invocation. The isolated operator approved that key, after which the production
app-server path launched one Sol/high test-design invocation. `plan.collect`
accepted its schema-valid `ready` result and retained the required 119-line
`test-plan.md`, plus an optional 11-line `execution-brief.md`.

The scenario document covers valid note creation, atomic validation failure,
ordered SQLite persistence, frontend submission, and frontend error preservation.
It names observable interfaces, isolated setup, expected outcomes, failure meaning,
and later red/green evidence for each scenario. It uses the declared pytest and
Vitest seams and explicitly leaves DOM coverage outside this fixture's tooling.
This is scenario-design evidence, not proof the product behavior works.

All baseline repository files remained byte-identical. The only new files outside
the protocol directory were those two planning artifacts. No executable tests,
application changes, dependency installation, model-requested gate execution, or builder invocation
occurred. The production `implement.start` held **`1:implement:1`**; invocation
count stayed one and no implementation event stream appeared. The scratch run
remains `planning`, awaiting that next approval.

Accounting retains semantic role `test_designer`, preset `volume`, adapter
`app-server`, and the exact compatibility report. The one invocation recorded
153,749 input tokens (123,776 cached), 6,825 output tokens, and complete usage.
Its eight request estimates were complete, totaling **$0.3059024 API-equivalent**
under the retained price revision. This is an invocation-specific estimate, not
a claim about overall accounting or an actual subscription charge.

The serialized context includes the default unavailable message alongside valid
tokens. Replaying that captured state through production display code established
that it is benign: at measurement +1 second the invocation card renders **11%,
fresh**; at +121 seconds it renders **unavailable, stale**. The fallback message
does not override a valid fresh measurement. No code change was needed.

The sandbox was stopped after collection and handed to the independent monorepo
replay acceptance task. This report records this role probe's stopping point;
that next owner controls its subsequent lifecycle.

## Retained evidence

`artifacts/runtime-test-design-acceptance/` contains the isolated Store, fixture
source revision, reusable `accept.py`, preflight, initial launch, collected result,
accounting, before-file digests, executable outcome assertions, and copied protocol
and planning artifacts outside the candidate directory. `evidence-sha256.json`
binds those retained files. The context display replay includes both actual rendered
HTML and its controlled-clock observations. Raw artifacts remain operator-private.
