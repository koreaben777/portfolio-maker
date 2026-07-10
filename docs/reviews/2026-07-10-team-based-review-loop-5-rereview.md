# Team Based Review Loop 5 - Re-Review Findings

Date: 2026-07-10
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d491cd1 fix: validate approval JSON payloads`
Status: NEEDS WORK

## Evidence Checked

- Main verification: `./.venv/bin/python -m pytest -q` -> `70 passed`.
- Focused approval/CLI set: `./.venv/bin/python -m pytest -q tests/test_cli.py tests/test_approval.py` -> `10 passed`.
- Diff hygiene: `git show --check --format=short HEAD` -> passed.
- Direct CLI repro for `source-approval.json` containing `[]`: exit code `1`, stderr `approval payload must be an object`, no traceback.
- Reviewer lanes rerun with the same team:
  - `@ponytail`: PASS.
  - `agency-router` / `codebase-onboarding`: PASS.
  - `agency-router` / `technical-writer`: one P3 docs drift.
  - `agency-router` / `reality-checker`: PASS.

## Closed Findings

- Non-object approval JSON now fails cleanly through `ApprovalFormatError`.
- Handoff schema wording now distinguishes current 0.1.0 runtime tables from deferred tables.
- Approval revocation and newly forbidden paths remain closed.
- Ponytail cleanup remains closed.

## Findings

### P3 - Architecture specs overstate 0.1.0 approval fields

- Location:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:265`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design-ko.md:265`
- Evidence: both specs say approval can include approved repositories and excluded file patterns. Runtime approval supports only `approved_source_uris`, `forbidden_paths`, `excluded_repositories`, and `private_sources_allowed`.
- Smallest doc fix: replace the approval bullet list in both specs with the exact 0.1.0 fields, and add one sentence that repository allowlists and excluded file patterns are deferred/not implemented in 0.1.0.

## Ponytail Cleanup

PASS.

## Next Minimal Checks

- Update both architecture specs only.
- Run a quick search for stale approval field names.
- Full suite: `./.venv/bin/python -m pytest -q`.
- Hygiene: `git show --check --format=short HEAD`.
- Re-run the same four review lanes.
