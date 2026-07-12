# Team Based Review Loop 17 - Initial Findings

Date: 2026-07-11
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `1cd6d292acde56313c8a00f7b64b92f7eb6c7a16`
Reviewed range: `03fb16f..1cd6d29`
Status: NEEDS WORK

## Evidence Checked

- Phase 1 specification: `docs/superpowers/specs/2026-07-11-portfolio-maker-roadmap-phase-1-policy-evidence-github.md`.
- Runtime paths: approval, GitHub discovery, SQLite repository, profile build, and draft portfolio generation.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `200 passed in 7.33s`.
- Focused policy/profile/storage tests -> `131 passed in 1.63s`.
- `git diff --check 03fb16f..1cd6d29` and `git show --check --format=short 1cd6d29` -> pass.
- Isolated reproductions:
  - a disappeared activity after a successful re-discovery remains a profile claim (`1 -> 1`);
  - an empty activity `state` produces one approved claim;
  - a filename pattern containing NUL is accepted;
  - a GitHub-only draft omits the activity URL and title and records only `master_profile` artifacts.

## Findings

### P1 - Stale public GitHub activity remains an artifact input

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:64` only upserts results from a successful discovery. It does not invalidate pre-existing GitHub activities that are no longer observed. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:106` can therefore continue to emit a previously public, approved activity after that activity/repository becomes unavailable or private.

Minimal fix: after a successful repository-list discovery, conservatively invalidate prior GitHub activity visibility/last-seen state, then reactivate only newly confirmed public activities. Preserve the existing fail-open behavior when the GitHub discovery itself fails.

### P1 - Approved activity is rendered as a project skeleton rather than activity evidence

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:25` consumes only profile sources. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:29` consequently renders an approved GitHub activity as a project section with Role/Technical approach/Outcome placeholders. The exact approved URL, title, and activity type are absent. This contradicts Phase 1 Stage C: GitHub activity must be shown only as reviewable evidence, not an automatically generated project narrative.

Minimal fix: render public approved GitHub claims in a distinct evidence-reference list including the safe title/type and exact public URL. Generate project skeleton sections only from local project evidence. Add an end-to-end test proving private, excluded, unapproved, and stale URLs never render.

### P2 - Portfolio artifact has no provenance manifest

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:200` records only `master_profile`. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:49` writes `portfolio-draft.md` without an `artifacts` record, so its input claim/evidence graph cannot be traced.

Minimal fix: record a `portfolio_draft` artifact with the exact claim/evidence IDs used to render it. Test the persisted manifest.

### P2 - `projects` relationship is schema-only

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:71` creates `projects`, but `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:601` always creates claims with `project_id = NULL`. Phase 1 requires a project unit to connect multiple evidence and claims; the current model cannot exercise that relationship.

Minimal fix: add a small project upsert and associate generated claims with the source/repository project. Do not add company/JD narrative or HTML rendering.

### P2 - Invalid activity metadata can be promoted into a public claim

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:250` accepts an empty `state`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:106` does not revalidate it. An empty-state approved activity reproduced as `claim_count=1`, contrary to the Stage C requirement that unconfirmed/malformed activity remains metadata-only.

Minimal fix: require non-empty state in parser/DB inputs and skip malformed activity at profile build as a defensive check. Add parser and stored-row regressions.

### P2 - Approval URL validation can leak query secrets and raise an uncaught CLI traceback

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:148` accepts query/fragment components and does not convert `urlparse()` `ValueError` into a controlled approval error. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:154` then writes the raw approved URL to the profile artifact.

Minimal fix: allow only canonical `https://github.com/<owner>/<repo>/(actions|commit|issues|pull)/...` paths with no query, fragment, params, credentials, or malformed host; convert parsing failures to `ApprovalFormatError`. Mask public activity title/author text before artifact output and test CLI exit `1` without traceback.

### P2 - Canonical-equivalent repository rows can create duplicate claims

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:40` validates canonical repository names but retains raw case. `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:500` uniqueness is case-sensitive, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:106` has no duplicate guard. Case-only equivalent repository rows produced duplicate profile claims in review reproduction.

Minimal fix: canonicalize/deduplicate repository names during discovery and add a `(canonical repository, activity type, URL)` seen-set when building the profile.

### P3 - Approval filename-pattern validation does not reject every control character

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:105` permits a NUL pattern. Phase 1 requires control characters to be rejected.

Minimal fix: reject every Unicode `C*` category character, including NUL and zero-width controls, with regressions.

### P3 - Repo-scoped Codex skill still states GitHub is discovery-only

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:3` does not describe `approved_github_activity_urls` or the public-only, allowlist/exclusion revalidation now implemented.

Minimal fix: update the skill alongside README to describe the exact approval boundary.

## Ponytail Cleanup

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:629`: `record_artifact()` has no caller that uses its return value. Change it to return `None` and avoid retaining an unused cursor.
- The `projects` table is not deleted as a ponytail candidate because the Phase 1 specification explicitly requires it. Its missing runtime connection is tracked above as P2.

## Closed Findings

- Scope remains within Phase 1. Company/JD-specific writing and the interactive HTML renderer were not added.
- Existing GitHub discovery remains fail-open with respect to local discovery.
- Foreign keys, legacy `github_activities.is_private` safe default, and repeated schema migration were checked without a new defect.

## Next Minimal Checks

1. Add focused failing tests for every finding before changing runtime code.
2. Verify policy rejection, stale re-discovery invalidation, duplicate suppression, and public artifact output in temporary workspaces.
3. Run focused tests, full `pytest`, `git diff --check`, and a final no-secret/no-private-path artifact inspection.
