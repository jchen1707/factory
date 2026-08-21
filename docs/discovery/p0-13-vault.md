# P0-13 — vault snapshot baseline

`OBSIDIAN_VAULT_DIR=/Users/james/Documents/Obsidian Vault` (set in `~/.zshrc:13`).
`OBSIDIAN_VAULT_DIRECTORY` is **unset** in the shell — `~/.zshrc:219` emits it only as a
`shell_environment_policy.set.…` line for Codex, which is precisely the two-scope split
§9.2 documents. Confirmed, no action.

| Walk | Files | Bytes | `mtime + size + sha256` |
| --- | --- | --- | --- |
| everything | **162** | **15.7 MB** | 21.6 ms |
| excluding `.obsidian`, `.git`, `.trash` | 98 | 1.9 MB | 2.4 ms |
| — warm second pass | 98 | 1.9 MB | 3.0 ms |
| — `stat` only, no hashing | 98 | — | 0.7 ms |

**162 files / 15.7 MB confirms §8.5 exactly.** The full hashing walk is 22 ms, so running it
twice per attempt costs ~44 ms. The §8.5 workaround is free; no caching, no incremental
scheme, no `.obsidian` exclusion needed for performance.

If the exclusion is wanted anyway it is for correctness rather than cost: `.obsidian/`
holds workspace UI state that changes when James merely opens a pane, which would show up
as a spurious vault diff attributed to the run. Recommend excluding `.obsidian`, `.git` and
`.trash` from the *diff*, and saying so in §8.5.
