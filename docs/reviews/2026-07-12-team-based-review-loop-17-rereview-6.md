# Team Based Review Loop 17 - Re-Review 6 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `4c8b814ce78c30070bcf51474cdb0109462a9f3f`
Status: NEEDS WORK

## Evidence Checked

- Prior Loop 17 reports, Phase 1 contracts, and `2896be9..4c8b814`.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `232 passed in 7.51s`.
- Focused GitHub/discovery/profile/draft suite -> `113 passed in 0.87s`.
- `git diff --check 2896be9..4c8b814` and `git show --check --format=short 4c8b814` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Two independent lanes reproduced both P2 paths.

## Closed Findings

- Commit timestamp now uses the same required RFC3339/ISO-8601 validator as the other activity types; malformed commit data is not endpoint-complete and prior valid commit evidence remains retryable.
- The Phase 1 policy specification is current and authoritative; historical architecture documents explicitly label their discovery-only wording as pre-Phase-1 baseline.
- Invalid timestamp parser coverage is parameterized without reducing activity-type coverage.

## Still Open / Newly Found Findings

### P2 - Malformed or cross-repository activity URLs are accepted as endpoint success

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:85` and the other candidate parsers accept raw activity URLs without validating their public GitHub shape, expected activity type, or endpoint repository. A PR `url="not-a-url"` succeeds with no status and records the pull-request endpoint complete; the subsequent invalidation hides earlier approved valid evidence, while profile merely skips the replacement. A valid-shaped URL for a different repository can also be stored and rendered because `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:120` checks the type but not its repository association.

Minimal fix: validate every candidate URL during parsing against the expected activity type and canonical endpoint repository before the endpoint is marked complete. Revalidate the URL-to-row repository association in `build_profile()` for legacy persistence. Add malformed and cross-repository candidate regressions that prove endpoint failure preserves the old profile/draft evidence.

### P2 - Bounded/non-paginated GitHub listings are treated as authoritative and revoke page-two evidence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:233` and several activity calls use finite/non-paginated listings, but a status-free result is treated as complete and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:68` invalidates all existing activity visibility. The report itself documents non-paginated API incompleteness at `discovery.py:151`. Direct reproduction with an older approved commit outside a successful truncated first page removed the profile claim and draft evidence.

Minimal fix: make an endpoint authoritative only after exhaustive pagination, or conservatively treat a bounded/truncated endpoint listing as incomplete and preserve previous activity visibility for that endpoint. Add a page-two approved-activity re-discovery regression. Update the discovery-report limitation wording only if behavior changes.

## Ponytail Cleanup

No new cleanup. These validation and bounded-listing checks are necessary to prevent false successful endpoint coverage.

## Next Minimal Checks

1. Enforce activity URL type/repository binding during candidate parsing and legacy artifact revalidation.
2. Prevent a finite first page from revoking older approved activity evidence; keep policy fail-open only until an endpoint can establish complete coverage.
3. Run focused/full tests, `git diff --check`, and direct malformed/cross-repository/truncated-page profile-draft reproductions.
