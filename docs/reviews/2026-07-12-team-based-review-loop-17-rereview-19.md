# Team Based Review Loop 17 - Re-Review 19 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `08aed72cdd41e53eed82cac3c08d67c6bd8040b1`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 18 report and `d379083..08aed72`.
- Independent verification: full suite `296 passed`; focused GitHub/policy/profile/approval/SQLite/discovery suite `245 passed`; `git diff --check` and `git show --check --format=short` passed.
- Fresh independent `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker lanes.

## Findings

### P1 - U+180F invisible mark is omitted from hidden-secret detection

`src/portfolio_maker/infrastructure/policy.py:82` builds `range(0x180B, 0x180F)`, which excludes U+180F. A value equivalent to `Bearer<U+180F> token` is accepted, unmasked, and emitted as public evidence.

Minimal fix: include the complete U+180B through U+180F range and add parser plus persisted-row regressions using U+180F.

### P1 - Hidden-secret detection covers bearer only

`src/portfolio_maker/infrastructure/policy.py:160` checks only the bearer pattern after removing invisible marks. A hidden-mark `sk-`, `github_pat_`, or `gh*` token bypasses both redaction and exclusion, despite equivalent visible forms already being covered by `mask_secrets`.

Minimal fix: detect whether the invisible-mark removal changed the value and whether applying the existing `mask_secrets` policy to that detection-only value would redact it. Remove the bearer-only duplicate rule. Preserve the explicit behavior that unmodified ordinary bearer values are masked rather than excluded.

### P2 - Legacy URL aliases still win over rediscovered current activities

`src/portfolio_maker/infrastructure/sqlite_repository.py:527` canonicalizes only new inserts. Existing case-variant legacy rows are normalized only when read, so a later canonical upsert creates a second row. `build_profile` deduplicates in ID order and emits stale legacy title/evidence instead of the rediscovered current activity.

Minimal fix: before the activity upsert, locate a canonical-equivalent legacy row and update that row in place with the canonical key and current discovery values, preserving its ID. Add a legacy-row -> current-upsert -> repeated-build regression proving one row and current evidence.

## Ponytail Cleanup

Replace the bearer-specific hidden-secret matcher with the existing masking policy instead of adding parallel token-pattern maintenance. Keep the SQLite repair limited to canonical-equivalent activity rows.

## Next Minimal Checks

1. Verify U+180F and U+034F bearer/token variants are excluded at parser and persisted-row gates.
2. Verify normal bearer values continue through the existing masking path.
3. Verify legacy case-variant URL plus rediscovery yields one stored activity and current profile/draft content across repeated builds.
4. Run focused and full suites plus diff whitespace checks.
