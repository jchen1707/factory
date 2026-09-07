# Composable preset installation acceptance

Measured 2026-09-07 UTC. All four default presets and three optional-component
combinations installed successfully and passed every declared gate. Both attempted
browser/server-provider conflicts were refused before a destination was created.
This measures generated project installation and default scaffold gates, not deployed
provider/database integration or factory orchestration.

## Exact inputs and execution

- Shared source: `d2992c8be12edd9da3d39f70a433c2cb5157b477`, local branch
  `fix/explicit-test-design` in `artifacts/runtime-test-design-harness`.
- Python catalog/templates: `624542b4aa8887f1e94a00ef40efd4cde697b723`, retained
  disposable Python checkout from the original rollout probe.
- Frontend catalog/templates: `87cf15cb0df220c0fd576e32960284cf396d1c58`, freshly
  cloned `frontend-harness@v2` under this measurement's artifact directory.
- Host: macOS ARM64, Python 3.14.7, Node v22.23.2, pnpm 10.15.1, uv 0.12.5.

The source-owned `scripts/validate_compositions.py` composed and installed every
catalog preset in a fresh temporary directory, then invoked the shared gate runner
with force, the generated authority directory, and the generated default delivery
profile. Its reports also require all review frames and project checklists to exist.
Every report had an empty `missing_review_inputs` list.

Validation overlapped preparation of the final shared commit. That commit changed
shared test-design guidance and monorepo app template test-path declarations; it did
not change the composer, validator, gate hooks, or standalone catalog templates used
here. Retained input hashes were compared again after the commit: no captured input
changed. `provenance.json` records both the initially observed HEAD and final revision.
The CRUD monorepo baseline was not repeated as part of this standalone preset check.

## Results

| Generated case | Components beyond preset | Install | Gates |
| --- | --- | --- | --- |
| Python minimal | None | Pass | 4/4 pass |
| Python FastAPI | None | Pass | 4/4 pass |
| TypeScript minimal | None | Pass | 5/5 pass |
| React-Vite | None | Pass | 5/5 pass |
| FastAPI optional services | postgres, rag, agents, openai | Pass | 4/4 pass |
| React-Vite clients | rest, graphql | Pass | 5/5 pass |
| TypeScript server providers | anthropic-server, openai-server | Pass | 5/5 pass |

The default validator accepts no optional-component argument. The retained
`optional.py` calls the same source-owned `compose()` function with the combinations
above, uses each generated config's installation argv, and delegates all gates to
the same source-owned runner. It preserves generated source/configs and resolved
lockfiles for the optional combinations. Requirements are resolved transitively by
the composer; every currently declared optional component appears in a measured
installation, but these three combinations are not an exhaustive powerset test.

Adding `anthropic-server` to `react-vite` was refused with
`Incompatible component: anthropic-server`; adding `openai-server` was refused
likewise. Both destination paths remained absent. No provider or database service
was called, and no credential was supplied.

## Retained evidence and limits

Evidence lives in `artifacts/runtime-composition-acceptance/`:

- `python/minimal.json`, `python/fastapi.json`, `frontend/minimal.json`, and
  `frontend/react-vite.json`: source-validator installation output and full gate reports.
- `python-optional/report.json`, `frontend-clients/report.json`, and
  `typescript-server-providers/report.json`: optional installation/gate reports,
  each beside generated inputs and resolved lockfiles.
- `conflicts.json`, `optional.py`, process logs, `provenance.json`,
  `verified-summary.json`, and `sha256.json`.

Artifact index SHA-256: `78b70782f202b671bb665a1aa12e18fa1a5ab7aea9a03d02c0e311895cafade7`.

All 32 reported gates have `status: pass`; none was counted from a skipped or disabled
status. The default validator deletes temporary generated projects after execution;
its retained reports record resolved installation output and exact gate results,
while catalog/template/source hashes bind the inputs. Optional cases additionally
retain generated sources and lockfiles.

These installations ran on the host in temporary projects with the inherited Python
environment override removed. They do not assert Linux-native dependency compatibility,
provider behavior, a live PostgreSQL deployment, retrieval quality, agent workflow
correctness, or all optional-component combinations. The generated scaffold gates
exercise the scaffold's own behavior; successful dependency resolution does not prove
service integrations. Broader product/template/sandbox acceptance remains distinct.
No existing product, ticket, sandbox, live registry, or source file was modified.
