---
description: Start the development server
---

Read `harness.config.json` at the repository root and start the server its `dev.run` names.
Run `install` first if dependencies changed.

If there is no `dev` block, this repository has no development server — say so and stop.

Once it is up, report `dev.url` and confirm whatever `dev.readyCheck` names actually
responds. "The process started" is not the same claim as "the application is serving", and
reporting the first as the second is how a broken entry point survives a whole session.

If the entry point the server expects does not exist yet, say that plainly and stop. **Do not
create it** unless the user asks — an application skeleton invented to make a command succeed
is the wrong shape by construction, because nothing has specified it.

Once it is running, drive it rather than guessing. Where this repository documents an
interactive verification loop — a browser automation server, a client, a console — its
`docs/` says so.

> **Image-input consent (hard rule):** a screenshot or a screencast feeds an image into the
> model. **Stop and ask the user for permission first**, and do not proceed until they
> confirm. Prefer a text snapshot of the interface where one is available: it is a better
> assertion target than a picture, and it needs no consent.

The interactive loop is fast and asserts nothing. When you settle on behaviour worth keeping,
write it into the test suite — that is what will still be checking it in six months.
