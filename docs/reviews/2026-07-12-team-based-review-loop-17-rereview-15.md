# Team Based Review Loop 17 - Re-Review 15 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `880ff60cb8acdc3626a16f47b9f0d6fea3619e1a`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 14 report, `de8b037..880ff60`, public profile serialization, masking policy, workflow state validation, and current Phase 1 policy.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `275 passed in 8.39s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `206 passed in 3.01s`.
- `git diff --check de8b037..880ff60` and `git show --check --format=short 880ff60` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Three lanes passed; reality-checker found the findings below.

## Closed Findings

- Persisted `state_field=status, state=completed` is artifact-ineligible.
- README, repo skill, and Phase 1 contract align on legacy workflow recovery via successful discovery and exact URL reapproval.

## Still Open / Newly Found Findings

### P2 - Generic bearer credential in GitHub author reaches public profile unmasked

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:57` masks only `Authorization: Bearer ...`. A workflow `actor.login` containing `Bearer test-value` is accepted at `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:241`, then serialized in public profile author metadata at `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:188`. Direct reproduction confirmed the raw value remains in `master-profile.json`.

Minimal fix: make bearer-credential masking generic for public values and add parser-to-artifact regression proving raw sentinel absence.

### P3 - Workflow state control characters are stripped before validation

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:439` normalizes before state validation. `is_valid_github_activity_state("workflow_run", "queued\\x00", "status")` returns true, and an approved persisted row can become public evidence.

Minimal fix: reject Unicode control characters before normalization in workflow parser and persisted-state validation. Add a suffix-control regression.

## Ponytail Cleanup

No new cleanup. These are public-evidence privacy and malformed-metadata boundaries.

## Next Minimal Checks

1. Mask generic bearer credentials in public values, including author metadata.
2. Reject state control characters before normalization at parser and legacy gates.
3. Run focused/full tests and direct author/state regressions.
