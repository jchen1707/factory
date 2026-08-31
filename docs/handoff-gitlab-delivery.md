# Handoff — GitLab delivery, and the in-sandbox bypass

Written 2026-08-31. Two commits land with this file:

1. **GitLab delivery from the host** — complete, tested, verified against the live
   instance. This is the default and it works today.
2. **In-sandbox delivery** — `delivery/sandbox_gitlab.py`, complete and unit-tested but
   **not wired to anything**. It is a checkpoint, not a feature. Nothing calls it.

Read the second half before touching the second commit. It reverses a boundary the rest of
this repository is built on, James approved that reversal deliberately, and the reasons the
approval was safe are properties a careless edit can remove without failing a test.

## What shipped, and what it cost to learn

The company GitLab is `https://172.18.194.183` — a bare IP, VPN-only, behind an internal
CA (`O=WAD, CN=ORAN`) that no default trust store carries. That single fact drives most of
the design, and every claim below was measured rather than assumed.

| Measured | Result |
| --- | --- |
| Cert chain served | leaf only; the ORAN CA is **not** in it, so it cannot be harvested from the connection |
| Cert SAN | `IP:172.18.194.183` **and** `DNS:gitlab.ric.teluslabs.net` — but that name is NXDOMAIN, so the IP is the host string |
| Cert validity | to 2035-02-22, so pinning the leaf is stable |
| Host `git` | works already: all `~/nexus-core/*` remotes are SSH via `~/.ssh/id_ed25519_gitlab` |
| Sandbox image | `gh`, `curl`, `git` present; **`glab` MISSING**; ORAN CA not trusted; no `~/.ssh` |
| `glab` version | 1.115.0 — **there is no `--use-keyring`**; the keyring is the default and `--insecure-storage` is the opt-out |

### Host-side delivery (commit 1)

`delivery/forge.py` dispatches on a `forge` key in `projects.toml` (`github` default) to
`delivery/github.py` or `delivery/gitlab.py`. `delivery/body.py` holds the PR/MR body,
hoisted out of `github.py` because none of that evidence is GitHub's.

Three translations in `gitlab.py` are pinned by tests because each fails *silently*:

- **State vocabulary.** `cli.py` compares against the literal `"MERGED"`. An adapter
  passing GitLab's lowercase `merged` through would make a run James had merged refuse to
  complete, for ever, with no error anywhere.
- **URL shape.** `/-/merge_requests/(\d+)`, not `/pull/(\d+)`. Reusing GitHub's regex
  returns `None` and F16 opens a duplicate MR on every re-delivery.
- **`glab mr list` JSON.** Bare array or wrapped object; guessing wrong also defeats F16.

`test_boundaries.py` gained the `glab` half of the never-merges surface. A second forge
adapter walks straight past `_GH_ARGV` — `glab mr merge` contains no `"gh"` literal — so
without it, "the factory never merges" would have quietly become a claim about GitHub only
with nothing failing to say so. Proven by adding a `merge_pr` and watching two tests fail.

`policy.HOST_EXECUTION_DENY` gained `.gitlab-ci.yml`, `**/.gitlab-ci.yml` and `.gitlab/**`.
Without them a `forge = "gitlab"` project got a **weaker** guard than a GitHub one, and the
runners here are self-hosted on the corporate network.

### The registry (commit 1)

`python-harness` is retired — commented out, not deleted, because its comments are measured
findings. `Registry.resolve` refuses a team two projects claim, so BAC could not be shared;
it was handed to `nemoclaw-dev`. Checked first: no `awaiting_human` runs, and the blocked
rows carry no branch, so nothing in the store was orphaned.

**`network_allow` in `projects.toml` is dead config.** It is parsed into `Project` and
referenced nowhere; only `deny_network` reaches `sbx create`. Egress is controlled solely
by daemon-side `sbx policy` rules. Do not add a host there expecting it to do something.

---

## The in-sandbox bypass (commit 2) — read this before changing it

James asked for factory sandboxes to open merge requests themselves. That reverses §13.2.
The concern was raised twice and the decision is his; this section exists so the *reasons
it is safe* survive the next edit.

### Why REST-over-curl and not `glab`

