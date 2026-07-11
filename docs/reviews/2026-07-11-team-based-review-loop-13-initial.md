# Team Based Review Loop 13 - Initial Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `c1baaa46a4e29d56d6c9eff1e8ac9b53abcacfaa`

Status: NEEDS WORK

## Evidence Checked

- Loop 12 re-review and Loop 13 preparation documents.
- Same four reviewer lanes: Parfit (`@ponytail`), Epicurus (logical flow), Erdos (technical writer), and Cicero (reality checker).
- Focused SQLite/CLI suite: `49 passed`.
- Full suite: `167 passed`.
- Direct raw-API reproduction: `SQLiteRepository.connect()` plus a late hard-linked journal changed the external inode to `16928` bytes.
- `git diff --check` and `git show --check --format=short HEAD` passed.
- `origin/main` has two README-only commits not yet integrated; they do not change the Loop 13 runtime scope.

## Closed Findings

- Static main/sidecar aliases and non-regular targets fail closed through guarded repository methods.
- Existing workspace/database modes are tightened to `0700/0600`.
- New enum constraints, legacy invalid-enum handling, and differentiated recovery guidance remain covered.
- Normal repeated discovery remains functional and idempotent.

## Findings

### P1 - Raw `SQLiteRepository.connect()` bypasses lifetime validation

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:75`.
- The method returns a write-capable connection after closing the pinned directory descriptor. Late journal insertion can modify an external inode and commit successfully.
- Minimal fix: remove the raw method or make it non-public/read-only; keep all production writes behind the guarded `_connection()` lifecycle. No production source caller requires the raw API.

### P1 - Main and sidecar replacement windows remain during connection/DML

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:89`, `:135`, and `:192`.
- A replacement to a missing external target during `sqlite3.connect()` can create an external zero-byte DB before validation rejects it.
- A late journal hard link inserted after validation but before DML can be modified before rollback detects the family mismatch.
- Minimal fix: use SQLite URI `mode=rw` after safe creation, and establish/validate the write transaction and resulting family before yielding a write-capable connection.

### P1 - Replacement inside commit can detach successful persistence

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:91`.
- Pre-commit validation does not cover replacement during `conn.commit()`. The command can report success while the visible DB retains old state and the detached inode receives the update.
- Minimal fix: keep database-family identity serialized through commit and verify the visible identity after commit; do not report success for detached persistence.

### P2 - Non-enum semantic row types bypass hydration validation

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:435` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:62`.
- Required or optional text fields stored as SQLite BLOBs can escape repository hydration and later produce a traceback.
- Minimal fix: validate all hydrated required/optional field types before constructing domain models and map failures through `_semantic_error()`/`RepositoryError`.

## Ponytail Cleanup

- Removing or restricting raw `connect()` is both the smallest P1 fix and a net simplification.
- Reuse `_connection()`, managed-file primitives, and SQLite URI support before considering any larger abstraction.
- Do not add a custom SQLite binding, VFS, second repository layer, or unrelated cleanup without new evidence.

## Next Minimal Checks

- Add focused red regressions for raw-connect late sidecar insertion, connect-time missing-target replacement, late sidecar insertion before DML, replacement inside commit, and BLOB hydration.
- Verify normal repository operations, persisted WAL/SHM, repeated discovery, and controlled CLI output remain intact.
- Run focused/full suites, direct visible/detached state checks, `git diff --check`, `git show --check --format=short HEAD`, and the Fable findings gate.
