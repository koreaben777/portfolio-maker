# Team Based Review Loop 16 - Re-Review Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `a09ecee58cc0cfeb15e6e0200be3268dc5cad515`

Status: PASS

## Evidence Checked

- Implementation fixback marker: `[TEAM_REVIEW_FIX_DONE_16]`.
- Implementation commit: `a09ecee58cc0cfeb15e6e0200be3268dc5cad515` (`fix: lock SQLite repository across processes`).
- Changed implementation files:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/tests/test_sqlite_repository.py`
- Full suite:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
184 passed in 7.22s
```

- Selected Loop 16, Loop 15, and Loop 14 regression bundle:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_journal_alias_after_child_process_read \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_journal_alias_after_overlapping_read \
  tests/test_sqlite_repository.py::test_sqlite_repository_prevents_empty_late_journal_alias_after_connect \
  tests/test_sqlite_repository.py::test_sqlite_repository_preserves_nonzero_user_version \
  tests/test_sqlite_repository.py::test_sqlite_repository_read_does_not_block_on_healthy_writer \
  tests/test_cli.py::test_cli_discover_busy_database_exits_with_retryable_contention_error \
  tests/test_sqlite_repository.py::test_sqlite_repository_accepts_normal_persisted_wal_and_shm_sidecars \
  tests/test_sqlite_repository.py::test_sqlite_repository_rejects_commit_replacement_without_visible_persistence
9 passed in 6.50s
```

- Independent parent/child cross-process smoke:

```text
started=started
child_completed_before_injection=False
injection_blocked=True injected=False
external_size=0
parent_error=None
child_rc=0 child_stdout=True child_stderr=''
managed_mode=0o700
```

- Hygiene:

```text
git diff --check
passed

git show --check --format=short HEAD
passed
```

- Same four reviewer lanes were retained:
  - Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail`.
  - Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): logical flow.
  - Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): plan/spec and contract.
  - Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): bug and reality checker.

## Closed Findings

### P1 - Cross-process repository read could reopen the sidecar mutation window

- Previous failing HEAD: `857f50114b486f6a5b90804843df99be1ee1310e`.
- Previous evidence:

```text
child_rc=0
child_stdout=True
injected=True
external_size=12824
RepositoryError
```

- Closure:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:123` now enters `_repository_critical_section()` before database-family preparation.
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:156` acquires a stdlib `fcntl.flock(LOCK_EX)` on the workspace directory descriptor for the outermost operation.
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:142` through `:171` preserve same-process reentrant depth while serializing cooperating processes.
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:125` through `:140` hold the critical section through managed-directory setup, sidecar preparation, directory chmod, SQLite connection/read/write, validation, close, chmod restore, and descriptor close.
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/tests/test_sqlite_repository.py:544` adds the subprocess regression and asserts the external inode remains size `0`.

## Findings

No P1 or P2 findings remain.

The only reviewer note was a non-blocking P3 documentation observation: public storage-layout docs list `portfolio.db` without enumerating the managed SQLite sidecars. README recovery wording and this report cover the sidecar policy, and the note does not contradict the `0.1.0` artifact contract.

## Ponytail Cleanup

PASS. The fix stays inside `SQLiteRepository`, uses the standard library, creates no lock-file artifact, preserves same-process reentrancy, and avoids a custom SQLite VFS/binding, dependency, or second repository layer.

## Final Reviewer Decisions

- Parfit / `@ponytail`: PASS. No low-risk cleanup requested.
- Epicurus / logical flow: PASS. Repository-flow P1 is closed for cooperating Portfolio Maker processes.
- Erdos / plan and contract: PASS. The Loop 16 inter-process critical-section requirement is satisfied.
- Cicero / reality checker: PASS. The direct parent/child external-inode reproduction now stays at `0` bytes and no new actionable issue was found.

## Residual Risk

`fcntl.flock()` is an advisory lock. It coordinates Portfolio Maker repository and CLI processes that use this code path; it does not claim to stop arbitrary same-user filesystem mutation that intentionally ignores the repository lock.

## Outcome

Loop 16 outcome is PASS. The reviewer team has no remaining improvement request for Portfolio Maker `0.1.0`.
