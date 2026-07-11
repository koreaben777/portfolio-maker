# Team Based Review Loop 12 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `6872040671de02380dfb5f153dea8d20b3f0f520`

Status: NEEDS WORK

## Evidence Checked

- The same Loop 11 reviewer team and four lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: `agency-router` / `codebase-onboarding` logical-flow review.
  - Erdos: `agency-router` / `technical-writer` contract review.
  - Cicero: `agency-router` / `reality-checker` adversarial validation.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `142 passed`.
- `git diff --check` and `git show --check --format=short HEAD` passed.
- Direct temporary-workspace reproductions confirmed:
  - `portfolio.db` symlink and hard-link aliases changed external databases and exited `0`;
  - a hard-linked `portfolio.db-journal` changed the external inode and exited `0`;
  - semantically invalid `SourceStatus` data exited with a traceback;
  - an existing `0755` workspace and SQLite-created `0644` database remained broadly readable.
- Reviewer reproductions additionally covered pre-connect final-component replacement, detached commits after path replacement, rollback journal, persisted WAL/SHM sidecars, FIFO/directory targets, and repeated discovery.

## Closed Findings

- All accepted Loop 11 initial findings remain closed at `6872040`.
- Symlinked SQLite sidecars, FIFO/database-directory targets, malformed discovery symlinks, managed report/artifact writes, and corrupt raw SQLite bytes fail through controlled paths.
- Documented GitHub discovery-only scope, review-required skeleton output, retained local history, and historical Ponytail cleanup remain non-release limitations.

## Findings

### P1 - Main SQLite database aliases and replacement races escape the managed workspace

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:58`.
- `sqlite3.connect(self.db_path)` follows a final-component symlink and accepts a same-filesystem hard link. Replacing a validated regular path immediately before connect also redirects schema and source writes externally.
- A transaction can commit to a detached original inode after `portfolio.db` is replaced, so the command reports success while the visible workspace database has no committed row.
- Minimal fix: reuse the pinned parent `dir_fd` machinery, reject symlink/non-regular or `st_nlink != 1` entries, create missing databases with `O_CREAT | O_EXCL | O_NOFOLLOW` mode `0600`, record `(st_dev, st_ino)`, verify the opened identity before the first write and again before commit, and roll back with `RepositoryError` on replacement.

### P1 - SQLite journal/WAL/SHM hard links can modify external inodes

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:62` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:75`.
- Hard-linked `portfolio.db-journal`, `portfolio.db-wal`, and `portfolio.db-shm` entries are written or resized by SQLite before the managed name is removed. Sidecar symlinks already fail closed, but hard links bypass that protection.
- Minimal fix: under the same pinned parent descriptor, validate the complete database family (`portfolio.db`, `-journal`, `-wal`, `-shm`) before every write-capable connection and reject pre-existing symlink, non-regular, or multiply linked entries. Add rollback-journal and persisted-WAL/SHM external-sentinel regressions.

### P1 - Existing workspace and database permissions are not tightened

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/managed_files.py:95` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:62`.
- Existing `.portfolio-maker` directories can remain `0755`, while SQLite creates `portfolio.db` as `0644` under a common umask. The database contains private local source URIs and GitHub metadata.
- Minimal fix: `fchmod` validated managed directories to `0700`, create or tighten the main database to `0600`, and add an upgrade regression starting from `0755/0644` state.

### P2 - Semantically corrupt rows bypass controlled CLI failures

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:255` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/adapters/cli.py:62`.
- Hydrating a valid SQLite row with `status='invalid-status'` raises uncaught `ValueError` and emits a traceback.
- Minimal fix: map model-hydration value/type failures to `RepositoryError`, add `CHECK` constraints for new databases, and preserve controlled handling for existing damaged databases.

### P2 - SQLite recovery guidance can recommend replacing a healthy database

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:82`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/adapters/cli.py:62`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/README.md:99`.
- Unsafe sidecar paths and non-database target types receive the same `Repair or replace the damaged database` advice as actual SQLite corruption.
- Minimal fix: distinguish unsafe managed-path/sidecar errors from corruption, identify the offending managed path without exposing sensitive data, advise preserving/backing up state before replacement, and add a concise README recovery section.

### P2 - Loop 11 documentation understates the active database-family boundary

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-team-based-review-loop-11-rereview.md:53` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-portfolio-maker-0.1.0-final-report.md:18`.
- The current wording names only a final-component symlink and would not require hard-link, replacement-race, journal, WAL, or SHM regressions.
- Minimal fix: rename the scope to the SQLite database-family managed-path boundary and record the expanded acceptance checks.

### P3 - Publication manifest omits completed Loop 11 reports

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-portfolio-maker-0.1.0-final-report.md:146`.
- The report currently says publication includes reports through Loop 10 although Loop 11 reports exist and Loop 12 is active.
- Minimal fix: if Loop 12 reaches PASS, list all available reports through Loop 12 in the final 0.1.0 publication state.

## Ponytail Cleanup

- Do not add a second repository layer, custom SQLite C binding, or VFS wrapper.
- Reuse `managed_files.py` directory-descriptor primitives and add one SQLite-specific database-family validation boundary in `SQLiteRepository`.
- Keep previously adjudicated historical-plan and dead-surface cleanup outside this correctness fixback.

## Next Minimal Checks

- Add focused failing checks before implementation for main DB symlink/hard link, pre-connect replacement, replacement-before-commit, journal hard link, persisted WAL/SHM hard links, permission upgrade, semantically invalid rows, and recovery wording.
- Verify normal new-database creation and repeated discovery remain functional and idempotent.
- Run the focused persistence/CLI suite, full suite, `git diff --check`, `git show --check --format=short HEAD`, and Fable findings gate.
