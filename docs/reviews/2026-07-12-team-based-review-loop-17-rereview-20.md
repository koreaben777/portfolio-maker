# Team Based Review Loop 17 - Re-Review 20 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `983c9c413d803ba9bfc4334f5e63274d94dae597`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 19 report and `08aed72..983c9c4`.
- Independent verification: full suite `315 passed`; focused GitHub/policy/profile/approval/SQLite/discovery suite `264 passed`; `git diff --check` and `git show --check --format=short` passed.
- Fresh independent `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker lanes.
- Normal bearer metadata remains intentionally masked; U+034F/U+180F obfuscation for every current masking-pattern class is excluded; normal international text remains accepted. Phase 1, README, and historical handoff statements align.

## Findings

### P1 - Coexisting canonical and legacy URL rows retain stale public evidence

`src/portfolio_maker/infrastructure/sqlite_repository.py:541` skips legacy-equivalent lookup when an exact canonical row already exists. If an older mixed-case legacy row and canonical row coexist, rediscovery refreshes only the canonical row. `list_github_activities()` returns the older row first, and profile deduplication retains the stale title/evidence.

Minimal fix: transactionally collect every canonical-equivalent row regardless of exact-row presence, choose one survivor, apply current discovery values to it, repoint dependent `evidence_items` rows to the survivor, and delete duplicate activity rows. Add a dual-row legacy + canonical -> rediscovery -> repeated-build regression proving one row, current title/source, and one evidence/claim path.

### P2 - Legacy reconciliation scans all activities for each insert

`src/portfolio_maker/infrastructure/sqlite_repository.py:550` fetches every row of the repository/activity type and canonicalizes each in Python. Inserting 100 new activities causes 5,050 URL canonicalization calls.

Minimal fix: use a case-insensitive exact URL lookup for the canonical URL to obtain candidate aliases, then use that set for the P1 merge. Avoid full per-insert scans.

### P3 - Unused hidden-secret helper

`src/portfolio_maker/infrastructure/policy.py:159` defines `is_secret_shaped_public_value()` but production code calls only `contains_hidden_secret_shaped_public_value()`. Remove the unused helper and its test-only assertion; retain the production predicate and normal-bearer masking coverage.

## Ponytail Cleanup

The P3 removal and SQL candidate lookup should reduce code and repeated work. Do not add a separate migration framework or a generalized Unicode layer.

## Next Minimal Checks

1. Verify dual pre-existing alias/canonical rows consolidate to one referenced activity after rediscovery.
2. Verify profile and draft use current title/source across repeated builds.
3. Verify reconciliation no longer performs a growing Python scan for each insert.
4. Run focused/full suites and diff whitespace checks.
