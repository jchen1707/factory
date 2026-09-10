# factory

Factory is the control plane that takes an approved Linear ticket through isolated
implementation, verification and independent review to a pull request awaiting James.
The target repository supplies requirements, review instructions and gate commands;
Factory owns scheduling, recovery and recorded external effects.

Start with read-only inspection after installing dependencies:

```sh
uv sync
uv run factory doctor
uv run factory status --all
uv run factory run BAC-6 --check
uv run factory serve
```

`serve` exposes the operator console on loopback at http://127.0.0.1:7717.
It runs in the foreground; an existing background console needs a restart after code or
UI asset updates. See [console operation and revision checks](docs/operator-reference.md#console-operation-and-revision-checks).
A registered project, configured host credentials and compatible sandbox runtime are
prerequisites. `doctor --deep` performs a paid model canary.

- [Workflow reference](docs/workflows.md): responsibilities, decisions and failure paths.
- [Operator reference](docs/operator-reference.md): commands, configuration, runtimes,
  certification, delegation, concurrency and accounting.
- [Recovery runbook](docs/runbook.md): inspect and resolve a stopped run.
- [Documentation index](docs/README.md): current guidance, historical evidence and archived handoffs.
- [UI alternatives](docs/ui-alternatives/index.html): approval history; James selected the dark
  operations console. [Production browser evidence](docs/ui-alternatives/production-validation/README.md)
  covers all seven real views at desktop and narrow widths.
- [Contributor boundaries](AGENTS.md) and [canonical specification](SOFTWARE-FACTORY-PLAN.md).

James approves readiness, disputed findings, merges, deployment, schema migrations and
credential rotation. Factory never merges or creates tickets. The database contains
non-reconstructible effects and accounting: preserve it and its artifacts together.
Features being present in code does not mean they are activated for a project; follow the
[runtime rollout procedure](docs/runtime-certification-rollout.md) for operator changes.

For development, use branches named `<type>/<slug>` and run the declared gates:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
```

Shared hooks and contracts originate in `harness@v2` and arrive through vendor sync.
Never hand-edit `.agents/vendor/`. Fake-adapter tests prove control-plane logic;
retained real runtime measurements establish what the external tools actually do.
