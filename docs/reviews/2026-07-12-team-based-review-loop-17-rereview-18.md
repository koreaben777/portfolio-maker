# Team Based Review Loop 17 - Re-Review 18 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d379083f90eea279ccac64768aeaf81bc9f49902`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 17 report and `3e47bc6..d379083`.
- Independent verification: full suite `289 passed`; focused GitHub/profile/approval/SQLite/discovery/policy suite `238 passed`; `git diff --check` and `git show --check --format=short` passed.
- Fresh independent `@ponytail`, agency-router/codebase-onboarding, agency-router/technical-writer, and agency-router/reality-checker lanes.

## Findings

### P1 - Invisible combining marks bypass secret-shaped metadata protection

`src/portfolio_maker/infrastructure/github_connector.py:562` rejects Unicode `C*` controls but accepts invisible `Mn` marks such as U+034F. A title or author equivalent to `Bearer<U+034F> token` reaches `src/portfolio_maker/application/build_profile.py:151`; the generic bearer matcher misses it before normalization, and the raw secret-shaped text becomes public evidence.

Minimal fix: add a public-text secret predicate that tests both the original value and a detection-only projection with invisible combining marks removed. Reject matching title/author values at parser ingress and persisted-row public-evidence gates. Keep existing masking as defense in depth; do not reject ordinary non-secret international text.

### P2 - Case-variant activity URLs create duplicate public claims

`src/portfolio_maker/infrastructure/sqlite_repository.py:529` keys GitHub activities by raw URL, while `src/portfolio_maker/application/build_profile.py:154` also deduplicates with raw URL. Approved URLs differing only in repository owner/name case resolve to the same GitHub activity but generate separate claims and draft evidence across repeated builds.

Minimal fix: introduce and use one canonical public GitHub activity URL key for approval comparison, persistence/upsert, and `seen_activities`, including legacy-row read paths. Add a case-variant approval/discovery/repeated-build regression.

### P2 - Non-workflow state-field gate has no effective regression

`tests/test_profile_and_portfolio.py:379` combines a control-character state with `state_field`. The control check returns first, so the test never exercises the new non-workflow `state_field is None` branch. Replace that state with a valid normal state and retain `status`/`conclusion` injection cases; assert profile and draft exclusion.

### P3 - Persisted metadata tests duplicate setup

`tests/test_profile_and_portfolio.py:379` and `:430` repeat the same workspace, approval, source, raw-SQL, build, draft, and exclusion assertions. Parameterize the corrupted column/value and `state_field` provenance cases in one legacy-row test after correcting the P2 regression above.

## Disposition of Reviewed Reports

- Normal bearer-shaped values that match the existing mask policy are intentionally redacted, not excluded; this behavior is covered by the current parser-to-artifact regression and remains within the Phase 1 contract.
- A manually corrupted SQLite BLOB is outside the supported legacy text-row contract. It is not a reason to weaken the repository's current semantic-error handling in this loop.

## Ponytail Cleanup

Consolidate only the duplicated test setup while adding the missing effective regression. Do not add a general sanitizer or a new dependency.

## Next Minimal Checks

1. Verify invisible-mark bearer variants cannot enter parser output, persisted profile claims, or draft evidence.
2. Verify case-variant activity URLs converge to one approved, persisted, and rendered item across repeated builds.
3. Verify non-workflow state-field injection is rejected with otherwise valid states.
4. Run focused and full tests plus diff whitespace checks.
