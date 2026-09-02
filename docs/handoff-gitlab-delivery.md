# Handoff — GitLab delivery, and the in-sandbox bypass

Written 2026-08-31, **updated 2026-09-01 when the wiring landed and a real merge request
was opened from inside a sandbox.** Read [the 2026-09-01 section](#2026-09-01--wired-proven-and-two-corrections)
first: it corrects two claims below that measurement overturned, and it replaces "What is
left" almost entirely.

Three commits land with this file:

1. **GitLab delivery from the host** (2026-08-31) — complete, tested, verified against the
   live instance. This is still the default, and the only path for GitHub projects.
2. **In-sandbox delivery** (2026-08-31) — `delivery/sandbox_gitlab.py`, unit-tested but
   wired to nothing. A checkpoint, not a feature.
3. **The wiring** (2026-09-01) — registry, policy, deliver, doctor, preflight, and the two
   corrections that the first real push forced.

Read the second half before touching any of them. It reverses a boundary the rest of
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
| Sandbox image | `gh`, `curl`, `git` present; **`glab` MISSING**; no `~/.ssh`. ORAN CA not trusted **and not needed** — see correction 1 |
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

Exactly one thing, and it is James's because it needs the credential:

```
sbx secret set-custom --sandbox factory-build-nemoclaw-dev \
    --host 172.18.194.183 --env FACTORY_GITLAB_TOKEN --value <the glpat>
```

`set-custom` has no stdin flag, so the value lands in the shell history and, briefly, in
the process table. Read it into a variable first (`read -rs`), and `unset` it after.

`factory doctor` reports this as a `FAIL` row until it is done, and the sandbox preflight
fails the run before any model spend rather than discovering it at the push. Nothing else
is outstanding.

## What is verified, and what is not

Verified on the host: `glab` auth against the live instance, TLS trust with verification
**on**, the full suite / ruff / mypy green.

Verified from inside a sandbox, 2026-09-01, in `factory-build-python-harness-2`:

| Measured | Result |
| --- | --- |
| `curl https://172.18.194.183/api/v4/version`, **no** `--cacert` | `401` — TLS fine, the proxy's CA is trusted |
| the same with `PRIVATE-TOKEN: <placeholder>` on `/api/v4/user` | `200` |
| `git ls-remote` with `http.extraHeader: PRIVATE-TOKEN` | `could not read Username` — GitLab wants Basic |
| `git ls-remote` with `Authorization: Basic base64(oauth2:<placeholder>)` | refs listed — the proxy substitutes inside the base64 |
| `git clone`, commit, `git push` over HTTPS | branch `factory/delivery-probe` pushed |
| `POST /merge_requests` targeting `james/feat/prototype` | `!1` opened |

**Not** verified: a full `factory run` end to end through the in-VM path. The adapter, the
dispatch, the credential lifecycle and the preflight all have tests, and the mechanism
they drive has now been exercised for real — but no ticket has been taken from claim to
merge request through it. That remains the first real run, and it needs the secret above.

Housekeeping from the probe: MR `!1` and the branch `factory/delivery-probe` on
`nemoclaw-test` exist only as that proof, and can be closed and deleted. The credential
files the probe wrote into the sandbox were removed.

## 2026-09-01 — wired, proven, and two corrections

The four wiring steps are done:

1. **Registry** — `[projects.<name>.sandbox_delivery]` with `api_url`, `project_path` and
   `placeholder_env`. Validated at load: refused for a `github` forge, refused alongside
   `requires_clone`, refused half-specified. **No `ca_file` key**, see below.
2. **Policy** — `capability_secrets(..., declared=…)` admits the declared name, and only
   when `sbx inspect` reports `source: custom`. A *service* secret of the same name still
   blocks, because that would be a real credential wearing the declaration's clothes. The
   admission travels on `SandboxSpec.allowed_custom_secrets`, so `SbxAdapter.ensure`
   applies the same rule. Deleting the sub-table restores full strength with **no code
   revert**, and `tests/unit/test_policy.py` pins exactly that.
3. **`steps/deliver.py`** — `forge.for_delivery` (separate from `for_project`, so a read
   cannot accidentally get the in-VM adapter and a write cannot accidentally miss it),
   `install_credential` before the push, `revoke_credential` in a `finally`. A missing
   placeholder is a named `Blocked` that prints the provisioning command.
4. **`AGENTS.md`** — the credential clause now carries the one declared exception and why
   it is still literally true.

Plus two that were not on the list: a `sandbox delivery` row in `factory doctor`, and a
preflight check, so an unprovisioned secret fails at the start of a run rather than at the
end of a paid one.

### Correction 1: there is no CA to inject

The host needs the ORAN issuer. The sandbox does not, because it never sees it. All
sandbox egress goes through `HTTPS_PROXY=http://gateway.docker.internal:3128`, which
terminates TLS and re-presents its own leaf under `Docker Sandboxes Proxy CA` — already in
the image trust store, and also in `PROXY_CA_CERT_B64`. Pinning the ORAN leaf as `--cacert`
would pin a certificate the VM is never offered. `CA_PATH` and the registry's `ca_file` key
are both gone.

The earlier "without it curl exits 60" measurement is not reproducible and was most likely
taken before the egress allow rule existed, when the failure was a proxy refusal rather
than a trust failure.

### Correction 2: git needs Basic, and the proxy substitutes inside base64

`PRIVATE-TOKEN` authenticates `/api/v4` and nothing else. GitLab's git endpoint answers
`401 WWW-Authenticate: Basic`. The useful part is that `sbx`'s proxy substitutes the
placeholder **inside the base64 of a Basic credential**, so
`Authorization: Basic base64("oauth2:<placeholder>")` pushes — while the same header on
`/api/v4` does not authenticate, because the API does not take `oauth2:` Basic.

So `install_credential` writes **two** files: `header` for curl and `gitconfig` for git.
`push` reaches the second through `GIT_CONFIG_GLOBAL` rather than
`git -c http.extraHeader="$(cat …)"`, which would put the credential back into git's argv
— the exact `/proc` exposure the file exists to avoid.
