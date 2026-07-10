# Team Based Review Loop 4 - Re-Review Findings

Date: 2026-07-10
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `6a19ea8 fix: enforce current approval for artifacts`
Status: NEEDS WORK

## Evidence Checked

- Main verification: `./.venv/bin/python -m pytest -q` -> `69 passed`.
- Diff hygiene: `git show --check --format=short HEAD` -> passed.
- Reviewer lanes rerun with the same team:
  - `@ponytail`: PASS.
  - `agency-router` / `codebase-onboarding`: PASS.
  - `agency-router` / `technical-writer`: one P3 handoff drift.
  - `agency-router` / `reality-checker`: one P2 approval parsing bug.

## Closed Findings

- Approval revocation and newly forbidden paths are excluded from regenerated profile and portfolio artifacts.
- Ponytail cleanup requests are closed in committed code.
- MVP plan deferred-schema drift is closed by a top-level historical note.
- `export_artifacts` no longer appears as a current use case in README, skill, specs, plan, source, or tests.

## Findings

### P2 - Non-object approval JSON raises traceback instead of clean CLI error

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:51`
- Evidence: `load_approval()` assumes parsed JSON is a dict and calls `payload.get(...)`. If `.portfolio-maker/reviews/source-approval.json` contains valid non-object JSON such as `[]`, `portfolio-maker ingest` raises `AttributeError` and prints a traceback instead of going through the CLI's `ApprovalFormatError` handling.
- Smallest fix: reject non-dict approval payloads with `ApprovalFormatError`, add a CLI test for `[]`, then rerun full tests.

### P3 - Handoff still lists deferred schema tables as implemented

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/handoffs/2026-07-09-portfolio-maker-mvp-status.md:145`
- Evidence: the handoff still says the implemented schema includes `evidence_items`, `projects`, `career_claims`, `claim_evidence`, and `artifacts`. Current runtime schema creates only `sources`, `source_snapshots`, and `github_activities`; README and tests now state future tables are deferred.
- Smallest fix: replace the bullet with a qualified note that this was historical/pre-reduction, and current `0.1.0` creates only `sources`, `source_snapshots`, and `github_activities`; the other tables are deferred.

## Ponytail Cleanup

PASS: `audit.py` deleted, unused approval request/result models removed, `SnapshotStore` flattened, duplicate GitHub filtering moved to the connector, and the diff is net shorter.

## Next Minimal Checks

- CLI malformed approval test for top-level `[]`.
- Full suite: `./.venv/bin/python -m pytest -q`.
- Hygiene: `git show --check --format=short HEAD`.
- Re-run the same four review lanes.
