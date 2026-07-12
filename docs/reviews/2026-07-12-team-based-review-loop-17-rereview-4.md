# Team Based Review Loop 17 - Re-Review 4 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `8c3e5b9d4af47cb9aa5a77adda71bd161f9b7e8e`
Status: NEEDS WORK

## Evidence Checked

- Prior Loop 17 reports, current Phase 1 contracts, and `73eff2f..8c3e5b9`.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `229 passed in 7.46s`.
- Focused GitHub/discovery/profile/draft suite -> `110 passed in 0.74s`.
- Endpoint matrix for full/empty success, same/cross repository failure, public-to-private, allowlist/excluded -> `8 passed`.
- `git diff --check 73eff2f..8c3e5b9` and `git show --check --format=short 8c3e5b9` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes.

## Closed Findings

- Per-repository/activity-endpoint outcomes revoke only activity types whose endpoint completed successfully, including empty result sets; failed endpoint types remain retryable on confirmed-public repositories.
- Public-to-private, same-repository partial failure, and cross-repository partial failure all exclude stale evidence from profile and draft.
- README and repo skill now use the actual `GitHub Repositories` and `GitHub Activities` report sections and require repository `(public)` confirmation before a URL is approved.

## Still Open / Newly Found Findings

### P2 - Nonempty malformed GitHub timestamps can enter public evidence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:97` now requires a nonblank PR `createdAt`, but still accepts `"not-a-timestamp"`; equivalent paths exist for issue, review-comment, and workflow timestamps. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:118` only rechecks nonblank text, so a legacy malformed timestamp is emitted as public `created_at` metadata. Direct reproduction confirmed an approved profile claim with `created_at="not-a-timestamp"`.

Minimal fix: add one standard-library RFC3339/ISO-8601 timestamp validator for GitHub candidate parsers and the legacy-row revalidation in `build_profile()`. Reject nonblank malformed timestamps in parser tests and skip malformed legacy rows in artifact tests.

### P3 - Endpoint outcome stores unused failure state and retains a tuple compatibility branch

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:36` emits both success and failure `GitHubEndpointOutcome` values, but `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:75` consumes only successful outcomes; `statuses` already retains failure information. The same result object also keeps `__iter__()` plus `isinstance()` compatibility handling although this internal connector now has one structured contract.

Minimal fix: record only completed `(repository, activity_type)` coverage in `GitHubDiscoveryResult`, remove failed outcome objects and tuple compatibility branching, and update test doubles to construct the structured result. Preserve status messages for user-facing failures.

## Ponytail Cleanup

- P3 above is the only actionable cleanup. The structured completed-endpoint coverage itself is required by the accepted P1 policy and must remain.

## Next Minimal Checks

1. Validate real GitHub timestamp syntax both at candidate parsing and legacy artifact revalidation.
2. Reduce endpoint coverage to only the state used by discovery policy; keep outcome behavior and failure messages unchanged.
3. Run focused timestamp/connector/discovery/profile tests, full `pytest`, `git diff --check`, and direct malformed legacy timestamp verification.
