# Team Based Review Loop 12 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `c1baaa46a4e29d56d6c9eff1e8ac9b53abcacfaa`

Baseline: `6872040671de02380dfb5f153dea8d20b3f0f520`

Status: NEEDS WORK

## Evidence Checked

- The same four reviewer lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: `agency-router` / `codebase-onboarding` logical-flow review.
  - Erdos: `agency-router` / `technical-writer` contract review.
  - Cicero: `agency-router` / `reality-checker` validation review.
- Implementation commit: `c1baaa46a4e29d56d6c9eff1e8ac9b53abcacfaa` (`fix: secure SQLite database family`).
- Focused verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_sqlite_repository.py tests/test_cli.py
49 passed
```

- Full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
167 passed
```

- `git diff --check` and `git show --check --format=short HEAD` passed.
- Direct temporary-workspace checks confirmed that static main/journal hard links preserve external files, new enum corruption is rejected by schema constraints, and upgraded permissions become `0700/0600`.
- A direct raw-API reproduction confirmed that `SQLiteRepository.connect()` can still modify a hard-linked external journal after the method returns.

## Closed Findings

- P1 static main DB symlink, hard-link, directory, FIFO, and pre-connect replacement targets are rejected through the guarded repository path.
- P1 pre-existing journal/WAL/SHM symlink, hard-link, and non-regular entries are rejected; valid persisted WAL/SHM state remains supported.
- P1 managed workspace/database permissions are tightened to `0700/0600`.
- P2 new databases constrain enum values and existing invalid enum rows use controlled `RepositoryError` handling.
- P2 README and CLI guidance distinguish unsafe managed paths from actual SQLite corruption.
- Normal repository operations and repeated discovery remain functional and idempotent.

## Findings

### P1 - Public raw `connect()` bypasses database-family lifetime validation

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:75`.
- `connect()` closes the pinned directory descriptor before returning a raw write-capable connection. A hard-linked `portfolio.db-journal` added after return was modified externally and committed without `RepositoryError`.
- Main-review reproduction: `raw_connect_external_size=16928`.
- Smallest next fix: remove or make `connect()` non-public/read-only; keep every write behind `_connection()` and repository methods. No production source caller currently requires the raw API.

### P1 - Connection and sidecar replacement windows remain after validation

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:89`, `:135`, and `:192`.
- Replacing the main path with a symlink to a nonexistent external target inside `sqlite3.connect()` produced a controlled error but still created an external zero-byte DB.
- Adding a hard-linked journal after validation but immediately before DML caused rollback and `RepositoryError`, but the external journal inode had already been modified.
- Smallest next fix: use SQLite URI `mode=rw` after safe exclusive creation to prevent replacement-target creation, and redesign transaction startup so write-capable sidecars cannot be introduced after the last family validation and before DML.

### P1 - Main database replacement inside commit can detach successful persistence

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:91`.
- The current pre-commit identity check occurs before `conn.commit()`. A filesystem-backed replacement inside commit returned success while the visible DB retained old state and the detached DB received the update.
- Smallest next fix: serialize the database-family identity through commit and verify the visible identity after commit; the next design must avoid reporting success for detached persistence.

### P2 - Non-enum semantic row corruption is not fully validated

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:435` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:62`.
- A BLOB in a required text field such as `display_name` can still escape hydration validation and produce a traceback in a later use case.
- Smallest next fix: validate required and optional hydrated field types before constructing domain models, route failures through `_semantic_error()`, and add BLOB regressions.

## Ponytail Cleanup

- The directory descriptor, database identity, and family validators directly serve accepted findings and are not speculative cleanup targets.
- Removing or restricting raw `connect()` is both the smallest P1 fix and a simplification.
- Do not add a custom SQLite binding, VFS, second repository layer, or unrelated historical cleanup without new evidence.

## Next Minimal Checks

- Use `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-team-based-review-loop-13-preparation.md` as the next authorized loop input.
- Add red tests for raw-connect sidecar insertion, replacement-to-missing-target during connect, sidecar insertion immediately before DML, replacement inside commit, and BLOB hydration.
- Keep GitHub publication blocked until all four lanes return PASS.

## Re-Review Outcome

NEEDS WORK. Static database-family aliases, modes, enum constraints, and recovery guidance are substantially closed, but three P1 connection-lifetime windows and one P2 semantic-hydration gap remain. No second fixback was started inside Loop 12.
