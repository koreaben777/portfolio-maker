# Team Based Review Loop 15 - Preparation

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Starting branch / HEAD: `codex/portfolio-maker-mvp` / `2a1b397e2c87f6d3c00edcf1808c5e5e4fb3ac8b`

Prepared status: READY FOR NEXT AUTHORIZED LOOP

## Reviewer Team To Retain

- Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail`.
- Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): logical flow.
- Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): plan/spec and contract.
- Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): bug and reality checker.

## Accepted Starting Finding

1. P1: an overlapping repository read can call `_open_database_family()` and restore the managed database directory to writable while an outer write transaction is already past `after write transaction` validation, allowing an empty hard-linked `portfolio.db-journal` to be opened and expanded before final validation rejects it.

## Required Red Evidence

- During a write operation such as `upsert_source()`, inject after the `after write transaction` validation.
- Inside that injection, perform a normal overlapping repository read such as `SQLiteRepository(paths.db_path).table_names()`.
- After the overlapping read returns, attempt to replace `portfolio.db-journal` with an empty hard link to an external file.
- On the current starting HEAD, this reproduces the defect:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
```

- The fixed regression must assert the external inode remains size `0`.

## Implementation Constraints

- Use the `@codex-fable5` findings gate: reproduce, add a focused red test, apply the smallest root-cause fix, and self-review.
- Keep the fix inside `SQLiteRepository` and existing managed-file primitives unless direct evidence proves a lower-level mechanism is necessary.
- Do not add a custom SQLite C binding, VFS, second repository layer, remote dependency, or unrelated cleanup.
- Do not use `PRAGMA journal_mode = MEMORY` or `OFF`.
- A repository-wide critical section is acceptable if it is implemented with standard-library OS/file locking or a strictly local primitive and includes same-process reentrant depth handling so nested repository reads cannot unlock the directory before the outer operation exits.
- Preserve prior closures: empty late-journal external inode unchanged, `user_version` preservation, read `mode=ro`, retryable busy/locked errors, raw-connect removal, write `mode=rw`, post-commit identity validation, strict hydration, guarded schema creation, repeated discovery, and WAL/SHM compatibility.
- Preserve all `docs/reviews/*` files. Do not rebase, merge, edit the remote README work, or push before same-team PASS.

## Completion Evidence

- Focused red-to-green regression for the overlapping-read directory-unlock race.
- Existing Loop 14 regressions for empty journal, `user_version`, healthy writer read, CLI busy handling, persisted WAL/SHM, and visible/detached state.
- Full `pytest` suite.
- Direct external-inode, lock/reentrancy, `user_version`, WAL/SHM, CLI contention, and directory-mode restoration checks.
- `git diff --check`, `git show --check --format=short HEAD`, Fable findings gate, and same-team re-review.
