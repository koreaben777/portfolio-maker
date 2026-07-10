# Team Based Review Loop 3 - Re-Review Findings

Date: 2026-07-10
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `c8ea28a fix: close team review loop 3 findings`
Status: NEEDS WORK

## Evidence Checked

- Main verification: `./.venv/bin/python -m pytest -q` -> `68 passed`.
- Focused policy/ingestion/sqlite/github set -> `44 passed`.
- Diff hygiene: `git show --check --format=short HEAD` -> passed.
- Reviewer lanes rerun with the same team:
  - `@ponytail`: still found shrink candidates.
  - `agency-router` / `codebase-onboarding`: PASS.
  - `agency-router` / `technical-writer`: found remaining plan/spec drift.
  - `agency-router` / `reality-checker`: found one P1 artifact privacy boundary.

## Closed Findings

- Multi-word bare secret values are now covered and masked.
- Already ingested missing files are marked stale, and changed files are re-ingested.
- GitHub remains discovery-only in runtime and architecture specs.
- Previous future-only domain classes, normalized schema tables, unused `DiscoveryReport`, extra GitHub repo fields, and `github_snapshots_dir` were removed.

## Findings

### P1 - Revoked or newly forbidden ingested sources still feed generated profile artifacts

- Location:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:39`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:18`
- Evidence: `ingest_sources()` skips sources no longer approved and skips forbidden paths without clearing an existing `INGESTED` status. `build_profile()` then selects all `INGESTED` sources without checking the current approval file or current forbidden policy.
- Runtime repro from reviewer: after adding an already-ingested source's parent to `forbidden_paths`, rerunning ingest/build-profile kept `status_after_forbidden=ingested`, `profile_claim_count_after_forbidden=1`, and `profile_still_has_private_text=True`. Removing the URI from `approved_source_uris` likewise kept the revoked source in generated profile output.
- Risk: a user can revoke approval or newly forbid a directory, yet old evidence still appears in regenerated master profile and downstream portfolio drafts.
- Smallest fix: add regression tests for "ingested then approval revoked" and "ingested then parent forbidden"; move revoked/forbidden previously ingested sources out of `INGESTED`, or filter `build_profile()` against current approval and `FilePolicy`. Prefer the smaller approach that makes regenerated artifacts exclude revoked/forbidden evidence.

### P2 - MVP plan still presents deferred SQLite schema as current implementation work

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/plans/2026-07-09-portfolio-maker-mvp.md:853`
- Evidence: Task 5 snippets still assert and create `evidence_items`, `projects`, `career_claims`, `claim_evidence`, and `artifacts`, while current runtime schema intentionally creates only `sources`, `source_snapshots`, and `github_activities`.
- Smallest doc fix: add a clear top-of-plan note that this is historical and current `0.1.0` contract is README/spec/runtime, or update the Task 5 snippets to the current three-table MVP and mark the other tables deferred.

### P3 - Architecture specs still list `export_artifacts` as a current use case

- Location:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:186`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design-ko.md:186`
- Evidence: current runtime writes artifacts inside `build_profile` and `draft_portfolio`; there is no separate `export_artifacts` use case.
- Smallest doc fix: remove `export_artifacts` from current use-case lists or qualify it as folded into `build_profile` and `draft_portfolio` for `0.1.0`.

## Ponytail Cleanup

The ponytail lane still requests these shrink items:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/audit.py:10`: remove unused audit event/log code and audit-only workspace/spec/test hooks until a runtime writer exists.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:60`: remove duplicate GitHub private/excluded filtering after the same policy has been passed to `discover_github_candidates`.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/models.py:31`: delete unused `ApprovalRequest` and `ApprovalResult`.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/snapshots.py:11`: replace one-method `SnapshotStore` with a function only if this stays a clear net simplification.

Estimated shrink opportunity: about 144 lines. Treat P1 as higher priority than shrink work.

## Next Minimal Checks

- Regression: revoked approval excludes prior ingested evidence from regenerated profile artifacts.
- Regression: newly forbidden parent path excludes prior ingested evidence from regenerated profile artifacts.
- Full suite: `./.venv/bin/python -m pytest -q`.
- Hygiene: `git show --check --format=short HEAD`.
- Re-run the same four review lanes.
