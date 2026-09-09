# Interactive learning measurement: incomplete

Measured September 9, 2026, 04:00–04:04 UTC. This is historical evidence of an
unsuccessful acceptance attempt, not current operator instructions. It supplements
[the learning measurements](learning-repair-2026-09-08.md) and the
[completion handoff](../handoff-ui-learning-docs.md).

## Result

Actual host Codex CLI 0.153.4 and Claude Code 2.1.259 interactive frontends were
started in owned pseudo-terminals, with temporary fixture repositories and temporary
vaults. This was automated terminal interaction, not human-driven use. **Clean
interactive completion, authenticated Claude execution, capture, and later recall
were not established.** No learning note was written to either temporary vaults or
the real Obsidian vault by these probes. The logger only observes native callbacks;
it does not invoke the learning capture implementation.

Both frontends stopped at directory trust screens. The first Codex command rejected
`--ignore-user-config`, an option accepted by the earlier headless probe. Removing
that option reached the trust screen. An invocation override for
`projects.<resolved temporary path>.trust_level="trusted"` did not suppress that
screen in this measurement; the cause was not investigated further.

## Probe side effect and invalid measurements

The early probe sent `/exit` plus Enter after 35 seconds without first proving the
frontend was past the trust screen. Codex interpreted the Enter as trust acceptance
and persisted these two temporary directory entries in `~/.codex/config.toml`:

```text
/private/var/folders/1f/039kvb3j5vb1n0nr66j5xw6c0000gn/T/factory-interactive-learning-vexa753n
/private/var/folders/1f/039kvb3j5vb1n0nr66j5xw6c0000gn/T/factory-interactive-learning-t7uq5676
```

This violated the repository's trust-store boundary through native UI behavior.
The probe did not directly edit the configuration, and no automated cleanup or
restoration was attempted. James was informed; removing those entries, if desired,
is left to James. The exact paths above are temporary fixtures, not product repos.

Those two runs eventually reached native SessionStart callbacks and retained host
transcripts, but timed out and were killed. They did not complete the requested
clean-exit scenario. Each logged two SessionStart observations, no SessionEnd, and
reported that the temporary vault process binding matched. Duplicate observations
were not diagnosed and are not evidence of distinct sessions. Terminal marker
counts include the input prompt and cannot establish model success.
[One representative result](artifacts/learning-repair/interactive-codex-invalid-clean.json)
is retained with its original scenario label `clean` and actual exit code `-9`;
the label is requested intent, not success.

The early Claude probe attempted fixture trust acceptance but recorded no native
callbacks, including in its requested interruption scenario. It never established
authenticated execution. The previous expired-OAuth finding is not revalidated by
these results: a false `oauth_expired` flag means that string was not observed,
not that authentication worked.

## Safe abort and remaining work

The [archived PTY probe](artifacts/learning-repair/interactive-pty-probe.py.txt) was
corrected to kill only its owned process group when it recognizes a trust screen,
without sending trust-screen keystrokes. Final bounded abort checks recorded
[Codex](artifacts/learning-repair/interactive-codex-trust-abort.json) and
[Claude](artifacts/learning-repair/interactive-claude-trust-abort.json) stopping at
that boundary, with no callbacks and no vault writes. These are safety checks,
not successful runtime acceptance. All owned probe processes completed or were
killed by their own process-group cleanup; no live user session was signaled.
All further interactive probes were stopped.

Do not run this archived script as an unattended acceptance recipe. Trust handling
must first be independently established without modifying James's agent trust
store. A later properly authorized interactive session still needs clean exit,
interruption, native transcript availability, capture/index outcomes and relevant
recall evidence. No sandbox, authentication repair, real-vault mutation, or
historical recovery was attempted here. The raw terminal recordings remain in
temporary fixture directories and are intentionally not committed.

The [official terminal command reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
was consulted for command context, but did not establish the measured trust or
shutdown behavior. Installed runtime observations above are the evidence.
