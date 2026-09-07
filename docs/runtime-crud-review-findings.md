# CRUD review findings and reproduction

The isolated review of FRO-12 commit
`b635ade17a03b7adf3999b676244be8719bf7d89` reported a high-severity CORS finding
at `apps/api/src/api/main.py:22`. The factory's review policy reserves disposition
of high/critical findings for James. The candidate has not been changed or the
finding dismissed by the operator.

The operator reproduced these effects in `factory-build-crud-20260907` using the
actual candidate application and installed dependencies:

| Observation | Result |
| --- | --- |
| Documented `pnpm dev`, terminated after five seconds | Vite printed `http://localhost:5173/` as its local URL. |
| POST preflight from `http://localhost:5173` | HTTP 400, `Disallowed CORS origin`, no allow-origin header. |
| Same preflight from `http://127.0.0.1:5173` | HTTP 200, matching allow-origin header. |

The preflight probe used FastAPI's HTTP test client in the build VM with a temporary
SQLite path; it did not write application notes or use production data. This is
HTTP middleware and actual Vite-default evidence, not a browser screenshot or a
claim that all frontend flows were exercised interactively.

Retained evidence: `artifacts/crud-runtime-test/cors-preflight.json`,
`vite-default-origin.json`, and the executable probe under the isolated protocol
mount. The review output is retained under
`state/runs/47515078d97246e9/review/` in that home.

All eight independent axes completed: standards reported this one high finding;
spec, security, tests, simplicity, design, speed and cost returned no findings.
One security invocation failed because the runtime model was at capacity; a supported
retry preserved prior reviews, recorded a distinct invocation and completed cleanly.
The factory then blocked the run as `review-finding`, as its policy requires.

A proposed correction is to align the API's allowed development origins with the
documented frontend origin, retaining an acceptance test for that exact preflight.
James's disposition is required before the factory implements a review-driven fix.
The final state and all result paths are retained in
`artifacts/crud-runtime-test/handoff-state.json`. The candidate is preserved in a
verified Git bundle, both sandboxes are stopped, and the run is back in Approval mode.
