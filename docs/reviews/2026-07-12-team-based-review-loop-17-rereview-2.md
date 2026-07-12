# Team Based Review Loop 17 - Re-Review 2 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `6132db517028de49d8322e6b52202b9572cdb7ed`
Status: NEEDS WORK

## Evidence Checked

- Previous Loop 17 reports and the Phase 1 policy/evidence/GitHub specification.
- `git diff cdde6ea..6132db5`, `git diff --check cdde6ea..6132db5`, and `git show --check --format=short 6132db5`.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `224 passed in 7.51s`.
- Focused approval/GitHub/discovery/profile/draft suite -> `105 passed in 0.67s`.
- Direct URL boundary check: only `https://github.com/octo/demo/pull/1#discussion_r1` maps to `review_comment`; arbitrary fragment, query, credentials, and raw control-bearing URLs map to `None`.
- Fresh independent lanes: `@ponytail`, codebase-onboarding logical flow, technical-writer contract, and reality-checker.

## Closed Findings

- The safe `#discussion_r<digits>` review-comment URL is accepted through discovery, explicit approval, profile, and draft evidence rendering.
- Approval URL validation rejects raw whitespace, Unicode control characters, query strings, arbitrary fragments, and credentials before artifact use.
- Blank workflow state becomes a contained endpoint failure; a public repository with an incomplete endpoint response preserves its previously confirmed activity visibility.
- The requested ponytail cleanup removed redundant URL validation and the draft return-contract fallback. The ponytail lane found no remaining over-implementation finding.

## Still Open / Newly Found Findings

### P1 - Partial GitHub re-discovery can retain activity from a repository that is now private

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:64` preserves every existing activity visibility row whenever any activity endpoint reports a status. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:193` filters newly private repositories out before returning the repository list, so their formerly public activity rows retain `is_private=False`. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:115` then treats those approved rows as public evidence.

Two independent lanes reproduced this path: discover and approve an activity while public, then re-discover after that repository becomes private or disappears while another endpoint fails. The profile still reported `claim_count=1` and the stored activity visibility remained public.

Minimal fix: use the successfully returned repository-list visibility independently of endpoint completeness. Mark activity rows for repositories no longer confirmed public as `is_private=1`; retain prior activity visibility only for repositories still confirmed public whose activity endpoint is incomplete. Add a public-to-private plus unrelated endpoint-failure regression that proves profile and draft exclude the old activity.

### P2 - GitHub activity approval is documented in an unusable order

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:36` tells users to edit `approved_github_activity_urls` before discovery. The Phase 1 contract at `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md:158` requires the exact activity URL that discovery stored. The later skill step at `SKILL.md:52` describes only local-source approval, so it never tells a user when to copy a discovered GitHub activity URL into the approval file.

Minimal fix: before discovery, document only repository visibility policy fields. After reviewing the discovery report, explicitly instruct users to copy selected public `GitHub Activities` URLs into `approved_github_activity_urls`, then complete local-source approval. Align the README quick-start wording with that workflow.

## Ponytail Cleanup

No new cleanup finding. The new validation is a small boundary check rather than a second abstraction, and the focused regressions cover separate failure modes.

## Next Minimal Checks

1. Add a focused P1 regression for public-to-private repository transition with an unrelated endpoint failure; verify no profile claim or draft evidence remains.
2. Update only the workflow documentation needed to make post-discovery GitHub URL approval executable.
3. Run the affected focused tests, the full suite, `git diff --check`, and direct public-to-private/partial-failure verification.
