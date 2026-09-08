# Child mailbox polling timeout (2026-09-08)

FRO-12 run `c8d569fcd49a4094`, attempt1, failed while asking for child reviews.
The first call received a refusal after about32 seconds; the second failed with
`delegation controller response unavailable; request retained` after60 seconds.
No children launched. Retained accounting remains ten launches/ten invocation rows,
USD1.97297 known API-equivalent subtotal with three incomplete records.

The worker allowed exactly60 seconds, while the normal writer polls every60
seconds plus processing/scheduling delay. A request immediately after a controller
poll therefore had no headroom. A deterministic test reproduces the exact failure
with the real worker, mailbox, broker and SQLite, advancing only the clock.
The original test failed before the fix. It now tests61- and125-second poll delays,
with both repeated invalid paths and corrected paths.

The worker now waits at most300 seconds. Controller unavailability still ends the
invocation with incomplete usage and retains the request for reconciliation. This
is bounded tolerance, not a guarantee for arbitrarily slow controllers. Request
digests, durable refusal replay, approval and child admission are unchanged.

Both original calls used absolute paths, which the broker rejects by design.
Responses now explain repository-relative paths and `.` for the source root,
without echoing host information. Tool registration is deliberately unchanged:
existing native threads and mailbox ownership freeze that configuration. Previously
recorded refusals retain their exact replay response; new calls receive the guidance.

Recovery mailbox transfer was recorded at1788910212, after the failed transition
at1788910147. No transport-failure effect was recorded. These observations do not
implicate recovery archival as the cause of the timeout.

A separate-process check used real host files, an isolated SQLite database and real
elapsed time. The first invalid request was refused, then the second valid request
received one pending handle after62.057 seconds. Controller exit0; no model calls.
This measures host transport, not a new Linux/native-Codex compatibility result.
Private script/output: `/Users/james/factory/artifacts/runtime-child-transport-20260908/`.

Deployment must follow review/merge. Worker bytes changed, so fresh compatibility
certification is required. Preserve the existing VM, private clone, native thread
and queued attempt2 ownership. After deployment and certification, authorize only
the exact held recovery and reconcile its accounting separately. Do not reset the
run, replace its frozen mailbox, switch Approval mode, or complete synthetic tickets
unnecessarily. CLI cached-token display remains a separate unresolved discrepancy.
