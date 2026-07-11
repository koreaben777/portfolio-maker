# Team Based Review Loop 15 - Initial Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `2a1b397e2c87f6d3c00edcf1808c5e5e4fb3ac8b`

Status: NEEDS WORK

## Evidence Checked

- Loop 14 re-review result and Loop 15 preparation document.
- Same four reviewer lanes: Parfit (`@ponytail`), Epicurus (logical flow), Erdos (plan/spec contract), and Cicero (bug/reality checker).
- Full suite: `182 passed`.
- Selected Loop 14 direct tests: `7 passed`.
- `git diff --check` and `git show --check --format=short HEAD` passed.
- Independent reproduction confirmed the remaining accepted P1.

## Closed Findings

- Empty late-journal replacement after `after connect` is blocked for a single active repository operation.
- Nonzero SQLite `user_version` is preserved across reads and writes.
- Read operations now use `mode=ro` and do not acquire `BEGIN IMMEDIATE`.
- `SQLITE_BUSY` / `SQLITE_LOCKED` maps to concise retryable contention guidance.
- Prior raw-connect, `mode=rw`, post-commit visible identity, strict hydration, guarded schema creation, repeated discovery, and WAL/SHM compatibility closures remain covered.

## Findings

### P1 - Overlapping repository read can reopen the sidecar mutation interval

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:141`, also `:88` and `:210`.
- The Loop 14 fix uses directory `fchmod` as the critical-section guard. A normal overlapping repository read calls `_open_database_family()` and `ensure_managed_directory()`, restoring `.portfolio-maker` to writable while an outer write transaction has already passed `after write transaction` validation.
- Direct reproduction: during `upsert_source()`, inject after `after write transaction`, call `SQLiteRepository(paths.db_path).table_names()`, then replace `portfolio.db-journal` with an empty hard link. The outer write raises `RepositoryError`, but only after SQLite expands the external inode:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
```

- Minimal next action: add a real repository-wide critical-section mechanism, including same-process reentrant depth handling, so nested or overlapping repository operations cannot unlock the directory before the outer write completes. Add the focused red-to-green regression described in `docs/reviews/2026-07-11-team-based-review-loop-15-preparation.md`.

## Ponytail Cleanup

- Keep the fix inside `SQLiteRepository` and existing managed-file primitives.
- Do not add a custom SQLite C binding, VFS, second repository layer, remote dependency, or unrelated cleanup.
- Do not use SQLite `MEMORY` or `OFF` journal modes.

## Next Minimal Checks

- Reproduce the P1 with the overlapping read and empty external hard link.
- Add a focused regression asserting the external inode remains size `0`.
- Rerun the prior Loop 14 direct tests, full suite, `git diff --check`, and `git show --check --format=short HEAD`.
