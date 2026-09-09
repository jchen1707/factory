# Interactive learning shutdown: measured host results

Measured September 9, 2026, 04:16–04:21 UTC. This extends the
[earlier unsuccessful probe](interactive-learning-2026-09-09.md). These are automated
owned PTY sessions using the actual interactive frontends, not human-operated sessions,
headless `exec`, or sandbox measurements.

## Results

| Scenario | Native callbacks | Transcript after exit | Capture and recall |
| --- | --- | --- | --- |
| Codex CLI 0.153.4, `/exit` | Start, Prompt, End; transcript exists at each | Present, 89,190 bytes | Actual Codex distiller wrote the lesson and both indexes after frontend exit; later interactive session cited its note and verification marker |
| Codex CLI 0.153.4, owned process group SIGKILL after assistant response | Start, Prompt; no End | Present, 86,141 bytes | No automatic end capture; explicit host replay wrote the lesson and both indexes; same-session replay skipped unchanged; later interactive session recalled it |
| Claude Code 2.1.266, `/exit` after expired-login response | Start, Prompt, End; transcript absent at Start/Prompt, present at End | Present, 9,213 bytes | Detached capture reports `failed: claude exited 1`; no authenticated response or note |
| Claude Code 2.1.266, owned process group SIGKILL | Start, Prompt; no End | Present, 8,034 bytes | Login expired; no authenticated response or end capture |

Machine-readable results and the final measurement harness are under
[interactive-completion](artifacts/learning-repair/interactive-completion/).
The [clean Codex result](artifacts/learning-repair/interactive-completion/codex-clean.json),
[later recall](artifacts/learning-repair/interactive-completion/codex-recall.json),
[interruption](artifacts/learning-repair/interactive-completion/codex-kill.json),
[explicit recovery](artifacts/learning-repair/interactive-completion/codex-explicit-recovery.json),
and [recovered-note recall](artifacts/learning-repair/interactive-completion/codex-recovered-recall.json)
separate capture from retrieval. Claude's
[clean](artifacts/learning-repair/interactive-completion/claude-clean-auth-expired.json) and
[interrupted](artifacts/learning-repair/interactive-completion/claude-kill-auth-expired.json)
results preserve the authentication limitation.

The lesson describes the already measured difference between native callback environment
and `shell_environment_policy` tool environment. A fixture-only regression marker was added.
Both later interactive prompts omitted that marker and the note path; actual assistant
final responses supplied both from recalled context. No tools were requested. A deliberately
trivial initial lesson returned `no learnings`, demonstrating that outcome separately from
Claude's failed capture. Replaying the interrupted session produced one note for that session,
then `skipped: unchanged`; later recall sessions have their own identities and can produce
separate notes.

## Trust and isolation

Every frontend ran at the existing `/Users/james/factory` repository path. The invocation
supplied observed layer-A capture/recall hooks and a separate temporary vault through the
parent process environment. Native callbacks all confirmed that binding. No real Obsidian
vault was modified by these probes. The source implementation was the existing
`harness-learning-repair/plugins/harness/hooks` tree; no production hook code changed.

There was no project trust override and no trust-screen acceptance. The harness aborts its
owned process group if trust-screen text appears, before sending command keys. It waits for
an actual assistant final transcript before `/exit`; Claude's authenticated response detector
excludes its `<synthetic>` login-error message. Only owned probe process groups were signaled.
The SHA-256 digest of `~/.codex/config.toml` was identical before and after all completed
measurements; only the digest was inspected, never configuration values. Earlier accidental
trust entries remain James's to remove. No authentication repair was attempted.

Repository hook registration also invoked capture alongside the observer's invocation-local
hook, yielding two queued workers for some clean Codex sessions. The lock allowed one write
and reported the competing worker as `failed: session capture already running`. This is
observable duplicate invocation with one note, not evidence that only one hook was installed.
Capture was detached: the interactive frontend exited before the distiller's terminal write.

## Measurement corrections and limits

The script is archived as evidence, not a generic unattended trust workflow. Invalid attempts
were excluded from the success table. One early recall attempt reused the prior session's
transcript when deciding to send `/exit`, then timed out; the final probe filters callbacks to
its own start time. One owned SIGKILL cleanup waited on the child before closing the PTY
master and stalled on macOS; closing the master first fixed the repeat. Initial Claude error
detection incorrectly counted any assistant message; the final detector distinguishes the
synthetic expired-login message and records `authenticated_response: false`. These
measurement corrections did not change the trust configuration.

The installed Claude command reported 2.1.259 initially and 2.1.266 later during these probes;
no update command was issued. The final archived harness records version at startup and
sets `DISABLE_AUTOUPDATER=1` for subsequent uses. The valid final Claude measurements above
reported 2.1.266. Raw terminal output and native transcripts remain in private temporary/host
storage and are not committed; sanitized outcomes and relevant assistant recall text are.

**Claude authenticated capture/recall remains blocked by an expired login.** James must renew
that login before those model-dependent cases can pass. The native shutdown/transcript timing
and explicit capture failure are now measured even with expired authentication.

**SIGKILL cannot invoke SessionEnd.** This demonstrates retained-transcript recoverability
through an explicit host replay of the owning layer-A implementation, not an automatic
interactive restart scanner or historical backlog recovery. No historical transcripts were
processed. Full sandbox export and retention remain a separate acceptance task.

The [official CLI command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
was consulted before the local measurements. Installed CLI help and the observed effects,
rather than documentation alone, establish the behavior reported here.