`glab` is not in the sandbox image, and baking it in is a **template** change, which §9.1
fixes at creation — every factory sandbox would need a new name. `curl` is already there.
So `sandbox_gitlab.py` speaks `/api/v4` directly. The host adapter stays the tested default.

### The property that makes this defensible

`sbx secret set-custom` keeps the real `glpat-…` **on the host** and gives the sandbox a
placeholder; the proxy substitutes it into the request headers on the way out. Measured
2026-08-31 in `factory-build-python-harness-2` against `/api/v4/version`:

```
anonymous                                    -> 401
-H "PRIVATE-TOKEN: sbx-cs-AFlXreOjVPUyNl4k"  -> 200
```

**The agent gains a capability, not a credential.** The placeholder is bounded to one host
and one sandbox scope; off-proxy, or aimed anywhere else, it is a meaningless string.
`AGENTS.md`'s rule stays literally true — no credential enters a sandbox — and the amendment
needed is one clause, not a deletion.

A future author who "simplifies" `install_credential` to take a real `glpat-…` has made a
materially different change from the one that was approved. The parameter is called
`placeholder` for exactly that reason: a call site passing the wrong thing reads wrong.

### Gotchas that cost real time

- **`set-custom --env` does not reach `sbx exec` sessions.** `FACTORY_GITLAB_TOKEN` was
  absent before *and* after a stop/restart, while `sbx inspect` still listed the secret.
  The command is EXPERIMENTAL. Read the placeholder from `sbx secret ls` host-side instead;
  it is not a secret, and that is one fewer moving part in the delivery path.
- `sbx secret rm --placeholder <p>` defaults to **global** scope and reports "No custom
  secret found" while the entry is plainly listed. It needs `--sandbox <name>`.
- `set-custom` refuses to update an existing env name in a scope: remove, then re-add.
- There is **no `sbx start`**; `sbx exec` auto-starts a stopped sandbox.
- Built-in services do not include gitlab (`anthropic, cursor, droid, github, google, groq,
  mistral, nebius, openai, openrouter, xai`). `set-custom` is the only route.

### Current live state, outside the repo

These are machine state, not code, and they are **not** reverted by reverting a commit:

- Global egress allow rule `5a17095e-41df-4afa-87f3-b57ef222e414` for
  `172.18.194.183:443`. It is **global**, so it covers `codex-*` sandboxes too. Remove with
  `sbx policy rm network --id 5a17095e-41df-4afa-87f3-b57ef222e414`.
- A custom secret scoped to `factory-build-python-harness-2` — a sandbox belonging to the
  *retired* project. Before a real run, re-create it for `factory-build-nemoclaw-dev`.
- The ORAN leaf is trusted in the host System keychain. Undo with
  `sudo security delete-certificate -c 172.18.194.183 /Library/Keychains/System.keychain`.

## What is left

1. **Registry keys** — a `[projects.<name>.forge]` sub-table: `delivery = "sandbox"`,
   `api_url`, `project_path`, `ca_file`, and the custom-secret placeholder's source.
2. **`policy`** — admit the declared name in `capability_env_names` / `capability_secrets`.
   Gate it on the project's declaration rather than weakening the guard, so that removing
   the declaration restores full strength with **no code revert**. That is the whole reason
   James asked whether this could be put back afterwards, and the answer is yes only if it
   is built this way.
3. **`steps/deliver.py`** — choose the forge, `install_credential` before the push and
   `revoke_credential` in a `finally`. A sandbox outlives one run (§16.5), so a placeholder
   left behind is one available to whatever runs in it next.
4. **`AGENTS.md`** — amend the credential clause to say what is now true. Do not delete it.
5. **A real run**, which is the only thing that will prove any of this end to end.

## What is verified, and what is not

Verified: host `glab` auth against the live instance (`glab mr list` answers for
`nemoclaw-test`); TLS trust with verification **on**; the sandbox reaching `/api/v4` with an
injected CA; proxy substitution returning 200; the full suite, ruff, and mypy green.

Not verified: any merge request actually created by either adapter, and `git push` from
inside a sandbox over HTTPS. `sandbox_gitlab.py` is driven entirely by a recording fake. The
first real run is the proof, and it has not happened.
