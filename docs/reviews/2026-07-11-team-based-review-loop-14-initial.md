# Team Based Review Loop 14 - Initial Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `1b25a74da43ca2381013e6ea8c79144d7b9cf2cf`

Status: NEEDS WORK

## Evidence Checked

- Loop 13 re-review and Loop 14 preparation documents.
- Same four reviewer lanes: Parfit (`@ponytail`), Epicurus (logical flow), Erdos (plan/spec contract), and Cicero (bug/reality checker).
- Full suite: `178 passed`.
- `git diff --check` and `git show --check --format=short HEAD` passed.
- Direct reviewer reproductions covered an empty late-journal hard link, nonzero `user_version`, and healthy writer contention.
- `origin/main` remains two README-only commits ahead on its side; integration is deferred until the release gate passes.

## Closed Findings

- Raw write-capable `SQLiteRepository.connect()` is removed.
- SQLite URI `mode=rw` prevents connect-time creation of a replaced missing target.
- Main replacement during commit no longer reports detached persistence as success.
- Required and optional hydration types map invalid SQLite values to controlled `RepositoryError`.
- Schema creation remains inside the guarded transaction lifecycle.

## Findings

### P1 - Empty journal alias can be modified between connection validation and transaction validation

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:80`.
- An empty hard-linked `portfolio.db-journal` inserted immediately after `after connect` validation was expanded to `4616` bytes by `BEGIN IMMEDIATE` before the next family validation rejected it.
- The existing late-journal regression injects at an obsolete stage and uses a non-empty file, so it does not prove this interval is closed.
- Minimal next action: correct the red regression first, then select the smallest evidence-backed journal policy that prevents SQLite from opening an unvalidated external sidecar. Do not add another repository layer.

### P2 - Journal-forcing pragma destroys nonzero SQLite migration metadata

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:83`.
- `PRAGMA user_version = user_version` changed an existing `user_version` from `37` to `0` during a repository read.
- Minimal next action: preserve the exact numeric metadata value or replace the guard write with one that has no semantic side effect; test representative reads and writes.

### P2 - Healthy lock contention is classified as database damage

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:81`, `:92`, and `:242`.
- Every operation acquires `BEGIN IMMEDIATE`. A healthy concurrent writer therefore blocks reads and writes, and the controlled CLI error incorrectly recommends database repair or replacement.
- Minimal next action: separate guarded read behavior from guarded writes and map `SQLITE_BUSY`/`SQLITE_LOCKED` to a concise retryable-contention error.

## Ponytail Boundary

- Keep the change inside `SQLiteRepository`, its existing managed-file helpers, and focused tests unless direct evidence proves a lower-level SQLite mechanism is necessary.
- Do not add a second repository abstraction, custom C binding, remote dependency, or unrelated cleanup.
- A journal-mode change must preserve crash semantics or explicitly demonstrate why the selected SQLite policy remains safe for the MVP.

## Required Verification

- Red-to-green tests for all three findings.
- Existing raw-connect, missing-target, late-sidecar, commit-detach, hydration, persisted WAL/SHM, and repeated-discovery regressions.
- Full `pytest` suite and direct filesystem/lock/CLI reproductions.
- `git diff --check`, `git show --check --format=short HEAD`, findings gate, and same-team re-review.
