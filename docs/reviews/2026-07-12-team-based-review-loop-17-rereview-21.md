# Team Based Review Loop 17 - Re-Review 21 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `3890164c9c7ef0b99ced643a98c9365e022413f9`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 20 report and `983c9c4..3890164`.
- Independent verification: full suite `316 passed`; focused GitHub/policy/profile/approval/SQLite/discovery suite `265 passed`; `git diff --check` and `git show --check --format=short` passed.
- Fresh independent `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker lanes.
- Over-implementation, documentation-contract, normal bearer masking, invisible-mark exclusion, and ordinary international text lanes passed.

## Finding

### P2 - Case-variant legacy repository keys bypass activity reconciliation

`src/portfolio_maker/infrastructure/sqlite_repository.py:544` matches `repo = ?` exactly while URL matching is case-insensitive. Legacy rows with `repo` values such as `Octo/Demo` or `OCTO/DEMO` are not collected as canonical-equivalent candidates. A new canonical rediscovery then leaves multiple activities and stale profile/draft evidence.

Minimal fix: make the reconciliation candidate repository comparison case-insensitive as well, then add a regression with case-variant repository and URL rows, dependent evidence/claims, current rediscovery, and repeated profile/draft builds. Assert one survivor, all evidence references updated, and current title/source rendered.

## Next Minimal Checks

1. Verify legacy repo and URL case variants merge transactionally with a canonical rediscovery.
2. Verify referenced evidence/claims remain valid and point to the survivor.
3. Run focused/full suites and diff whitespace checks.
