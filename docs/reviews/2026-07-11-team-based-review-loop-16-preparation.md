# Team Based Review Loop 16 - Preparation

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Starting branch / HEAD: `codex/portfolio-maker-mvp` / `857f50114b486f6a5b90804843df99be1ee1310e`

Prepared status: READY FOR NEXT AUTHORIZED LOOP

## Reviewer Team To Retain

- Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail`.
- Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): logical flow.
- Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): plan/spec and contract.
- Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): bug and reality checker.

## Accepted Starting Finding

1. P1: the Loop 15 `threading.RLock()` and thread-local depth serialize only same-process repository operations. A separate Python process or CLI invocation can still call a normal repository read, run `ensure_managed_directory()`, restore the DB directory to writable, and reopen the empty-journal hard-link mutation interval while an outer write is active.

## Required Red Evidence

- During a parent write operation, inject after the `after write transaction` validation.
- In that injection, run a child Python process that calls `SQLiteRepository(paths.db_path).table_names()`.
- After the child read returns successfully, attempt to replace `portfolio.db-journal` with an empty hard link to an external file.
- On the current starting HEAD, this reproduces the defect:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
xproc_rc=0 xproc_stdout=True xproc_stderr=
```

- The fixed regression must assert the external inode remains size `0` and the child process cannot reopen the directory chmod window while the parent holds the repository critical section.

## Implementation Constraints

- Use the `@codex-fable5` findings gate: reproduce, add a focused red subprocess regression, apply the smallest root-cause fix, and self-review.
- Keep the fix inside `SQLiteRepository` and existing managed-file primitives unless direct evidence proves lower-level work is necessary.
- Use a standard-library inter-process file/descriptor lock or equivalent repository-owned lock around the full database-family critical section. Preserve same-process reentrant depth handling to avoid nested-read deadlock.
- Hold the inter-process lock across `ensure_managed_directory()`, sidecar preparation, directory chmod, SQLite connection, transaction/read, validation, commit, close, chmod restore, and lock release.
- Do not add a custom SQLite C binding, VFS, second repository layer, remote dependency, or unrelated cleanup.
- Do not use SQLite `MEMORY` or `OFF` journal modes.
- Preserve prior closures: same-process nested read, single-operation empty journal, `user_version`, read `mode=ro`, retryable busy/locked CLI text, raw-connect removal, write `mode=rw`, post-commit identity validation, strict hydration, guarded schema creation, repeated discovery, and WAL/SHM compatibility.
- Preserve all `docs/reviews/*` files. Do not rebase, merge, edit the remote README work, or push before same-team PASS.

## Completion Evidence

- Red-to-green subprocess regression for the cross-process child-read race.
- Existing Loop 15 and Loop 14 regressions.
- Full `pytest` suite.
- Direct external-inode, inter-process lock, same-process reentrancy, `user_version`, WAL/SHM, CLI contention, and directory-mode restoration checks.
- `git diff --check`, `git show --check --format=short HEAD`, Fable findings gate, and same-team re-review.
