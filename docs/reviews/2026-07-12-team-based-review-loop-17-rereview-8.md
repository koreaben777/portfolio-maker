# Team Based Review Loop 17 - Re-Review 8 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d5f7b8a74289734768d42e9db13c258a3ab0b90d` plus uncommitted Re-Review 7 changes
Status: NEEDS WORK

## Evidence Checked

- Re-Review 7 report, current uncommitted 10-file diff, Phase 1 contracts, README, repo skill, and historical baseline records.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `253 passed in 7.96s`.
- Focused GitHub/discovery/profile/draft suite -> `134 passed in 1.11s`.
- `git diff --check` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Three lanes independently reproduced the repository-cap P2.

## Closed Findings

- Type-specific GitHub activity identifiers, repository candidate URL binding, legacy source URI association, and the basic repository-cap completeness model have focused regressions.
- Historical final report, plan, and architecture wording is now labeled pre-Phase-1 baseline and points to the current policy contract.
- `@ponytail` found no over-implementation, dead helper, or duplicate-test issue in the current diff.

## Still Open / Newly Found Findings

### P2 - Repository-list cap completeness is calculated after canonical deduplication

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:225` calculates `repositories_complete` after `parse_repo_list()` has deduplicated canonical-equivalent rows. A raw 100-row response such as `Octo/Demo` plus 99 `octo/demo` rows reduces to one candidate and is incorrectly reported complete. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:67` then globally invalidates valid activity for unreturned repositories.

Minimal fix: determine cap completeness from the raw validated repository-list length before canonical deduplication (or reject duplicates). Treat raw count `==100` as incomplete and add a duplicate-at-cap regression preserving unreturned approved activity evidence.

### P2 - Capped repository list preserves activity for a repository explicitly observed as private

At `github_connector.py:228`, private repositories are filtered from discovery candidates. When a capped repository list is incomplete, `discovery.py:73` skips unconfirmed-repository invalidation; therefore a previously public approved activity remains eligible even if the raw list explicitly reports its repository as `isPrivate=true`. Direct reproduction retained one profile claim after a capped list reported the repository private.

Minimal fix: carry observed private repository identities separately from policy-filtered candidates. Even on incomplete repository lists, immediately mark existing activity for observed private repositories ineligible; preserve activity only for repositories not observed by the capped list. Add a capped-private re-discovery regression.

### P3 - Incomplete discovery status is rendered as a discovery failure

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:176` prefixes every status with `GitHub discovery failed`, including cap statuses such as `GitHub repository list discovery incomplete`. The report therefore misstates an intentionally conservative incomplete result as a failure.

Minimal fix: render an accurate neutral status label for incomplete results, retain failure wording for actual endpoint failures, and include the repository-list cap in the report limitation text.

## Ponytail Cleanup

No new cleanup. The requested changes refine the necessary repository completeness boundary.

## Next Minimal Checks

1. Base cap detection on raw repository-list cardinality and preserve unreturned evidence when the cap is reached.
2. Invalidate previously public activity when a capped raw list explicitly confirms its repository private.
3. Distinguish incomplete from failed discovery in the report.
4. Run focused/full tests, `git diff --check`, and direct duplicate-cap/capped-private/report-label reproductions.
