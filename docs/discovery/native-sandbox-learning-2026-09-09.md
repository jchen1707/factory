# Native sandbox learning retention — 2026-09-09

These are actual model and adapter measurements, not fake-adapter acceptance. Disposable
`factory-build-learning-kit-d7838306` used the default Codex template, Codex 0.149.1 and
its supported `gpt-5.6-sol` model. An isolated kit set every target-declared secret variable
to the empty string before creation. Container configuration and direct execution had zero
nonempty declared secret values; `sbx inspect` exposed only the excluded MCP gateway token.
No credential value was printed, copied or rotated. The kit changes only these disposable
probes; it is not a production registry or policy change.

## Measured paths

| Runtime and workspace | Actual model outcome | Native hooks | Host export |
| --- | --- | --- | --- |
| Legacy, bind workspace | exit 0 | Start and End | user, tool and assistant content |
| Factory app-server worker, bind workspace | exit 0 | Start and End | user, tool and assistant content |
| Legacy, Git worktree | exit 0 | Start and End | user, tool and assistant content |
| App-server, Git worktree | exit 0 | Start and End | user, tool and assistant content |
| Legacy, VM-local Git clone | exit 0 | Start and End | user, tool and assistant content |
| App-server, VM-local Git clone | exit 0 | Start and End | user, tool and assistant content |
| Legacy, SIGKILL after Start | -9 | Start; no End | existing user prefix |
| App-server, SIGKILL after Start | -9 | Start; no End | existing user prefix |

Every callback reported the temporary vault process environment correctly and a transcript
already present. Native transcripts live under `/home/agent/.codex/sessions/YYYY/MM/DD/`.
Readonly `state_5.sqlite`, exact `threads.id` lookup, returns the same `rollout_path` as the
native hook payload. The new `factory.sandbox.native_transcript.capture` API ran through
real `SbxAdapter` for all eight sessions. Native SQLite paths were not guessed from filenames.

The owned sandbox was removed after export. All eight host files then matched the SHA-256
of their original in-sandbox bytes, measured immediately before removal. Successful sessions
contain distinct known user, custom tool output, and assistant markers. Interrupted sessions
retain only messages actually produced; absent tool/assistant output is not a capture defect.
The interrupted launches were killed after Start and before tool completion, not a full
cancellation/resume workflow certification.

The source, worktree and VM-local clone all resolve through the shared `canonicalProject`
implementation to `native-learning`, using their identical origin. A separate actual
`sbx create --clone` measurement below covers the private-clone mount topology; the local
Git clone rows above alone would not prove factory `requires_clone` behavior.

## Learning from retained data

The actual native legacy transcript was distilled by the repaired layer-A hook on the host
into a temporary vault's `Project Learnings` directory. Initial processing wrote one note;
reprocessing rewrote the same identity, without creating a second note. Both generated
indexes contain it. A later actual Codex session in the other Git worktree received the
identifier `quokka-export-47` and cited its note path, neither supplied in its prompt.

After sandbox removal, fresh-vault processing used only the retained host transcript. The
first model classification returned observable `no learnings: the session taught nothing`;
the second wrote the note and both indexes, and relevant recall returned it. This short
fixture demonstrates model classification variability; it does not establish deterministic
capture for every conversation. No real Obsidian note or historical backlog was changed by
these synthetic probes. The earlier genuine Obsidian destination proof remains separate.

## Failed probes and limits

The first legacy invocation incorrectly passed `--ignore-user-config`, suppressing the
sandbox's existing proxy model provider, and received HTTP 401. Preserving the native sandbox
configuration resolved it with no authentication change. An app-server request for
`gpt-6-astra` failed the worker's model-availability check; selecting the installed runtime's
supported model resolved it. Invocation-local hook trust and inline hook registration
worked despite the runtime's initial project trust warning. No trust-store file was edited.

These disposable fixture runs prove native lifecycle, paths, export, mount topology and
host capture/recall. They do not by themselves certify every factory state transition,
child cancellation, or delivery workflow. Abrupt sandbox removal before export remains a
loss boundary. Full factory lifecycle regressions and gates are reported by the integration
acceptance, rather than inferred from these model runs.

Sanitized [artifacts](artifacts/native-sandbox-learning/) contain results and exact scripts.
Full transcripts stay in the temporary fixture workspace because they include runtime
system/tool schemas; only synthetic tool item shapes are archived.

## Actual private-clone topology and factory worker join

`factory-build-learning-clone-f3e0a137` was created with actual `sbx create --clone`, the
same empty-variable kit, and a separate writable artifact mount. Both container and direct
execution checks remained empty, with only the MCP gateway proxy secret. Writing a marker
inside its primary workspace did not create it on the host, proving the primary workspace
was a private clone rather than the earlier bind mount.

Both real legacy and factory app-server model runs exited 0 and fired Start and End. Actual
`factory.learning.retain` exported their sessions through `SbxAdapter` and wrote retained
native sidecar receipts. User, tool and assistant markers were present in both. The sandbox
was removed; both native sidecars retained their recorded byte hashes.

After removal, actual `factory.learning_worker.run` consumed each real event file and native
sidecar, using the registered host `/Users/james/factory` vendor hook at harness `96c8b68` and
a fresh temporary vault. Both receipts reported `retained-native-transcript`, the original
session identity and matching SHA-256; each learning snapshot exactly equaled its native
sidecar. The legacy model classified the short fixture as no learning. The app-server
transcript wrote `Project Learnings/2026-09-09 factory 01a0846a.md` and both indexes. Re-running
the worker left both receipts byte-for-byte unchanged, proving terminal digest deduplication
without another model call. The host registered project correctly supplies `factory` as
project identity here; the earlier fixture-project identity test is a separate measurement.

Both owned sandboxes have been removed. No live factory run, remote repository write,
deployment, user trust-store update or actual vault mutation occurred in this probe.

A final actual Codex session in `/Users/james/factory-sandbox-learning-evidence`, a different
factory worktree from the registered host project, used the **worker-produced post-removal
note**. Root's synced recall hook supplied the bounded initial index and prompt retrieval.
A logging wrapper recorded both callback outputs. An additional indexed note from a different
fixture project was absent from SessionStart but unexpectedly included at UserPromptSubmit:
the generic query word `project` matched its ubiquitous `Project Learnings` folder path. The
model did not cite the unrelated note. This is a retrieval precision defect, not proof of
unrelated context exclusion. The
model exited 0 and returned the worker note's full session identifier
`01a0846a-051f-7420-ad91-f59d571a49e8` and exact note path, neither present in the task prompt.
This closes the actual native model → retain → sandbox removal → host worker → indexed note
→ later worktree session chain. Capture was disabled in this recall probe.
