# Team Based Review Loop 17 - Re-Review 12 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d07c98b1b783f97cd4acd8242cf43ad49c59cdd8`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 11 report, `35c5652..d07c98b`, parser/profile source serialization, and current GitHub workflow-runs REST documentation.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `264 passed in 8.08s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `195 passed in 2.57s`.
- `git diff --check 35c5652..d07c98b` and `git show --check --format=short d07c98b` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes. Three lanes passed; reality-checker found the P2s below.

## Closed Findings

- Parser and legacy state validation now rejects bearer-shaped/control-only values and profile serialization applies defensive masking.
- The @ponytail, logical-flow, and technical-writer lanes found no further issue within their scopes.

## Still Open / Newly Found Findings

### P2 - Legacy GitHub source display name and owner are serialized without trusted derivation

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:194` serializes GitHub source `display_name` and `owner` directly. A legacy row with a valid source URI/activity repo but secret-shaped display name/owner creates a public claim and preserves both fields in `master-profile.json`; Markdown also exposes the display name.

Minimal fix: for GitHub sources that pass URI/repository identity validation, derive `display_name` and `owner` from canonical `repository_name` before profile payload/Markdown serialization. Add a legacy-row regression covering both fields.

### P2 - Workflow conclusion and status use one union allowlist

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:240` validates `conclusion` and `status` through one union allowlist. Invalid field combinations such as `conclusion="queued", status="completed"` are accepted, and unsupported legacy state values can remain eligible. GitHub’s current list endpoint exposes status/conclusion values in one query parameter but distinguishes their semantics; its documented values include conclusion-style `success` and status-style `queued`/`in_progress`.

Minimal fix: split conclusion and status allowlists, retain which field supplied the state during parsing, and revalidate legacy workflow state against supported values. Add parser and legacy-row regressions for invalid field combinations and unsupported state values.

## Ponytail Cleanup

No new cleanup. These are public-evidence integrity boundaries.

## Next Minimal Checks

1. Derive public GitHub source label/owner from canonical repository identity, not legacy free text.
2. Validate workflow conclusion/status according to field provenance and reject unsupported legacy values.
3. Run focused/full tests, `git diff --check`, and direct legacy-source/workflow-state reproductions.
