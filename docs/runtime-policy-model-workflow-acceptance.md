# Policy, model and repair acceptance follow-up

Measured 2026-09-07 in a new isolated `factory-review-policy-20260907` sandbox and
local home under `artifacts/runtime-profile-review-acceptance/`. No existing project,
FRO-12, production setting, tracker or delivery action was used. The experiment
reviews a deliberately defective four-line scaling function under three separately
snapshotted profiles; it does not implement or complete a ticket.

## Executing model catalogue

Production `model_probe.validate` queried the fresh executing runtime and checked
every distinct model/effort pair in the factory's Volume and High-confidence presets.
The raw catalogues, exact requests, stderr and accepted validation records are retained.

| Pair | Actual metadata validation |
| --- | --- |
| Sol / high | Pass |
| Terra / medium | Pass |
| Luna / medium | Pass |
| Sol / xhigh | Pass |
| Astra / high | Refused: `runtime-model-unavailable` |
| Astra / xhigh | Refused: `runtime-model-unavailable` |

Astra was absent from the catalogue, not merely missing the requested effort. Thus
all Volume role defaults are supported by this observed runtime; the Astra-dependent
High-confidence roles cannot currently execute here through factory validation.
The factory correctly fails closed instead of substituting a model. No preset or
routing was changed to hide the refusal. This is metadata availability, not evidence
that every role/effort combination completed a model invocation. Existing actual
Sol/high test-design and diagnosis, Terra/medium repair, and Sol/high reviews remain
separate execution evidence.

The executing runtime reports `codex-cli 0.149.1`. The stock image is `docker/sandbox-templates:codex-docker`, digest
`sha256:8b4cd0a46c8b600bc6b6a64af23c03d4c2807fbfc61f47568092a93fb9dc88b0`.
Model availability is scoped to this executing runtime/account observation; it does
not authorize other sandbox identities or production activation.

## Reviewer policy adherence

The fixture's approved source declares scaling correctness in every profile, focused
tests as engineering required by Core with an explicit Prototype deferral, and audit
records as a production requirement activated by Hardening. The candidate returns
its input instead of doubling, has no focused tests, and emits no audit record.

Each profile has an independent real authority snapshot. One standards-axis prompt
is assembled from the source-owned shared frame and fixture checklist, with the
production delivery-review contract and snapshot attached by `_axis_entry`. Actual
`_launch_next` supplies host approval, model selection, detached execution and
invocation accounting; `_collect_axis` collects real findings. Constructed one-axis
plans intentionally stop before a full suite, review disposition or delivery.
The reviewer uses the supported legacy adapter with Volume Sol/high.

All three actual reviews completed with schema-valid findings and exit 0:

| Profile | Actual findings |
| --- | --- |
| Prototype | Scaling correctness defect only (high); deferred tests and inactive audit requirement were not promoted into findings. |
| Core | Scaling correctness defect (high) and missing required focused tests (medium); no inactive audit finding. |
| Hardening | Scaling correctness defect (high), missing focused tests (medium), and missing production audit record (high). |

All three collected invocation snapshots retain reviewer semantic role, Sol/high,
Volume preset and legacy adapter. The candidate HEAD and complete tracked/untracked
status remain unchanged; the effects ledger is empty. The fresh reviewer sandbox is
inspected stopped after the three observations. These results establish adherence to
this fixture's three profiles and deferral distinction, not universal model reliability
or a full eight-axis review suite.

## Repeated failure and repair limits: reconciled scope

The existing [host diagnosis experiment](runtime-diagnosis-acceptance.md) ran six
actual failing gates through shared gate reporting and production failure provenance,
then exercised real repair admission. Two distinct failing behaviors authorized two
repairs in one episode; unchanged evidence, a third repair, modified verifier bytes,
lifetime exhaustion and a controlled spend ceiling were refused. Diagnosis
classifications and spend values were explicit experiment inputs, not model outputs.

Separately, [actual diagnosis workflow acceptance](runtime-diagnosis-workflow-acceptance.md)
measured a fresh Sol/high diagnosis, production schema/provenance checking, one
approved Terra/medium repair, and independent passing verification while preserving
commits and dirty notes. This validates the real diagnosis-to-repair seam for one
repair. It does not claim two failed model repairs followed by an exhausted-loop stop.

The two reports provide complementary acceptance evidence for deterministic episode
limits and actual model repair integration. No extra forced failed repair turns were
run: no concrete untested seam emerged that justified those model calls. This retains
the measured limits honestly without treating two deliberately unsuccessful model
repairs or completion of a disposable product as additional prerequisites.

## Boundary and setup observations

The first preparation command was rejected by the host PreToolUse hook because its
fixture declaration named a protected credential environment variable. Nothing in
that command executed. The rejected environment probe was not repackaged. The
approved safe continuation generated a fixture from an existing source-owned
configuration, added no credential-environment probe, and used normal production
preflight plus sandbox inspection. This experiment measures reviewer adherence;
it does not claim a new credential-environment exclusion or independent secret scan.

An initial fixture launch omitted the required lease and was refused before detached
execution. Its scheduled invocation record remains visibly incomplete with no model
usage; the corrected fixture acquired a lease and used the next approval ordinal.
A later fixture collection used an incorrect Store transition argument; findings
were already retained, and correction only recorded the supported paused transition.
Neither setup error is counted as a successful runtime invocation or source defect.

Raw transcripts, operator snapshots and stores remain local evidence.
`verified-summary.json` binds the three findings sets, invocation metadata, catalogue
results, unchanged candidate, empty effects ledger and stopped sandbox. It retains
the initial factory source revision and hashes of the actual review/model-probe/
execution/accounting sources. `verify_evidence.py` asserts those observations;
`sha256.json` indexes the retained inputs, snapshots, streams, output and store.
The fixture's shared vendor was generated normally from the retained source-owned
concurrent-workflow checkout; source revision and inventory remain in its vendor pin.

Verified summary SHA-256:
`e4c92062764a699e42b7f0e953e821073bedff9185604cf623137c83d02c451a`.

No factory source change or commit was needed. These results close the sampled
reviewer-adherence check and validate fail-closed model availability. Activation of
Astra-dependent roles still requires a runtime whose actual catalogue supports them;
this limitation is not a reason to silently change the approved preset.
