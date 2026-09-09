# Native learning implementation and acceptance — September 9, 2026

This continues the [prior acceptance](learning-continuation-2026-09-09.md). Native sandbox
retention and Codex interactive capture/recall are now implemented and measured.
**Claude authenticated capture/recall remains blocked by expired host login.** James was
asked to renew it; no credential or authentication configuration was changed.

## Implementation

Factory exports an exact runtime session through a controller-owned, isolated Python helper
inside the sandbox. The measured `state_5.sqlite` index supplies its native rollout path;
there is no guessed filename or scan through other conversations. Lookup, path validation,
regular-file reads and output size are bounded. Export runs before suspension stops and
cancellation archives, including recorded invocations without an exit marker. Review uses
its recorded invocation even when its event file has moved.

Native bytes and retention receipts live beside the attempt events. The host worker prefers
matching native evidence, otherwise retaining its explicit event fallback. An incomplete
final JSON record produces `retained-native-prefix`; complete retained records produce
`retained-native-transcript`, which does not claim a successfully completed model turn.
Unknown indices, unsafe paths, removed sandboxes and transcripts above 64 MiB report native
unavailability. Export is bounded synchronous collection; model distillation stays detached.
Abrupt removal before export remains a loss boundary. No independent recovery queue was added.

Two independent review reproductions found and fixed quarantine replay and temporary-file
symlink defects. Replays respect quarantine and recheck the registered project's secret
names. Exclusive temporary-file creation secures native, snapshot and receipt writes.
Shared harness parsing now preserves native tool calls and outputs, and partial native
prefixes cannot replace an existing same-session note. No certified app-server worker,
public API or database schema changed.

The final actual recall check found a precision defect: the generic query word `project`
matched every note's `Project Learnings/` folder. Harness `54c5c9a` excludes that structural
prefix from scoring; it does not discard the word from meaningful summaries or alter the
context budget. The original failing prompt and unrelated fixture passed after repair.

## Actual runtime evidence

[Sandbox measurements](../discovery/native-sandbox-learning-2026-09-09.md) establish:

- Eight actual successful legacy/app-server runs across bind mounts, worktrees, VM-local
  clones and real `sbx --clone` with a separate artifact mount.
- Two actual SIGKILL interruptions: Start fired, End did not, and existing native records
  remained exportable. No unproduced assistant or tool response is claimed.
- Ten native exports with matching hashes after owned sandbox removal.
- Actual `learning.retain` → sandbox removal → `learning_worker.run` with a registered host
  hook produced an indexed note from native evidence. Replaying terminal snapshots left
  receipts unchanged. A later actual Codex session in another factory worktree recalled the
  worker-produced note and its original session identity, absent from its prompt.
- Both callback contexts excluded an unrelated project note after the scoring repair.

A disposable kit supplied empty declared credential variables at creation. Container and
process checks found none nonempty, and proxy inspection showed only the permitted gateway.
This resolves the probe blocker without a policy exception, global credential change or
production registry change. The installed `sbx` 0.38.0 behavior was measured rather than
inferred from newer documentation. The kit uses the documented
[environment block](https://docs.docker.com/ai/sandboxes/customize/kit-reference/).
Both owned sandboxes were removed. Native runtime was Codex 0.149.1 with its supported model.

[Interactive measurements](../discovery/interactive-learning-completion-2026-09-09.md)
establish actual Codex 0.153.4 terminal shutdown, detached capture, indexes and later
interactive recall. SIGKILL preserved its transcript; explicit host recovery and replay
passed. These are automated PTYs operating the actual frontend, not human-operated sessions.
They do not establish an automatic interactive restart scanner. Existing trusted repository
context avoided trust prompts, and the user trust-config hash remained unchanged.

Claude 2.1.266 fired clean shutdown callbacks and left its transcript, but reported expired
login and observable failed capture. Authenticated Claude capture/recall cannot be credited
until reauthentication and a new measurement. No trust-screen acceptance or secret handling
is part of that remaining check.

The earlier [genuine Obsidian destination proof](learning-continuation-2026-09-09.md) remains
valid: a real audit lesson and both indexes exist under the configured vault's
`Project Learnings`, and later worktree recall consumed it. New runtime probes used temporary
vaults and did not rewrite historical notes. Short fixtures sometimes produced an explicit
`no learnings` model classification; the sandbox evidence preserves those outcomes.

## Verification and limits

The [gate record](native-learning-gates-2026-09-09.json) contains Factory and all three
consumer reports. Shared source checks and formatting passed; the main suite ran 165 tests.
The wildcard command reported 184 executions because it also selects files imported by the
main suite; this is not a count of 184 unique tests.

Factory's declared gates all passed at exit 0: Ruff lint (66 ms), Ruff format (37 ms),
mypy (221 ms) and pytest (175,714 ms). Output tails were empty; no pytest count is inferred.
Mypy's configured
paths include the new production helpers. The fake-adapter caveat remains applicable to
pytest: lifecycle regressions prove collection/quarantine/ordering, while the real runtime
measurements above prove native hooks, export and host processing. These fixture runs are
not a full child/delivery workflow recertification. The certified worker was unchanged.
The updated learning diagram rendered; 145 local documentation links resolved. No console
behavior changed, so the existing dark-console browser acceptance remains applicable.

All source and consumer changes remain on local feature branches. Vendor integrity matches
harness `54c5c9a`; upstream freshness is not established. James owns merging and deployment.
The [current handoff](../handoff-ui-learning-docs.md) now lists Claude authentication and
publication status, rather than treating completed sandbox/Codex work as still blocked.
