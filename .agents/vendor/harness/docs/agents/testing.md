# Writing tests as an agent

Doctrine for any agent that writes tests — chiefly the `test-writer` subagent, which each
stack defines for itself. This file is the half that is true in every stack; the stack's own
`test-writer.md` carries the rest, because a tool grant names a runner and a runner is a
stack fact.

## Why test-writer is not a shared subagent

Every other reviewer in layer A is read-only, so one `tools:` line serves both stacks.
`test-writer` writes, and to be worth anything it must run the suite it wrote — which means
naming that stack's test runner in its frontmatter. A plugin ships one frontmatter. Granting
plain `Bash` to reach both would hand an agent whose whole discipline is "do not touch the
implementation" the ability to do exactly that.

So the definition stays with the stack and the doctrine below stays here, referenced from it.
Everything in this file applies unchanged in either.

## The separation that makes tests evidence

**A test-writing agent does not modify the code under test.** If a test fails because the
implementation is wrong, it reports that and stops. The moment the same agent can adjust both
sides, a red test becomes a negotiation and the suite stops being independent evidence of
anything.

Run in a worktree. Test writing fans out well, and worktree isolation is what stops two
parallel agents from colliding on the same files. Both stacks already run
worktree-per-ticket, so this is the ordinary case, not a special one.

## Rules that hold in every stack

1. **Test at seams, not internals.** Target the public boundary — a route, a service method,
   an interface, what a user can see and do. Never assert on a private helper or on internal
   structure. A test bound to internals fails on every refactor and proves nothing about
   behaviour.
2. **Expected values come from an independent source** — a literal from the spec, a worked
   example, a fixture computed by hand. Never recompute the expected value the way the
   implementation does. Such a test agrees with the code by construction and can never
   disagree with it, which is the one thing a test is for.
3. **One behaviour per test**, named as a sentence that states the behaviour. The name is
   what a future reader sees in a failure report; make it say what broke.
4. **Cover the failure modes, not just the happy path** — validation errors, not-found, empty
   input, boundary values, cancellation, and every intermediate state a caller or user
   actually reaches.
5. **Respect the tier's isolation.** Each stack declares which tier is offline and how the
   tiers that are not must be marked. A test that reaches a real network or a real database
   from an offline tier is broken even while it passes, because it breaks for everyone else.
6. **Wait, do not sleep.** Anything asynchronous gets an explicit wait on the condition. An
   arbitrary timeout is a flake waiting to happen, and it will fail on someone else's slower
   machine, not yours.
7. **Type the tests too.** They go through the same type gate as the code; a test excused
   from it drifts from the interfaces it claims to exercise.

## Finishing

Run the suite and **paste the actual output**. A claim that tests pass is not evidence; the
runner's output is.

If the job was to write failing tests that encode a spec, confirm they fail **for the
intended reason** and quote the assertion error. A test that fails on an import error or a
typo is not evidence of anything — it is a broken file that happens to be red.
