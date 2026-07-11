# Team Based Review Loop 13 - Re-Review Findings

Date: 2026-07-11

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `1b25a74da43ca2381013e6ea8c79144d7b9cf2cf`

Baseline: `c1baaa46a4e29d56d6c9eff1e8ac9b53abcacfaa`

Status: NEEDS WORK

## Evidence Checked

- The same four reviewer lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: logical-flow and SQLite lifecycle review.
  - Erdos: plan/spec and contract review.
  - Cicero: bug and reality-check validation.
- Implementation commit: `1b25a74da43ca2381013e6ea8c79144d7b9cf2cf` (`fix: harden SQLite connection lifecycle`).
- Independent full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
178 passed
```

- `git diff --check` and `git show --check --format=short HEAD` passed.
- Focused reviewer runs passed between 10 and 121 selected tests depending on lane.
- Direct reproductions confirmed the accepted raw-connect, missing-target, commit-detach, and BLOB-hydration findings are closed.

## Closed Findings

- The raw public `SQLiteRepository.connect()` write bypass is removed.
- Connect-time replacement with a missing external target no longer creates that target because the repository uses SQLite URI `mode=rw`.
- Replacement during commit produces a controlled `RepositoryError` instead of reporting detached persistence as success.
- Required and optional hydrated SQLite values are type checked and invalid BLOB/text rows map to controlled repository errors.
- `initialize()` no longer uses `executescript()` in a way that can implicitly commit outside the guarded lifecycle.
- Normal persisted WAL/SHM behavior, repeated discovery, and controlled CLI failures remain covered by the passing suite.

## Findings

### P1 - Empty late journal hard link can still be modified before family validation

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:80`.
- Parfit inserted an empty hard-linked `portfolio.db-journal` immediately after the `after connect` validation. `BEGIN IMMEDIATE` expanded the external inode to `4616` bytes before the subsequent validation raised `RepositoryError`.
- The committed regression injects at the obsolete `before first write` stage and uses a non-empty marker, so it does not exercise this interval.
- Smallest next step: first correct the regression to inject an empty hard link at `after connect`. Closing the interval requires an explicitly approved sidecar-free journal policy within `SQLiteRepository`, or a separately scoped SQLite VFS/open-flags design. Do not add another repository abstraction.

### P2 - Journal-forcing pragma resets SQLite `user_version`

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:83`.
- Epicurus set `PRAGMA user_version=37`, called read-only `table_names()`, and observed `37 -> 0` because `PRAGMA user_version = user_version` assigns the unresolved token as zero.
- Smallest next fix: read the current numeric value and write that exact value, or use a dedicated guard write that does not alter SQLite metadata. Add read/write regressions preserving a nonzero `user_version`.

### P2 - Healthy SQLite lock contention is reported as database damage

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:81`, `:92`, and `:242`.
- Cicero held a healthy database with another writer. Read operations and `discover` failed through `BEGIN IMMEDIATE`; the CLI returned exit 1 without traceback but advised repairing or replacing a damaged database.
- Smallest next fix: provide a guarded read lifecycle that does not acquire an immediate write transaction, and map `SQLITE_BUSY`/`SQLITE_LOCKED` to a concise retryable-contention error.

## Reviewer Decisions

- Parfit / `@ponytail`: NEEDS WORK, one P1 late-journal interval.
- Epicurus / logical flow: NEEDS WORK, one P2 `user_version` regression.
- Erdos / plan and contract: PASS, no additional improvement request.
- Cicero / reality checker: NEEDS WORK, one P2 lock-contention classification defect.

## Next Minimal Checks

- Use `docs/reviews/2026-07-11-team-based-review-loop-14-preparation.md` as the next authorized-loop input.
- Reproduce the empty late-journal mutation, `user_version` reset, and healthy writer contention before implementation.
- Preserve all Loop 13 closed findings and rerun full, focused, direct filesystem, and CLI checks.

## Re-Review Outcome

NEEDS WORK. Loop 13 closes the four accepted findings from Loop 12, but the same-team re-review found one release-blocking P1 and two P2 regressions. Version `0.1.0` is not re-finalized and no remote push is authorized from this result.
