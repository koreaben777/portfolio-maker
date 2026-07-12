# Team Based Review Loop 17 - Re-Review 16 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `2e2e88d2fe34c4cea285a59eaf891502620cd1dd`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 15 report, `880ff60..2e2e88d`, GitHub activity parser and persistence gates, public profile serialization, credential masking policy, and the current Phase 1 contract.
- Fresh independent reviewer lanes: `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker.
- The current implementation correctly rejects control characters for `workflow_run` state fields and masks generic bearer credentials before public serialization.

## Findings

### P2 - Non-workflow activity states accept Unicode control characters

`src/portfolio_maker/infrastructure/github_connector.py:538` rejects controls only for `workflow_run`. Pull-request, issue, and commit state values are normalized before validation, so a value such as `"open\\x00"` can be stored and emitted as a public claim. This violates the Phase 1 malformed-metadata exclusion boundary.

Minimal fix: reject Unicode control characters before normalization for every activity type in `is_valid_github_activity_state`, then add parser-to-persisted-artifact regressions for pull-request and issue states (and commit state where applicable).

### P3 - Redundant bearer masking pattern

`src/portfolio_maker/infrastructure/policy.py:58` is unreachable in practice because the preceding generic bearer matcher consumes its input. Remove the redundant authorization-specific pattern, retaining the generic pattern and its existing regression behavior.

### P3 - Historical handoff calls a superseded schema current

`docs/superpowers/handoffs/2026-07-09-portfolio-maker-mvp-status.md:145` labels the old schema description as "Current 0.1.0" despite the document's historical disclaimer. Reword it as a historical handoff-state claim and link the current Phase 1 contract.

## Ponytail Cleanup

Remove only the dead authorization-specific masking rule. No new abstraction or dependency is warranted.

## Next Minimal Checks

1. Confirm control-character state values are rejected before normalization for every GitHub activity type.
2. Confirm malformed persisted pull-request and issue rows cannot produce public claims.
3. Run focused GitHub/profile/SQLite tests, the full suite, and diff whitespace checks.
