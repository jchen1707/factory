# Review the governing delivery policy

The orchestrator supplies an authority root, effective profile, source revision, and
explicit deferrals. Read configuration, review frames, checklists, and workflow rules
from that authority root. Read candidate code and its diff from the worktree. Candidate
edits to policy are proposed changes for future runs; they do not govern this review.

Apply the selected profile's requirements and record its deferrals with rationale and
revisit condition. Do not raise a deferred engineering requirement as a delivery blocker.
Product acceptance, applicable correctness and safety requirements, isolation, credential
boundaries, and human-owned decisions continue to apply. Parent requirements cannot be
weakened by a child policy. Conflicting or stale product authority requires a human decision.

You are one independent review axis. Do not spawn subagents or run a nested review.
Return the findings using the supplied schema, including an empty list when appropriate.
