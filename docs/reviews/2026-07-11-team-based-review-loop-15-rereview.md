# Team Based Review Loop 15 - Re-Review Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `857f50114b486f6a5b90804843df99be1ee1310e`

Baseline: `2a1b397e2c87f6d3c00edcf1808c5e5e4fb3ac8b`

Status: NEEDS WORK

## Evidence Checked

- Same four reviewer lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: logical-flow and SQLite lifecycle review.
  - Erdos: plan/spec and contract review.
  - Cicero: bug and reality-check validation.
- Implementation commit: `857f50114b486f6a5b90804843df99be1ee1310e` (`fix: serialize SQLite repository operations`).
- Implementation thread completion marker: `[TEAM_REVIEW_FIX_DONE_15]`.
- Independent full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
183 passed in 6.17s
```

- Independent selected checks:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_journal_alias_after_overlapping_read \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_empty_late_journal_alias_after_connect \
  tests/test_sqlite_repository.py::test_sqlite_repository_preserves_nonzero_user_version \
  tests/test_sqlite_repository.py::test_sqlite_repository_read_does_not_block_on_healthy_writer \
  tests/test_cli.py::test_cli_discover_busy_database_exits_with_retryable_contention_error \
  tests/test_sqlite_repository.py::test_sqlite_repository_accepts_normal_persisted_wal_and_shm_sidecars \
  tests/test_sqlite_repository.py::test_sqlite_repository_rejects_commit_replacement_without_visible_persistence
8 passed in 5.42s
```

- `git diff --check` and `git show --check --format=short HEAD` passed.
- Independent cross-process reproduction confirmed the remaining P1 below.

## Closed Findings

- The same-process nested `table_names()` regression now passes.
- The same-process nested read no longer unlocks the outer write's directory guard.
- Prior Loop 14 closures remain covered: single-operation empty journal protection, `user_version` preservation, read `mode=ro`, retryable busy/locked CLI text, raw-connect removal, write `mode=rw`, post-commit visible identity validation, strict hydration, guarded schema creation, repeated discovery, and WAL/SHM compatibility.

## Findings

### P1 - Cross-process repository read can still reopen the journal mutation interval

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:64`, also `:141`, `:159`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/managed_files.py:15`.
- Loop 15 introduced a class-level `threading.RLock()` plus `threading.local()` path depth. This serializes same-process operations, but it is not visible to another Python process or CLI invocation.
- Independent reproduction: during a parent `upsert_source()` after `after write transaction`, a child Python process ran `SQLiteRepository(paths.db_path).table_names()` successfully. The child entered as an outermost operation, ran `ensure_managed_directory()`, restored `.portfolio-maker` to writable, and returned `True`. The parent then replaced `portfolio.db-journal` with an empty external hard link. SQLite expanded the external inode before final validation rejected it:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
xproc_rc=0 xproc_stdout=True xproc_stderr=
```

- Minimal next fix: use a real inter-process repository lock, such as a standard-library advisory file/descriptor lock, held across `ensure_managed_directory()`, directory chmod, SQLite connection, transaction, validation, commit, and unlock. Preserve same-process reentrant depth handling to avoid nested-read deadlock, but make the repository critical section visible across processes. Add a subprocess regression asserting the external inode remains size `0`.

## Ponytail Cleanup

- Parfit found the current `threading.RLock` layer is not enough and should be replaced or augmented with the smallest stdlib file/descriptor lock. Do not add a custom SQLite VFS/binding, second repository layer, remote dependency, or broad abstraction.

## Reviewer Decisions

- Parfit / `@ponytail`: NEEDS WORK, one P1 local-only lock issue.
- Epicurus / logical flow: NEEDS WORK, one P1 cross-process sidecar window.
- Erdos / plan and contract: NEEDS WORK, process-local lock does not satisfy the Loop 15 repository-wide critical-section requirement.
- Cicero / reality checker: NEEDS WORK, one P1 cross-process reproduction.

## Next Minimal Checks

- Use `docs/reviews/2026-07-11-team-based-review-loop-16-preparation.md` as the next authorized-loop input.
- Reproduce the cross-process child-read scenario before implementation.
- Add a focused subprocess regression that keeps the external journal inode at size `0`.
- Preserve all prior closures and rerun full, focused, direct filesystem, WAL/SHM, lock, and CLI checks.

## Re-Review Outcome

NEEDS WORK. Loop 15 closes the same-process nested-read race, but does not close the repository-wide cross-process variant. Version `0.1.0` is not re-finalized and no remote push is authorized from this result.
