# App-server worker compatibility binding

The adapter now refuses compatibility evidence for a different worker, including
manifests without a worker hash. Previously the manifest recorded `worker_sha256`
but validation only checked runtime identity, sandbox identity and evidence files.
Thus a source update could silently continue using an old compatibility result.

The correction compares the manifest with the actual worker bytes during adapter
selection and again immediately before preparing an invocation. The latter check
covers an adapter object retained across a host source update. Preparation copies
exactly the bytes checked; refusal occurs before writing the worker or request.
Legacy exec selection remains unchanged.

`tests/unit/test_app_server_compatibility.py` covers missing/stale hashes, an exact
match, and a source change after selection. The two missing/stale cases failed on
the original adapter (`artifacts/runtime-worker-binding/red.txt`). All four cases
and the runtime workflow tests then passed (25 tests total). These use synthetic
manifests and establish refusal logic, not sandbox compatibility.

The unchanged retained build manifest for `factory-build-crud-20260907`, describing
worker `f0bf1c95…`, was actually passed to the corrected validator and refused.
`artifacts/runtime-worker-binding/retained-manifest-check.json` records this check
and confirms the original file was unchanged. Fresh real compatibility evidence
must describe the final worker bytes; historical reports remain historical.
