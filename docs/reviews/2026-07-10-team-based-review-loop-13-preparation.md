# Team Based Review Loop 13 - Preparation

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Starting branch / HEAD: `codex/portfolio-maker-mvp` / `c1baaa46a4e29d56d6c9eff1e8ac9b53abcacfaa`

Prepared status: READY FOR NEXT AUTHORIZED LOOP

## Reviewer Team To Retain

- Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail`.
- Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): logical flow.
- Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): technical writer.
- Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): reality checker.

## Accepted Starting Findings

1. P1: remove or constrain the raw write-capable `SQLiteRepository.connect()` path so it cannot bypass pinned family validation.
2. P1: prevent main target creation when replacement occurs inside connect and prevent external sidecar modification when a sidecar is introduced after validation.
3. P1: prevent a replacement inside commit from returning success with persistence detached from the visible workspace DB.
4. P2: validate all hydrated SQLite field types, including required/optional text BLOB cases, and use controlled `RepositoryError` output.

## Required Red Evidence

- Raw `connect()` followed by late journal hard-link insertion.
- Main path replacement to a symlink with a missing external target during `sqlite3.connect()`.
- Late journal hard-link insertion immediately before DML.
- Main path replacement inside commit with visible/detached state comparison.
- Required and optional text fields stored as BLOBs in an existing database.

## Implementation Constraints

- Inspect and reproduce before changing code; use the `@codex-fable5` findings gate.
- Prefer deleting/restricting `connect()` and reusing existing `_connection()`/managed-file primitives.
- Investigate transaction ordering and SQLite `mode=rw` before considering larger architecture.
- Do not add a custom C binding, VFS, second repository layer, or remote dependency without explicit approval.
- Preserve all `docs/reviews/*` files and do not push until re-review PASS.

## Completion Evidence

- Focused SQLite/CLI regressions.
- Full `pytest` suite.
- Direct external-sentinel and visible/detached-state checks.
- `git diff --check`, `git show --check --format=short HEAD`, Fable findings gate, and same-team re-review.
