# P0-12 — model cache freshness

```
client_version : 0.147.0        codex --version : codex-cli 0.147.0   ✅ match
fetched_at     : 2026-08-20T21:36:22Z   (today; the plan recorded 2026-08-15)
```

The cache re-fetched itself during this session, so it is current with the CLI. No TUI
`/model` visit is needed.

| slug | display | default | supported efforts | `context_window` | `max_context_window` | visibility |
| --- | --- | --- | --- | --- | --- | --- |
| `gpt-5.6-sol` | GPT-5.6-Sol | `low` | low…max, **ultra** | 272 000 | 872 000 | list |
| `gpt-5.6-terra` | GPT-5.6-Terra | `medium` | low…max, **ultra** | 272 000 | 872 000 | list |
| `gpt-5.6-luna` | GPT-5.6-Luna | `medium` | low…max | 272 000 | 872 000 | list |
| **`gpt-reserve`** | GPT-Reserve | `medium` | low…max | 272 000 | 872 000 | **hide** |
| `gpt-5.5` | GPT-5.5 | `medium` | low, medium, high, xhigh | 272 000 | 272 000 | list |
| `gpt-5.4` | GPT-5.4 | `medium` | low, medium, high, xhigh | 272 000 | 1 000 000 | list |
| `gpt-5.4-mini` | GPT-5.4-Mini | `medium` | low, medium, high, xhigh | 272 000 | 272 000 | list |
| `codex-auto-review` | Codex Auto Review | `medium` | low…max | 272 000 | 872 000 | **hide** |

**Every §4.5 routing target still exists**, with the efforts §4.5 claims. `ultra` is offered
by `gpt-5.6-sol` and `gpt-5.6-terra` only — exactly where §4.5's builder prohibition bites.
`effective_context_window_percent` is `95` for all eight.

Three corrections to §4.5's table:

1. **`gpt-reserve` is missing from it.** Eight models, not seven. It is `visibility: hide`,
   so it should not be a routing target — but validation rule 2 ("every role names a model
   present in the measured model list") reads the cache, and a table that omits a model it
   would accept is a table that has drifted. Add it, marked hidden, alongside
   `codex-auto-review`.
2. **`max_context_window` differs per model** (272 000 / 872 000 / 1 000 000) while
   `context_window` is uniformly 272 000. §4.5 says "Every model reports
   `context_window: 272000`", which is true, but the console's denominator (§18.5, and
   `codex-events.md`) must use `context_window × 0.95` and not the larger figure.
3. The effort list lives under **`supported_reasoning_levels`**, as a list of
   `{effort, description}` objects, and the default under `default_reasoning_level` — not
   the field names §4.5 implies. `routing.py`'s validation must read those keys.

Verbatim, the description that justifies §4.5's builder prohibition:

> `ultra` — *"Maximum reasoning with automatic task delegation"*

Still accurate in today's cache.
