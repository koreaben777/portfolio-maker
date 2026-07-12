# Team Based Review Loop 17 - Re-Review Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `cdde6eaf4d6dc94e3a43bb34180abef374dbbe8b`
Status: NEEDS WORK

## Evidence Checked

- Initial Loop 17 report and Phase 1 spec.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `212 passed in 7.51s`.
- Focused approval/CLI/GitHub/discovery/profile/SQLite suite -> `161 passed in 7.28s`.
- `git diff --check 1cd6d29..cdde6ea` and `git show --check --format=short cdde6ea` -> pass.
- Independent four lanes: `@ponytail`, codebase-onboarding logical flow, technical-writer contract, and reality-checker.
- Direct temporary-workspace reproduction found the normal discovery review-comment URL `https://github.com/octo/demo/pull/1#discussion_r1` cannot be added to `approved_github_activity_urls`.

## Closed Findings

- Successful GitHub re-discovery marks missing activities artifact-ineligible; failed whole GitHub discovery preserves existing visibility.
- Approved pull/commit/issue/workflow activity is rendered as a distinct draft evidence reference instead of a project skeleton.
- `portfolio_draft` now records claim/evidence provenance.
- Claims are connected to a concrete `projects` row.
- Approved activity state, canonical repository identity, title/author masking, invalid URL traceback handling, and filename-pattern controls have regression coverage.
- README and the repo-scoped skill now describe the explicit GitHub approval boundary.

## Still Open / Newly Found Findings

### P2 - Review-comment candidates can never be explicitly approved

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:119` discovers `review_comment` candidates from the GitHub API. Their fixture URL is `https://github.com/octo/demo/pull/1#discussion_r1` (`tests/fixtures/github/gh_review_list.json:3`). `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:331` rejects every fragment and only maps `pull` URLs to `pull_request`, so `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:118` rejects a URL that discovery itself reports.

Minimal fix: accept only the known-safe public review-comment fragment shape emitted by GitHub (for example `#discussion_r<digits>`), map it to `review_comment`, preserve rejection of arbitrary fragments/query/credentials, and cover discovery -> approval -> profile -> draft evidence rendering.

### P2 - Approval URL validation permits raw control characters

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:331` delegates directly to `urlparse()`, which normalizes leading newline/tab characters. As a result `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/approval.py:118` accepts a control-prefixed URL, and the original control-bearing string can become a profile evidence URI.

Minimal fix: reject leading/trailing whitespace and every Unicode `C*` character before parsing. Add approval and artifact regressions for newline, tab, and zero-width controls.

### P2 - Empty workflow status can invalidate prior activity visibility then abort discovery

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:150` uses `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:305`, which accepts an empty string as a workflow state. `discover_sources()` invalidates activity visibility before inserting results, then `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:495` rejects the activity and the call aborts. Previously visible activity therefore becomes ineligible even though the discovery did not complete.

Minimal fix: require nonblank workflow `conclusion` or `status` in the parser before mutation. Test that malformed workflow payload is recorded as an endpoint failure while existing activity visibility remains unchanged.

## Ponytail Cleanup

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/draft_portfolio.py:59`: remove defensive `isinstance` fallbacks around the direct `build_profile()` return contract and the unused `BuildProfileResult` import.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:119`: `public_github_activity_type()` already proves public URL validity; remove the immediately preceding redundant `is_public_github_activity_url()` check.

## Next Minimal Checks

1. Add focused red regressions for safe review-comment fragment acceptance, raw URL controls, and blank workflow state without visibility mutation.
2. Preserve query/unknown-fragment rejection, fail-open whole GitHub failure behavior, evidence-only rendering, and provenance manifests.
3. Run focused tests, full `pytest`, `git diff --check`, and the direct review-comment end-to-end reproduction.
