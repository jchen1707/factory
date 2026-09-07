# Suspend announcement outage correction

The concurrent workflow experiment's no-tracker fixture exposed an exception after
successful suspension. Inspecting the production path found that `_suspend_announce`
looked up the issue UUID outside its existing `LinearError` handler, despite promising
that tracker outages cannot mask suspension.

The lookup now sits inside that same handler. A regression injecting the actual
`LinearError` type fails before the correction and passes afterward: `suspend` returns
the original Verifying state, the run remains Suspended, no comment is sent, and
`suspend.announce_failed` is logged. All six suspend tests pass. The effects ledger and
state transition logic are unchanged. This is a source regression for an outage, not
a deliberately induced outage against live Linear.

The concurrent experiment's rejecting adapter raises `RuntimeError`, so its retained
refusal is fixture evidence and is not represented as a real Linear outage or as a
successful tracker announcement. No exception class was widened to hide that refusal.

The red output is retained at `artifacts/runtime-suspend-announcement/red.txt`; final
combined gate evidence is `artifacts/runtime-accounting-policy-gates/factory-gates.json`.
Bounded independent review found no concrete issue. No tracker write, sandbox action,
or production-state change was performed to test this correction.

Correction `ec36c41` passes all four final factory gates in the linked report.
