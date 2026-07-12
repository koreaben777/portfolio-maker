# Team Based Review Loop 17 - Re-Review 17 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `3e47bc6f9f736241fc50697cffd44fa62f30360e`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 16 report and `2e2e88d..3e47bc6`.
- Independent verification: full suite `281 passed`; focused GitHub/profile/approval/SQLite/discovery suite `212 passed`; `git diff --check` and `git show --check --format=short` passed.
- Fresh independent `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker lanes.

## Findings

### P1 - Control characters in title or author can reveal secret-shaped public text

`src/portfolio_maker/infrastructure/github_connector.py:461` accepts Unicode controls in activity titles. In `src/portfolio_maker/application/build_profile.py:148`, public masking runs before normalization; therefore `"Bearer\\x00example-token-value"` misses the bearer matcher, is normalized, and can be emitted as `"Bearer example-token-value"` in a `public_safe` claim. This breaches the Phase 1 malformed-metadata and public-secret exclusion boundary.

Minimal fix: reject Unicode controls in parser title and author fields, and reject persisted activity title/author values with controls before public evidence creation. Add parser and legacy-row regressions proving no claim or draft evidence is emitted.

### P2 - Non-workflow activities accept workflow-only state fields

`src/portfolio_maker/infrastructure/github_connector.py:541` validates a non-workflow activity's state without constraining `state_field`. Because the SQLite schema permits `status` or `conclusion` for every row, an approved persisted `pull_request` with `state_field="status"` remains public artifact-eligible through `src/portfolio_maker/application/build_profile.py:126`.

Minimal fix: require `state_field is None` for every non-workflow activity type in `is_valid_github_activity_state`, then add a malformed legacy-row regression proving the profile and draft exclude it.

### P3 - Persisted-state regression contains a no-op parser call

`tests/test_profile_and_portfolio.py:396` calls the parameterized parser but discards its result; the assertion relies only on direct SQLite insertion. Remove that parser parameter, its imports, and call. Keep the test focused on the legacy persisted-artifact boundary.

## Ponytail Cleanup

The P3 test cleanup is a net deletion. Do not generalize validation or introduce a new masking layer.

## Next Minimal Checks

1. Reproduce the secret-shaped title and author case before/after the guard, at parser and legacy persisted-row boundaries.
2. Verify non-workflow `state_field` injection is rejected at profile and draft output gates.
3. Run focused GitHub/profile/SQLite tests, the full suite, and diff whitespace checks.
