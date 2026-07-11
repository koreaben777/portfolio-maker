# Team Based Review Loop 14 - Re-Review Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `2a1b397e2c87f6d3c00edcf1808c5e5e4fb3ac8b`

Baseline: `1b25a74da43ca2381013e6ea8c79144d7b9cf2cf`

Status: NEEDS WORK

## Evidence Checked

- Same four reviewer lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: logical-flow and SQLite lifecycle review.
  - Erdos: plan/spec and contract review.
  - Cicero: bug and reality-check validation.
- Implementation commit: `2a1b397e2c87f6d3c00edcf1808c5e5e4fb3ac8b` (`fix: guard SQLite sidecar lifecycle`).
- Implementation thread completion marker: `[TEAM_REVIEW_FIX_DONE_14]`.
- Independent full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
182 passed in 6.07s
```

- Independent Loop 14 selected checks:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_empty_late_journal_alias_after_connect \
  tests/test_sqlite_repository.py::test_sqlite_repository_preserves_nonzero_user_version \
  tests/test_sqlite_repository.py::test_sqlite_repository_read_does_not_block_on_healthy_writer \
  tests/test_cli.py::test_cli_discover_busy_database_exits_with_retryable_contention_error \
  tests/test_sqlite_repository.py::test_sqlite_repository_accepts_normal_persisted_wal_and_shm_sidecars \
  tests/test_sqlite_repository.py::test_sqlite_repository_rejects_commit_replacement_without_visible_persistence
7 passed in 5.29s
```

- `git diff --check` and `git show --check --format=short HEAD` passed.
- Direct smoke confirmed `user_version=37` preservation, healthy-writer read success, WAL/SHM acceptance, sidecar existence, and directory mode restoration to `0700`.
- Independent race reproduction confirmed the new P1 below.

## Closed Findings

- The corrected empty-journal regression now injects after `after connect` and confirms the external inode remains unchanged when only a single repository operation is active.
- `PRAGMA user_version = user_version` was removed; nonzero `user_version` is preserved across representative reads and writes.
- Read methods now use a `mode=ro` read path and do not issue `BEGIN IMMEDIATE`.
- `SQLITE_BUSY` / `SQLITE_LOCKED` now maps to a retryable contention message instead of database-damage guidance.
- Raw `connect()` remains removed, write connect remains `mode=rw`, post-commit visible identity validation remains present, strict hydration remains controlled, guarded schema creation remains covered, repeated discovery still passes, and WAL/SHM compatibility remains covered.

## Findings

### P1 - Overlapping repository read can unlock the directory while a write transaction is active

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:141`, also `:88` and `:210`.
- Epicurus found that the sidecar protection relies on changing the managed database directory mode with `fchmod`. A normal overlapping repository read calls `_open_database_family()`, which runs `ensure_managed_directory()` and restores `.portfolio-maker` to writable while an outer write transaction is already past the `after write transaction` validation and about to execute DML after the yield.
- Independent reproduction matched the reviewer result. During `upsert_source()`, after `after write transaction`, the reproduction called `SQLiteRepository(paths.db_path).table_names()`, then replaced `portfolio.db-journal` with an empty hard link to an external file. The outer write eventually raised `RepositoryError`, but SQLite had already expanded the external inode:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
```

- Minimal next fix: add a real repository-wide critical-section mechanism around database-family permission changes, with same-process reentrant depth handling so overlapping repository reads/writes cannot restore directory write permission until the outer write completes. Add a regression that injects the empty hard-linked journal after `after write transaction`, performs an overlapping `table_names()` call, and asserts the external inode stays size `0`.

## Ponytail Cleanup

- No blocking over-implementation finding from Parfit. Loop 14 stayed inside `SQLiteRepository` and focused tests, with no `MEMORY`/`OFF`, custom VFS/binding, new dependency, second repository layer, or unrelated cleanup.
- Erdos recorded two non-blocking P3 documentation observations:
  - The storage-layout spec lists `portfolio.db` but not the pre-created SQLite sidecars. A future doc cleanup can mention `portfolio.db-journal`, `portfolio.db-wal`, and `portfolio.db-shm`.
  - The final report should record `PERSIST` as the accepted rollback-journal policy for Loop 14.
- Cicero passed the release checks but noted the non-blocking residual risk that owner-only directory chmod is not a full privilege boundary.

## Reviewer Decisions

- Parfit / `@ponytail`: PASS.
- Epicurus / logical flow: NEEDS WORK, one P1 reentrant directory-unlock race.
- Erdos / plan and contract: PASS with non-blocking P3 documentation observations.
- Cicero / reality checker: PASS with non-blocking residual risk.

## Next Minimal Checks

- Use `docs/reviews/2026-07-11-team-based-review-loop-15-preparation.md` as the next authorized-loop input.
- Reproduce the overlapping-read directory-unlock race before implementation.
- Preserve all Loop 14 closed findings and rerun full, focused, direct filesystem, WAL/SHM, lock, and CLI checks.

## Re-Review Outcome

NEEDS WORK. Loop 14 closes the three accepted Loop 13 regressions, but same-team re-review found a new P1 that allows an overlapping repository read to reopen the sidecar mutation interval. Version `0.1.0` is not re-finalized and no remote push is authorized from this result.
