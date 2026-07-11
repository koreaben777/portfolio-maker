# Team Based Review Loop 11 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `473f5c045b24f7703c48fa6438ec9dba63ff058f`

Status: NEEDS WORK

## Evidence Checked

- A new reviewer team was created after the model change:
  - Parfit (`019f4b07-2b48-71a1-b50c-de6007ff0f67`): `@ponytail` over-implementation review.
  - Epicurus (`019f4b07-3e90-7631-858b-e75a73e8e2a5`): `agency-router` / `codebase-onboarding` logical-flow review.
  - Erdos (`019f4b07-5aca-7a52-8695-5d5352dbd17f`): `agency-router` / `technical-writer` contract review.
  - Cicero (`019f4b07-721f-7652-96c3-26cf58895fc3`): `agency-router` / `reality-checker` adversarial validation.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `125 passed`.
- `git diff --check`, `git show --check --format=short HEAD`, and CLI help checks passed.
- `30d3cf3..473f5c0` contains review-document changes only; the reviewed runtime implementation is still `30d3cf3`.
- Direct temporary-workspace reproductions confirmed:
  - a managed discovery-report symlink overwrote an external sentinel;
  - invalid UTF-8 approval data exited with a traceback;
  - a non-SQLite database exited with a traceback;
  - a self-referential discovery symlink aborted discovery with a traceback and no report.

## Closed Findings

- Loop 10 descriptor-lifetime, legacy migration, malformed GitHub repository payload, approval, and privacy findings remain covered by the passing suite.
- The documented GitHub discovery-only scope, review-required portfolio skeleton, candidate limits, and retained local history remain explicit 0.1.0 boundaries rather than defects.

## Findings

### P1 - Managed output paths can follow symlinks outside the workspace

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:87`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:42`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/artifacts.py:10`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/workspace.py:47`.
- A symlinked `.portfolio-maker/reviews/discovery-report.md` was followed and its external target was overwritten. Artifact and forced-approval writes use the same path-following pattern.
- Minimal fix: introduce one atomic descriptor-relative managed-file writer, reject symlink/non-directory managed components and non-regular output targets, and add external-sentinel regressions for reports, artifacts, and forced approval writes.

### P2 - Corrupted persisted state bypasses controlled CLI failures

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:58`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:55`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/adapters/cli.py:61`.
- Invalid UTF-8 in `source-approval.json` raises `UnicodeDecodeError`; a non-SQLite `portfolio.db` raises `sqlite3.DatabaseError`. Both reach the installed CLI as tracebacks.
- Minimal fix: map decode/JSON failures to `ApprovalFormatError`, map `sqlite3.Error` to a repository-specific controlled error, preserve the damaged file, and give a concise recovery instruction with exit code 1.

### P2 - Malformed symlinks abort otherwise valid local discovery

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/local_discovery.py:74`.
- A self-referential `loop.txt -> loop.txt` raises uncaught `RuntimeError`, aborts valid neighboring candidates, and produces no discovery report.
- Minimal fix: convert root-resolution failures to `DiscoveryRootError`; record malformed entry-resolution failures as skipped and continue. Add a CLI regression proving valid candidates survive without a traceback.

### P2 - Control characters in labels can forge Markdown structure

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:122`, `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:81`, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:33`.
- A filename containing a newline and Markdown heading text is rendered verbatim in the approval report and generated portfolio.
- Minimal fix: reject or normalize control characters at discovery and apply one Markdown text-escaping/normalization helper to local and GitHub labels used in reports and artifacts.

### P2 - Rebuilding the profile can leave a revoked-source portfolio draft stale

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:92` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:47`.
- After approval revocation and `build-profile`, the profile excludes the source but an already generated `portfolio-draft.md` still lists it.
- Minimal fix: invalidate the existing portfolio draft after a successful profile replacement; `draft-portfolio` can regenerate it immediately from the current profile.

### P2 - Commits without stable URLs collide in SQLite

- Locations: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:97` and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:43`.
- `parse_commit_list()` accepts missing `html_url` as an empty string, while persistence uses `UNIQUE(repo, activity_type, url)`, so distinct malformed commits collapse into one row.
- Minimal fix: require a non-empty `html_url` and route malformed endpoint payloads through the existing per-endpoint `GitHubDiscoveryError`, or persist commit SHA as the stable identity.

### P3 - Duplicate symlink aliases consume the discovery cap

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/local_discovery.py:85`.
- Multiple aliases to the same canonical URI are counted before SQLite deduplication and can exclude a distinct candidate.
- Minimal fix: track canonical URIs during discovery and count only unseen candidates.

### P3 - Final publication report is stale and overstates report completeness

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-portfolio-maker-0.1.0-final-report.md:128`.
- The report says the branch is still to be published although `origin/main` already equals `473f5c0`, and says the repository contains complete reports through Loop 10 although early loops have only the available re-review documents.
- Minimal fix: record the actual published ref and replace `complete` with `available`.

## Ponytail Cleanup

- The historical implementation plan contains large copied source/test blocks and can eventually be reduced to decisions, commands, results, and file/commit references.
- `ProgressEvent`, request wrappers, `run-mvp`, `SourceStatus.APPROVED`, and thin artifact wrappers have few current consumers. They are low-risk cleanup candidates, not part of this correctness fixback.
- The artifact wrapper should not be deleted during this loop because the P1 fix needs a shared managed-file writer.
- The verified `text-v1` migration path is retained: it protects existing workspaces and has an established privacy/integrity contract. Removing it is not accepted as a 0.1.0 cleanup.

## Next Minimal Checks

- Add focused failing regressions before implementation for every P1/P2 finding and the accepted P3 alias/report findings.
- Keep the fix scoped to shared managed writes, controlled error mapping, resilient discovery, label normalization, draft invalidation, and stable GitHub activity identity.
- Re-run the focused regressions, the full suite, `git diff --check`, and `git show --check --format=short HEAD`.
