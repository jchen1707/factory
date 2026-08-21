---
description: Load this repository's architectural standards into context
---

Read `docs/architecture.md` now, in full.

Then summarise, in 5–8 bullets, the standards most relevant to the current task — the layering
rule and which direction dependencies point, interface conventions, where data crossing a
boundary gets validated, where configuration and secrets are allowed to be read, typing rules,
what the test tiers are, and the approved stack.

Summarise what the file actually says, not what you expect a repository like this to say. The
summary is a working aid; `docs/architecture.md` remains the authority, and where the two
disagree you reread the source.

If the user has described a change, flag any part of it that would violate a standard
**before** any implementation — especially a change to the approved stack or a crossing of a
layer boundary. Those are the two that are cheap to prevent and expensive to unpick.
