# Team Based Review Loop 17 - Re-Review 3 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `73eff2f39038e672a2a8457a44e339ff5da1be61`
Status: NEEDS WORK

## Evidence Checked

- Prior Loop 17 reports, Phase 1 policy/evidence/GitHub specification, README, repo skill, and the latest `6132db5..73eff2f` diff.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `225 passed in 7.54s`.
- Focused approval/GitHub/discovery/profile/draft suite -> `106 passed in 0.79s`.
- `git diff --check 6132db5..73eff2f` and `git show --check --format=short 73eff2f` -> pass.
- Fresh independent lanes: `@ponytail`, codebase-onboarding logical flow, technical-writer contract, and reality-checker. Two independent lanes directly reproduced the same stale-activity P1.

## Closed Findings

- A repository that becomes private or disappears from a successfully retrieved repository list is now immediately ineligible even when another activity endpoint fails.
- The pre-discovery/post-discovery GitHub URL approval order is documented in both README and repo skill.
- The latest `@ponytail` lane found no actionable over-implementation. A separate suggestion to merge two semantically distinct invalidation methods was rejected as non-actionable: the two public operations make complete versus partial-discovery policy explicit.

## Still Open / Newly Found Findings

### P1 - Unrelated partial endpoint failure retains activity that its own endpoint confirmed absent

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:64` uses one aggregate `github_statuses` list. Once any endpoint fails, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:542` preserves every activity of every repository that is still public. The code cannot distinguish an activity endpoint that succeeded with an empty result from the endpoint that failed. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:111` then includes the stale approved activity and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:53` renders it.

Two independent reproductions confirmed this. After approving a public pull request, a re-discovery with the same public repository and an empty successful PR list plus either an unrelated workflow failure or a workflow failure in that repository still returned `claim_count=1` and retained the evidence URL in the draft. This contradicts the Phase 1 stale-activity exclusion at `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md:177`.

Minimal fix: return structured outcome coverage by repository and activity endpoint. With a successful repository list, immediately revoke activities for repositories not confirmed public; for confirmed-public repositories, revoke activity types whose own endpoint completed successfully, including empty results, and preserve only types whose own endpoint failed. Add end-to-end regressions for empty PR success plus unrelated workflow failure and for same-repository endpoint failure.

### P2 - Empty required GitHub timestamp can become public evidence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:77` accepts `createdAt=""` because `_required_string()` at `github_connector.py:263` only checks the type. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:173` writes the empty value to a public approved claim. Direct reproduction confirmed that a blank PR timestamp survives discovery and explicit approval as `profile_created_at=''` with `claim_count=1`.

Minimal fix: require nonblank timestamps in PR, issue, review-comment, and workflow parsers. Defensively exclude legacy stored rows with blank timestamps in `build_profile()`. Add parser and stored-row artifact regressions.

### P2 - Approval documentation names a report section that does not exist

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:52` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/README.md:81` refer to a public `GitHub Activities` section. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:149` actually renders one unclassified `GitHub Activities` list. When `private_sources_allowed=true`, it can contain private activity although Phase 1 forbids private activity in public artifacts.

Minimal fix: tell users to choose URLs from `GitHub Activities` only after confirming the matching repository is marked `(public)` in `GitHub Repositories`. Do not claim a separate public activity section exists.

## Ponytail Cleanup

No actionable cleanup. The endpoint-outcome requirement is necessary to make the established stale-evidence policy true under partial failure; it is not optional abstraction.

## Next Minimal Checks

1. Add structured repository/activity-endpoint outcome coverage and verify successful-empty endpoints revoke only their own stale activity types.
2. Add blank timestamp parser and legacy-row exclusions without changing unrelated GitHub behavior.
3. Correct the README/skill wording to the report sections that actually exist.
4. Run focused and full tests, `git diff --check`, and direct profile/draft reproductions for both partial-failure cases.
