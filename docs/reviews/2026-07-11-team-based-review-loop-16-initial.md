# Team Based Review Loop 16 - Initial Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `857f50114b486f6a5b90804843df99be1ee1310e`

Status: NEEDS WORK

## Evidence Checked

- Loop 15 re-review result and Loop 16 preparation document.
- Same four reviewer lanes: Parfit (`@ponytail`), Epicurus (logical flow), Erdos (plan/spec contract), and Cicero (bug/reality checker).
- Full suite: `183 passed`.
- Selected Loop 15/14 tests: `8 passed`.
- `git diff --check` and `git show --check --format=short HEAD` passed.
- Independent cross-process reproduction confirmed the remaining accepted P1.

## Closed Findings

- Same-process nested repository read no longer unlocks the outer write guard.
- The same-process overlapping-read regression passes.
- Prior Loop 14 closures remain covered.

## Findings

### P1 - Process-local critical section does not protect against another repository process

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:64`, also `:141`, `:159`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/managed_files.py:15`.
- The current critical section uses `threading.RLock()` and `threading.local()`, so another Python process or CLI invocation does not observe it.
- A child process can run `SQLiteRepository(paths.db_path).table_names()`, become the outermost operation in its own process, call `ensure_managed_directory()`, restore `.portfolio-maker` to writable, and return while a parent write is still active.
- Direct reproduction:

```text
injected=True external_size=12824 result=RepositoryError: Unsafe managed database path: portfolio.db-journal. Preserve or back up the workspace state, remove the unsafe managed path, and rerun the command
xproc_rc=0 xproc_stdout=True xproc_stderr=
```

- Minimal next action: add a repository-owned inter-process file/descriptor lock around the full database-family critical section while preserving same-process reentrant depth handling. Add the subprocess regression described in `docs/reviews/2026-07-11-team-based-review-loop-16-preparation.md`.

## Ponytail Cleanup

- Replace or augment the local-only lock with the smallest standard-library inter-process lock.
- Do not introduce custom SQLite VFS/binding, new dependencies, second repository layer, or broad cleanup.

## Next Minimal Checks

- Reproduce the cross-process P1 before implementation.
- Add a focused subprocess regression asserting the external inode remains size `0`.
- Rerun same-process nested read, Loop 14 direct tests, full suite, `git diff --check`, and `git show --check --format=short HEAD`.
