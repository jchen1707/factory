# Ticket readiness

Start from the approved spec and approved vertical ticket. Planning is optional.
Check acceptance criteria, testing seams, dependencies, and current execution authority.
Use approved product decisions. Return missing product decisions to the operator.
Proceed directly when these inputs are complete.
Write a short execution brief when only technical execution details need clarification.

A configurable test-design role defines acceptance scenarios and test boundaries.
The builder owns each vertical red-green slice.
Prove one failing test before implementing that slice. Then verify that behavior passes.
Do not write every test before implementing the first slice.

Use the effective delivery profile and its recorded deferrals.
Keep credential boundaries, human decisions, review isolation, and effects accounting intact.
Read relevant evidence on demand. Keep full logs outside the prompt.

Return a structured handoff under the schema supplied by the control plane.
Use `ready` when the acceptance contract supports implementation.
Use `needs-human` for missing product decisions or disputed scope.
