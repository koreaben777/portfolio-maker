# Team Based Review Loop 10 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `b1ff2899a548f8ac51200e129df8ab01c0d28a2a`

Status: NEEDS WORK

## Source

This fixback is derived from `docs/reviews/2026-07-10-team-based-review-loop-9-rereview.md`.

## Required Fixes

### P1

1. Make legacy snapshot cleanup an idempotent phase that runs before early return, covers same-content and changed-content legacy rows, retries after interruption, and deletes only through a validated managed-directory descriptor with relative inode/type-checked unlink.
2. Reject timestamped Chrome password and Firefox login export names at discovery, ingestion, and public artifact boundaries.

### P2

1. Replace non-force approval sample check-then-write with exclusive atomic creation; retain overwrite only for explicit `--force`.
2. Use one canonical GitHub `owner/repo` parser for approval and comparison; reject dot components and invalid leading forms.

### P3

1. Align line 19 of both architecture specs with the 0.1.0 master-profile plus portfolio-skeleton contract.
2. Delete superseded snapshot path/hash lookup APIs and the pass-through approval forbidden-path wrapper after updating callers/tests.

## Required Verification

- Add focused red tests for cleanup interruption/retry, changed-content legacy state, managed-directory replacement, Chrome/Firefox suffixes, exclusive approval creation, and malformed repository identifiers.
- Use synthetic placeholders only.
- Run the focused suite, full suite, Fable findings gate, adversarial self-review, `git diff --check`, and `git show --check --format=short HEAD`.
- Commit only implementation, tests, and product documents. Preserve all review reports untracked and do not push.

## Initial Outcome

NEEDS WORK. Apply one minimal `@codex-fable5` fixback, report completion with `[TEAM_REVIEW_FIX_DONE_10]`, and return the result for the same four-agent re-review.
