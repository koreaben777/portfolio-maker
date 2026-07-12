# Team Based Review Loop 17 - Re-Review 7 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d5f7b8a74289734768d42e9db13c258a3ab0b90d`
Status: NEEDS WORK

## Evidence Checked

- Prior Loop 17 reports, `4c8b814..d5f7b8a`, current Phase 1 contract, README, repo skill, historical specs, plan, and previous 0.1.0 final report.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `238 passed in 7.74s`.
- Focused GitHub/discovery/profile/draft suite -> `119 passed in 1.07s`.
- `git diff --check 4c8b814..d5f7b8a` and `git show --check --format=short d5f7b8a` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Two lanes independently reproduced repository-cap and repository-URL binding findings.

## Closed Findings

- Candidate activity URLs now match the endpoint repository and expected activity type; legacy activity URL-to-row repository association is revalidated before artifacts.
- Activity endpoint requests reaching their finite caps are incomplete and preserve existing activity visibility rather than falsely revoking page-two evidence.
- Current README and discovery report state the endpoint-cap fail-open behavior.

## Still Open / Newly Found Findings

### P2 - Activity URL identifiers are not type-specific enough

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:47` permits arbitrary identifier text matching `[A-Za-z0-9._-]+`. Invalid routes such as `pull/not-a-pr`, `issues/not-an-issue`, and `actions/runs/not-a-run` can pass parser identity, complete an endpoint, and revoke valid old evidence before the malformed replacement is later filtered.

Minimal fix: enforce type-specific GitHub identifier shapes in `public_github_activity_identity()` (numeric PR/issue/workflow identifiers and valid commit SHA form; retain the safe review-comment fragment rule). Cover parser rejection and legacy profile exclusion.

### P2 - Repository list cap is incorrectly treated as authoritative

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:203` calls `gh repo list --limit 100` without tracking completeness. When exactly 100 repositories are returned and no activity endpoint reports a status, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:67` globally invalidates activity visibility, including approved evidence from repository 101.

Minimal fix: carry repository-list completeness separately. At the cap, classify the list as incomplete and do not perform global/unconfirmed-repository visibility invalidation; retain endpoint-level invalidation only for returned confirmed-public repositories. Add `<100`, `==100`, and partial-discovery regressions.

### P2 - Repository candidate and legacy source URLs are not bound to the canonical repository

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:73` accepts any repository `url`. A `nameWithOwner="octo/demo"` row with `url="https://github.com/octo/private"` can later cause public artifacts to emit the mismatched URI. Likewise, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:128` checks GitHub source type/status but not whether its stored URI identifies the same repository as `activity.repo`.

Minimal fix: validate repository candidate URL-to-`nameWithOwner` binding during discovery and require a legacy source URI to resolve to the same canonical repository as its activity before artifact output. Cover malformed, cross-repository, and non-public source URI cases.

### P3 - Remaining historical documents are presented as current 0.1.0 scope

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-portfolio-maker-0.1.0-final-report.md:42`, `:45`, and `:164` describe pre-Phase-1 GitHub/schema limits as current. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/plans/2026-07-09-portfolio-maker-mvp.md:11` calls the three-table, discovery-only design the implemented 0.1.0 contract. The bilingual architecture specs retain similar current-scope statements at `architecture-design.md:205,378` and `architecture-design-ko.md:205,378`.

Minimal fix: label these records as pre-Phase-1 historical snapshots/baselines and link the current Phase 1 policy contract. Do not change their historical evidence or claim later features as part of their original state.

## Ponytail Cleanup

No additional cleanup beyond the P3 historical labeling. The new P2 checks are necessary to prevent false complete discovery coverage and mismatched public evidence.

## Next Minimal Checks

1. Enforce type-specific activity identifiers and repository URL identities at parser and legacy artifact boundaries.
2. Track repository-list completeness separately from activity endpoint completeness; preserve activity not covered by a capped list.
3. Mark remaining reports/plans/spec wording as historical baseline and link the authoritative current Phase 1 contract.
4. Run focused/full tests, `git diff --check`, and direct identifier/repository-cap/source-URI reproductions.
