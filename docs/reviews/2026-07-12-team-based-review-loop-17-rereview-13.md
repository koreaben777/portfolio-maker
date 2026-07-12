# Team Based Review Loop 17 - Re-Review 13 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `3cd809df4915a3b94974bf301f9955fb9910e229`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 12 report, `d07c98b..3cd809d`, workflow parser, discovery persistence, SQLite schema, and profile eligibility.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `270 passed in 8.09s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `201 passed in 2.69s`.
- `git diff --check d07c98b..3cd809d` and `git show --check --format=short 3cd809d` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Two lanes independently found the P2.

## Closed Findings

- Canonical GitHub source metadata is derived after URI/repository validation, and parser-level workflow conclusion/status combinations are more constrained.
- Technical-writer lane found current policy and historical documentation consistent.

## Still Open / Newly Found Findings

### P2 - Workflow state provenance is discarded before persistence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:244` retains `state_field` in the candidate, but `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:109` persists only the state text. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:126` then accepts a union allowlist. A stored row with `state="queued"` can therefore remain public-safe even though it is indistinguishable from the previously accepted invalid `conclusion="queued", status="completed"` payload.

Minimal fix: add constrained workflow `state_field` persistence to `github_activities`, preserve it from parser through discovery/repository hydration, and require field-specific validation for workflow profile eligibility. Pre-migration workflow rows with no provenance must remain ineligible until rediscovery. Add endpoint-to-SQLite-to-profile/draft and ambiguous-legacy regressions.

### P3 - Workflow paired fields allow blank/missing inconsistent combinations

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:436` accepts combinations including blank conclusion with `status="queued"`, `conclusion="success"` with blank status, and missing status. Require a compatible normalized pair: non-completed status requires `conclusion is None`; completed status requires a supported conclusion; blank fields are invalid.

## Ponytail Cleanup

- The earlier suggestion to remove `state_field` is superseded: provenance is required by the accepted P2 and must be persisted rather than removed.

## Next Minimal Checks

1. Migrate/persist constrained workflow state provenance and fail closed for legacy unknown provenance.
2. Reject blank/missing workflow field combinations.
3. Run focused/full tests, `git diff --check`, and direct parser-to-SQLite-to-artifact/legacy workflow regressions.
