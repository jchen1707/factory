# Secrets and API keys

How to add a key, where to put it, and what to do when one leaks.

This is shared harness doctrine. Each repo keeps its own `docs/agents/secrets.md` naming the
files that hold and declare its values.

## The rule behind every step below

**A secret is compromised the moment its value enters an agent transcript.** The value reaches
the context window, the transcript file on disk and the API request body in one action. No
later edit removes it, and deleting the file afterwards undoes none of it. **Rotation is the
only remedy.**

Every practice here keeps the literal value out of the model's input. Keeping it out of git is
a separate, already-solved problem — the two are not the same goal, and solving one does not
solve the other.

## Decide which kind of secret it is

Answer one question first: **which process reads this value?** The answer decides everything
else.

| Reader                   | Where the value lives                       | Where the name is declared                        |
| ------------------------ | ------------------------------------------- | ------------------------------------------------- |
| A server or build step   | the repo's ignored env file                 | the repo's env example file                       |
| A client bundle          | the repo's ignored env file                 | the repo's typed env module, and the example file |
| A managed MCP connection | the tool that owns it (e.g. Docker Desktop) | nothing in the repo at all                        |

A value read by a client bundle is **shipped to users**. It is not a secret in the sense this
page means, whatever it is named — never put a private key behind one.

## Never paste the value

- Write the value into the env file with a shell redirect or an editor, not by pasting it into
  a prompt and asking an agent to write it.
- Refer to a secret by its **name** in every conversation, plan, ticket and PR body.
- When an agent needs to confirm a key is set, check that the variable is non-empty — never
  echo it.
- A value that has been pasted is burned. Rotate it; do not try to scrub the transcript.

## Adding one

1. Add the **name**, with an empty or obviously fake value, to the repo's env example file.
2. Declare it wherever the repo requires declaration — a typed env module, a settings class.
3. Put the real value in the ignored env file, by hand.
4. Confirm the ignored file is actually ignored before the first commit.

## When one leaks

Rotate first, then clean up — in that order, and do not reverse them.

1. **Rotate the credential at its source.** Until this is done nothing else matters.
2. Remove the value from wherever it landed.
3. If it reached a commit, treat the history as public: rotation already happened in step 1,
   so history rewriting is cleanup, not remediation.
4. Note the incident in the session report so the next agent knows the old value is dead.

## What the guard layers can and cannot do

The harness gates protected paths and denies reads of known secret files, and those gates are
worth keeping. But they match **paths**, not values. A secret pasted into a prompt, embedded
in a URL, or written to a file the gate does not know about passes straight through.

The gates are a backstop for mistakes. They are not the reason the practices above exist.
