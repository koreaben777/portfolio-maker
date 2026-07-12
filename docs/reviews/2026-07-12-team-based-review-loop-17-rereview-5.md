# Team Based Review Loop 17 - Re-Review 5 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `2896be9e792e5ea9b7b98efa7ff1d656501dbaef`
Status: NEEDS WORK

## Evidence Checked

- Prior Loop 17 reports, source/spec/README/repo skill contracts, and `8c3e5b9..2896be9`.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `231 passed in 7.58s`.
- Focused GitHub/discovery/profile/draft suite -> `112 passed in 0.79s`.
- Direct timestamp check accepted valid `Z` and offset forms and rejected `not-a-timestamp`, impossible date, and missing offset.
- `git diff --check 8c3e5b9..2896be9` and `git show --check --format=short 2896be9` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Two independent lanes reproduced the commit timestamp P2.

## Closed Findings

- PR, issue, review-comment, and workflow candidates reject blank and malformed RFC3339/ISO-8601 timestamps.
- Legacy stored rows with malformed timestamps are excluded from public profile/draft artifacts.
- `GitHubDiscoveryResult` now retains only completed endpoint coverage; failed endpoint state remains in existing status messages and tuple compatibility residue was removed.

## Still Open / Newly Found Findings

### P2 - Commit timestamp bypasses validation and can incorrectly revoke valid prior evidence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:126` still reads `commit.author.date` with `_optional_string()`. A malformed nonblank value such as `"not-a-timestamp"` therefore creates a commit candidate and marks `("octo/demo", "commit")` complete at `github_connector.py:259`. During a partial re-discovery, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:76` revokes prior valid commit evidence as though that malformed response were authoritative; the replacement is only skipped later by profile validation.

Two independent reproductions confirmed the candidate is rendered for review, statuses stay empty, and the completed commit endpoint is present. Minimal fix: require `_required_timestamp(author, "date", "commit list")`; add blank/malformed commit-date parser coverage and a partial re-discovery regression proving an invalid commit payload becomes an endpoint failure that preserves prior valid commit evidence.

### P2 - Historical and Phase 1 specifications contradict the implemented GitHub approval policy

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md:10` says GitHub activity is discovery-only, then line 15 says explicitly approved public activity can enter profile/portfolio. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:4` labels the implemented 0.1.0 MVP as discovery-only and line 22 says it never enters profiles or drafts. This conflicts with README, repo skill, and current Phase 1 implementation.

Minimal fix: label the older architecture statements as a pre-Phase-1 historical baseline or update them to the exact current rule: discovery metadata by default; only URL-explicitly approved public-repository activity that passes policy revalidation may appear as evidence, never an automatic project narrative. Make the Phase 1 specification internally consistent and identify it as the current authoritative contract.

### P3 - Required-timestamp parser regression duplicates four assertion bodies

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/tests/test_github_connector.py:153` and `:176` repeat the same PR/issue/review/workflow invalid-timestamp parser assertions for `""` and `"not-a-timestamp"`.

Minimal fix: parameterize the existing test over those invalid values (and include commit date coverage required by the P2 fix) so each parser contract has one concise matrix.

## Ponytail Cleanup

- P3 above is the only remaining cleanup. Keep the completed-endpoint structured contract; it closes the accepted partial-discovery P1.

## Next Minimal Checks

1. Validate commit timestamp before the endpoint is recorded complete and prove partial re-discovery retains prior valid commit evidence on invalid payload.
2. Align current and historical specification wording with the explicit approved-public GitHub evidence policy.
3. Parameterize the timestamp parser tests without reducing coverage.
4. Run focused/full tests, `git diff --check`, and direct malformed commit/partial re-discovery verification.
