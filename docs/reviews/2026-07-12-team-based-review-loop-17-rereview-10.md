# Team Based Review Loop 17 - Re-Review 10 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `d3dcf60cf56c8e744e2263aae5481ea94f92090d`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 9 report, `627d1c2..d3dcf60`, current Phase 1 policy, repository lifecycle, CLI, and repo skill.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `260 passed in 8.15s`.
- Focused GitHub/discovery/profile/draft/SQLite suite -> `191 passed in 2.45s`.
- `git diff --check 627d1c2..d3dcf60` and `git show --check --format=short d3dcf60` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes.

## Closed Findings

- GitHub evidence and linked claims reconcile `public_safe` on rebuild, activity title refresh updates one stable claim, and local source evidence stays outside that reconciliation.
- Normalized title and shared URL trust parser boundaries have focused regressions.
- `excluded_file_patterns` is now described for pre-discovery policy editing.
- `@ponytail` found no over-implementation in this diff.

## Still Open / Newly Found Findings

### P2 - Repo skill omits persistent forbidden-path policy before discovery

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:36` omits `forbidden_paths` from the pre-discovery approval fields. A user following that workflow can pass a new folder only through transient `--forbidden-path`; `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:29` later revalidates only persisted `source-approval.json.forbidden_paths`. An already ingested source under the new forbidden folder can therefore remain eligible on profile rebuild.

Minimal fix: add `forbidden_paths` to the pre-discovery approval fields, state it must be persisted in `source-approval.json` for ingest/profile/draft revalidation, and document `--forbidden-path` as discovery-only.

### P3 - Revoked GitHub project remains public-safe

After an approved public activity creates project `github:octo/demo`, changing the activity private and rebuilding clears evidence and claim safety but leaves `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:689` project row `public_safe=1`.

Minimal fix: reconcile GitHub-backed project safety with activity eligibility alongside evidence/claims. Current eligible activity should reactivate its project; local projects must remain unaffected. Add regression coverage.

### P3 - Whitespace-only commit message creates malformed activity title

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:152` accepts a commit message containing only whitespace, creating a title that normalizes empty. Reject a blank normalized first commit subject as `GitHubDiscoveryError` and add parser coverage.

## Ponytail Cleanup

No new cleanup beyond the P3 lifecycle/title boundary fixes above.

## Next Minimal Checks

1. Persist forbidden-path policy guidance before discovery and distinguish transient CLI filtering from lifecycle policy.
2. Reconcile GitHub project `public_safe` with evidence/claim eligibility while keeping local projects intact.
3. Reject whitespace-only commit subjects.
4. Run focused/full tests, `git diff --check`, and direct forbidden-path/project/title reproductions.
