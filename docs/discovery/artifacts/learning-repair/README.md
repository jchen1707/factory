# Isolated learning runtime measurement artifacts

Historical measurements from September 9, 2026 UTC (September 8 EDT). `observed-results.json` preserves sanitized callback observations and original transcript ISO timestamps. `capture-results.json` preserves real distillation and later model recall outcomes. No credentials or full runtime transcripts are included.

The `.py.txt` files are archived measurement scripts, runnable with Python, rather than factory package modules. They write temporary fixture repositories and vaults. They require installed authenticated host runtimes, Python, Git and Node. They make real model calls, leave fixture runtime transcripts in the runtimes' normal session stores, and do not repair authentication. Interrupted probes SIGKILL only the process group they create.

Set `FACTORY_ROOT` to a factory checkout and `HARNESS_ROOT` to the repaired layer-A checkout. Keep the archived scripts together because specialized probes load their shared fixture setup from `factory-learning-probe.py.txt`.

Examples (paths relative to this directory):

```sh
python3 factory-learning-probe.py.txt
python3 factory-learning-recall-probe.py.txt exec
python3 factory-learning-recall-probe.py.txt appserver
python3 factory-learning-distill-probe.py.txt
PROBE_NATIVE_DEFAULT=1 python3 factory-learning-distill-probe.py.txt
python3 factory-learning-claude-args.py.txt args
python3 factory-learning-claude-args.py.txt command
python3 factory-learning-claude-args-env.py.txt args
```

The distillation scenario and unique recall tokens are test fixtures. Their real model processing proves plumbing, not a production incident. These are host measurements; sandbox evidence must be established separately. Original measurements used Codex 0.153.4 and Claude 2.1.259; future versions can differ.

`factory-learning-hook-env.py.txt exec` and `factory-learning-hook-env.py.txt appserver` measure whether native hooks receive a vault supplied only through the shell environment policy. `hook-environment-results.json` records boolean equality only; no environment values are emitted. Both callbacks missed the configured temporary vault in both measured host runtimes.

`factory-learning-alias.py.txt` verifies native Codex capture and fresh worktree hook recall with only `OBSIDIAN_VAULT_DIR` in the process environment and no canonical variable or distiller override. `alias-results.json` preserves the successful real model outcomes.
