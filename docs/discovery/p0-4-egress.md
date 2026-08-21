# P0-4 — egress audit

`sbx policy ls`:

```
POLICY         SOURCE   APPLIES TO   SUMMARY
local-policy   local    all          network: 194 allow; filesystem read: 1 allow; filesystem write: 1 allow
```

One policy, applying to **all** sandboxes. There is no per-sandbox rule today, so
`factory-review-*`'s "model endpoint only" posture (§8.7) has to be built from scratch with
`--deny-network` at creation time or `sbx policy deny --sandbox`.

## Rule groups

| Rule id | Editable | Rough contents |
| --- | --- | --- |
| `default-ai-services` | yes | `**.openai.com`, `**.chatgpt.com`, `api.anthropic.com`, `**.factory.ai`, `**.cursor.sh`, `gemini.google.com`, `api.perplexity.ai`, … |
| `default-package-managers` | yes | pypi, npm, crates, maven, go, hex, rubygems, `astral.sh`, `nodejs.org`, … |
| `default-code-and-containers` | yes | `**.github.com`, `**.githubusercontent.com`, `**.gitlab.com`, `**.docker.io`, `ghcr.io`, `quay.io`, `public.ecr.aws`, … |
| `default-cloud-infrastructure` | yes | `**.amazonaws.com`, `**.googleapis.com`, `**.blob.core.windows.net`, `**.hashicorp.com`, `vercel.com`, `supabase.com`, `figma.com`, … |
| `default-os-packages` | yes | ubuntu/debian/alpine/fedora archives, `apt.llvm.org`, `packagecloud.io` |
| `default-cert-validation` | yes | OCSP/CRL endpoints on :80 and :443 |
| `default-fs-read-allow-all` | **no** | `**` |
| `default-fs-write-allow-all` | **no** | `**` |
| `96ae2d07…`, `d39c5d89…` | yes | `cdn.playwright.dev`, `playwright.download.prss.microsoft.com` (added by hand) |

## Wildcards to prune before Phase 2, as §8.7 requires

Broadest first — each is a whole cloud, reachable from an unattended agent:

```
**.amazonaws.com          **.googleapis.com         **.blob.core.windows.net
**.googleusercontent.com  **.public.blob.vercel-storage.com
**.gcr.io                 **.docker.io              **.docker.com
**.github.com             **.githubusercontent.com  **.gitlab.com
**.oaiusercontent.com     **.chatgpt.com            **.openai.com
```

`**.amazonaws.com` and `**.googleapis.com` are the two worth removing first: they are
exfiltration-shaped (any bucket, any project) and neither Python nor frontend gates need
them. `default-cloud-infrastructure` as a whole has no role in either stack's gate set —
removing the entire rule group is a single command and the cheapest large win:

```sh
sbx policy rm network --id default-cloud-infrastructure
```

## Two things the audit found that §8.7 does not account for

1. **Filesystem read and write are `**` and cannot be narrowed from the CLI**
   (`filesystem rules cannot be modified with the CLI`). §8.7's table implies filesystem
   containment is available; it is not. Containment on the filesystem therefore comes
   *only* from which workspaces are mounted — which makes `--clone` and `:ro` mounts the
   real control, not a nicety. See `p0-10-gate-timing.md`.
2. **Policy scope is global.** `sbx inspect` reports `network_policy: {scope: global}` on
   every existing sandbox. The reviewer's tighter egress must be applied at
   `sbx create --deny-network …`, per-sandbox, and a local deny can only narrow — which is
   exactly what §8.7 wants, but it has to be authored, and it is not inherited.
