# Team Based Review Loop 14 - Preparation

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Starting branch / HEAD: `codex/portfolio-maker-mvp` / `1b25a74da43ca2381013e6ea8c79144d7b9cf2cf`

Prepared status: READY FOR NEXT AUTHORIZED LOOP

## Reviewer Team To Retain

- Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail`.
- Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): logical flow.
- Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): plan/spec and contract.
- Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): bug and reality checker.

## Accepted Starting Findings

1. P1: an empty hard-linked journal inserted after `after connect` validation can be modified by `BEGIN IMMEDIATE` before family validation rejects it.
2. P2: `PRAGMA user_version = user_version` resets nonzero SQLite migration metadata to zero.
3. P2: read operations acquire an immediate write transaction, and healthy `SQLITE_BUSY`/`SQLITE_LOCKED` contention is misclassified as database damage.

## Required Red Evidence

- Insert an empty hard-linked `portfolio.db-journal` immediately after the `after connect` validation and assert the external inode remains unchanged.
- Set a nonzero `PRAGMA user_version`, execute representative repository reads and writes, and assert the value is preserved.
- Hold a healthy database with another writer; verify read behavior and verify write/CLI contention produces a retryable error without damage guidance.

## Implementation Constraints

- Use the `@codex-fable5` findings gate: reproduce, add focused red tests, apply the smallest root-cause fix, and self-review.
- Separate guarded read and write lifecycles only as far as required by lock semantics.
- Correct the false late-journal regression before claiming the sidecar interval closed.
- A sidecar-free policy or SQLite VFS/open-flags solution requires explicit design evidence; do not add a second repository layer or dependency.
- Preserve raw-connect removal, `mode=rw`, post-commit identity checks, strict hydration, WAL/SHM compatibility, and repeated discovery.
- Preserve all `docs/reviews/*` files. Do not rebase, merge, edit the remote README work, or push before same-team PASS.

## Completion Evidence

- Focused regressions for all three accepted findings.
- Existing Loop 13 attack-path regressions.
- Full `pytest` suite.
- Direct external-inode, `user_version`, healthy-lock, visible/detached-state, and CLI checks.
- `git diff --check`, `git show --check --format=short HEAD`, Fable findings gate, and same-team re-review.
