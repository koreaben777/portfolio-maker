# Team Based Review Loop 11 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `6872040671de02380dfb5f153dea8d20b3f0f520`

Baseline: `473f5c045b24f7703c48fa6438ec9dba63ff058f`

Status: NEEDS WORK

## Evidence Checked

- The same new-model reviewer team and four lanes were retained:
  - Parfit: `@ponytail` over-implementation review.
  - Epicurus: `agency-router` / `codebase-onboarding` logical-flow review.
  - Erdos: `agency-router` / `technical-writer` contract review.
  - Cicero: `agency-router` / `reality-checker` adversarial validation.
- Implementation commit: `6872040671de02380dfb5f153dea8d20b3f0f520` (`fix: close team review loop 11 findings`).
- Focused verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_approval.py tests/test_artifacts.py tests/test_cli.py tests/test_github_connector.py tests/test_local_discovery.py tests/test_profile_and_portfolio.py tests/test_sqlite_repository.py
96 passed
```

- Full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
142 passed
```

- `git diff --check` and `git show --check --format=short HEAD` passed.
- Direct temporary-workspace reproductions confirmed that managed output symlinks preserve external sentinels, damaged approval/DB state exits without tracebacks, and self-loop symlinks no longer abort discovery.

## Closed Findings

- P1 discovery reports, approval files, profiles, and portfolio artifacts now use descriptor-relative managed writes and reject symlink/non-regular targets.
- P2 invalid approval encoding/JSON and corrupt SQLite contents now return controlled CLI errors without deleting the damaged state.
- P2 malformed local symlinks are skipped while valid candidates continue.
- P2 local and GitHub labels normalize control characters and escape Markdown structure.
- P2 successful profile rebuilds invalidate stale portfolio drafts.
- P2 malformed commit payloads without a stable URL are rejected before persistence.
- P3 canonical URI aliases are deduplicated before the discovery cap.
- P3 final-report publication and review-history wording now matches the published repository state.

## Findings

### P1 - SQLite database final-component symlink remains writable

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:62`.
- `ensure_managed_directory()` protects database parent components, but `sqlite3.connect(self.db_path)` still follows a symlink at `.portfolio-maker/portfolio.db`.
- Epicurus and Cicero independently reproduced writes into an external SQLite target. The main reviewer also reproduced exit `0` with the external target growing from `0` to `28672` bytes.
- Smallest next fix: pin the managed parent descriptor, reject symlink/non-regular database entries, safely create a missing database with `O_CREAT | O_EXCL | O_NOFOLLOW`, verify the final entry before schema writes, and add an external-database sentinel regression.

This finding was superseded and expanded by Loop 12 into the full SQLite database-family boundary: main-file hard links, replacement timing, rollback journal, WAL, SHM, permissions, and connection lifetime.

## Ponytail Cleanup

- Parfit returned PASS with no further improvement request for the Loop 11 fix diff.
- The shared managed-file and presentation helpers serve several accepted findings and were not judged speculative or disproportionate.
- Previously adjudicated historical-plan and dead-surface cleanup remains outside this one-cycle fixback.

## Next Minimal Checks

- Continue through the separately documented Loop 12/13 database-family scope rather than applying a symlink-only fix.
- Preserve this Loop 11 result as historical evidence; use the latest re-review for the active release gate.
- Do not remote-push until the latest loop returns PASS.

## Re-Review Outcome

NEEDS WORK. All accepted initial Loop 11 findings are closed, but the same managed-path privacy and integrity boundary has one newly confirmed P1 manifestation at the SQLite database final component. Per the one-cycle guardrail, no second fixback was started.
