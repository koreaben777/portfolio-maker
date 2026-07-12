# Team Based Review Loop 17 - Re-Review 14 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `de8b0376749969ba8aa5a9696d877320dc32a1b9`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 13 report, `3cd809d..de8b037`, workflow parser/provenance migration, profile eligibility, README, repo skill, and Phase 1 contract.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `275 passed in 8.33s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `206 passed in 2.85s`.
- `git diff --check 3cd809d..de8b037` and `git show --check --format=short de8b037` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Two lanes independently found the first P2.

## Closed Findings

- Workflow state provenance is now live through parser, discovery, SQLite migration/hydration, and profile eligibility; pre-migration null provenance is fail-closed.
- Pair validation rejects blank/missing/incompatible new workflow payloads.
- @ponytail and reality-checker lanes found no additional migration/idempotency issue within their scopes.

## Still Open / Newly Found Findings

### P2 - Persisted `state_field=status` still accepts `state=completed`

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:501` treats `completed` as allowed for `state_field="status"`. A migrated or malformed SQLite row with that pair bypasses parser rules, passes `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:126`, and becomes public artifact evidence. A completed workflow must carry a validated conclusion.

Minimal fix: allow only non-`completed` status states for `state_field="status"`; require `state_field="conclusion"` for completed workflow outcomes. Add SQLite-hydration-to-profile/draft regression.

### P2 - Legacy workflow provenance recovery is undocumented

Provenance migration makes pre-migration workflow rows `state_field=NULL` and artifact-ineligible until re-discovery, but `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/README.md:111`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:10`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md:162` omit this recovery action.

Minimal fix: document that legacy workflow activity with unknown provenance remains artifact-ineligible until `portfolio-maker discover --workspace .` succeeds; then users may reapprove the exact URL if needed.

## Ponytail Cleanup

No new cleanup. State-field restriction and recovery documentation are required public-evidence boundaries.

## Next Minimal Checks

1. Reject persisted `status/completed` workflow rows at field-specific profile eligibility.
2. Document migration recovery consistently in README, repo skill, and current Phase 1 contract.
3. Run focused/full tests, `git diff --check`, and direct legacy workflow/recovery documentation checks.
