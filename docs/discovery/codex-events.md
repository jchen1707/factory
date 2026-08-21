# P0-7 — the `codex exec --json` event shape

Captured on the **host** binary, `codex-cli 0.147.0`, four runs. Fixtures in `fixtures/`.

> ⚠️ The binary inside `docker/sandbox-templates:codex-docker` is **0.146.0**, not 0.147.0.
> Everything below was measured on the host. See `p0-3-toolchain.md`.

## Invocation

```sh
codex exec --json --skip-git-repo-check -o last-message.json '…' < /dev/null
```

**`< /dev/null` is not optional.** With a non-TTY stdin that is not closed, `codex exec`
prints `Reading additional input from stdin...` and blocks for ever waiting for EOF, because
it appends piped stdin to the prompt as a `<stdin>` block. A factory that spawns Codex
without closing stdin hangs with no output and no error. This cost one 5-minute timeout
during discovery and belongs in `agent/codex.py` as a hard rule.

## Event types observed

| Event | Top-level keys | Notes |
| --- | --- | --- |
| `thread.started` | `type`, `thread_id` | **`thread_id` is the only field.** |
| `turn.started` | `type` | No payload at all |
| `item.started` | `type`, `item` | Only for long-running items |
| `item.completed` | `type`, `item` | |
| `turn.completed` | `type`, `usage` | |
| `turn.failed` | `type`, `error` | `error.message` is a JSON *string*, doubly encoded |
| `error` | `type`, `message` | Top-level, alongside the `item` form |

`item.updated` was **never emitted**, in any run.

### `item` types observed

`agent_message` (`text`) · `command_execution` (`command`, `aggregated_output`, `exit_code`,
`status`) · `file_change` (`changes: [{path, kind}]`, `status`) · `error` (`message`)

`command_execution` carries `aggregated_output: ""` on `item.started` and the **entire**
output on `item.completed`. There is no incremental delivery, so a live console cannot tail
a long command through this stream.

## Confirmed against the plan's v0.147.0 reading

✅ `thread.started` / `turn.started` / `turn.completed` / `turn.failed` / `item.*`
✅ usage: `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`,
   `output_tokens`, `reasoning_output_tokens`

## Refuted

| Claimed | Reality |
| --- | --- |
| `total_tokens` in usage | **Absent.** The factory sums it itself |
| `model_context_window` in the thread metadata | **Absent.** `thread.started` carries `thread_id` and nothing else |
| `context_window` / `tokens_used` / `percent` in the stream | **Absent.** TUI-only |

Verbatim, the whole of the final event of the simplest run:

```json
{"type":"turn.completed","usage":{"input_tokens":14670,"cached_input_tokens":11008,
 "cache_write_input_tokens":0,"output_tokens":6,"reasoning_output_tokens":0}}
```

### What that means for §18.5's live context percentage — it is still possible

The stream cannot supply the denominator, but `~/.codex/models_cache.json` can, and the
factory already reads it (§4.5, `p0-12-models.md`):

```
percent = input_tokens / (context_window × effective_context_window_percent / 100)
        = input_tokens / (272 000 × 0.95)
```

So §18.5 survives, with one design change: **the console's denominator comes from
`config/models.toml`, seeded from the model cache — not from the event stream.** The
factory must also handle the model being switched mid-run, because no event names the model.

`reasoning_output_tokens` was `0` on runs that plainly reasoned and `24`/`33`/`165` on
others. Treat it as a lower bound for cost, not a measurement.

## Failure shape

`codex exec` with an unknown model exits **1** and emits, in order: `thread.started`,
`item.completed`(`type: error`, metadata warning), `turn.started`, `error`, `turn.failed`.
The `error.message` is a JSON document **encoded as a string** — the parser must
`json.loads` it a second time to reach `status` and `error.type`.

**An `item` of `type: "error"` is not a run failure.** The `--dangerously-bypass-hook-trust`
warnings arrive in exactly that shape, twice, on a run that then succeeds
(`fixtures/codex-exec-hook-bypass.jsonl`). Only `turn.failed` and the process exit code
mean failure.

## Hook output does not appear in the JSON stream

When `protect_paths.mjs` blocked an edit, the block was written to **stderr** as a plain
line, not as an event:

```
ERROR codex_core::tools::router: error=Command blocked by PreToolUse hook: Refusing to
edit uv.lock - regenerate with `uv lock`, never hand-edit.
```

The stream showed only the agent's own summary of what happened. **The factory must capture
stderr alongside `events.jsonl`**, or every hook denial becomes invisible in the evidence
trail. `fixtures/codex-exec-hook-bypass.stderr.txt` is the fixture for that parser.

## Fixtures

| File | What it exercises |
| --- | --- |
| `codex-exec-hello.jsonl` | minimal happy path, 4 events |
| `codex-exec-command.jsonl` | `command_execution` started→completed |
| `codex-exec-file-change.jsonl` | `file_change` item |
| `codex-exec-turn-failed.jsonl` | `error` + `turn.failed`, doubly-encoded message |
| `codex-exec-hook-bypass.jsonl` | `item` of `type: error` on a **successful** run |
| `codex-exec-hook-bypass.stderr.txt` | a hook denial, stderr only |
