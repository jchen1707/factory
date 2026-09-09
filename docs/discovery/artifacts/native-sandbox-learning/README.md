# Native sandbox learning artifacts

These September 9 measurements contain fixture data only. Scripts retain their exact
measured temporary paths for provenance; update paths and choose fresh owned sandbox names
when reproducing. Never point these scripts at a live user sandbox or actual vault.

- `clean-kit-results.json` and `clone-topology.json`: secret names/boolean emptiness only;
  no credential values. The initial kit script overrides target-declared sensitive variables
  with empty values at creation, and no production configuration is changed.
- `capture-results.json`, `post-removal-results.json`: real adapter exports and byte hashes.
- `paths-results.jsonl`, `interruption-results.jsonl`: native callbacks for real models and
  actual interrupted processes. `clone-*` records actual `sbx create --clone` topology,
  native retention, removal and the real host learning worker.
- `native-tool-shapes.json`: synthetic tool call/output items from the actual runtime.
- `distill-results.json`: direct host processing after sandbox removal. One model decision
  skipped the short lesson; another captured it. This variability is preserved.
- `before-precision-fix-*`: the final worker-to-later-session test returned the desired note
  but its prompt hook also selected an unrelated note; the failed precision check is evidence.

Full raw rollouts are intentionally not archived here: their system/tool schemas are not
needed to establish the synthetic message-preservation assertions. Reproduction invokes
real models and incurs normal model usage. These scripts are measurement records, not a
replacement for factory adapters or lifecycle commands.
