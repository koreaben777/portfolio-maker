# Team Based Review Loop 17 - Re-Review 9 Findings

Date: 2026-07-12
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `627d1c2f53cdffa61512c0caf3a6493ac88e6419`
Status: NEEDS WORK

## Evidence Checked

- Re-Review 8 report, `d5f7b8a..627d1c2`, current Phase 1 policy, README, repo skill, SQLite schema/repository, and artifact lifecycle.
- Full suite: `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `256 passed in 7.80s`.
- Focused GitHub/discovery/profile/draft suite -> `137 passed in 1.17s`.
- `git diff --check d5f7b8a..627d1c2` and `git show --check --format=short 627d1c2` -> pass.
- Fresh independent `@ponytail`, codebase-onboarding, technical-writer, and reality-checker lanes.

## Closed Findings

- Raw repository cap is assessed before canonical deduplication; capped public/private duplicate observations resolve private and prevent public artifact reuse.
- Capped lists preserve unobserved repository evidence while invalidating activity for repositories explicitly observed private.
- Incomplete discovery is separately rendered from endpoint failure.
- The logical-flow lane found no additional state-transition P1/P2/P3.

## Still Open / Newly Found Findings

### P2 - Revoked or private GitHub activity leaves durable public-safe evidence and claims

After a build creates GitHub-backed `evidence_items` and `career_claims`, a capped re-discovery can mark its repository private at `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/discovery.py:65`. The next profile excludes the activity, but the earlier rows remain `public_safe=1`. Direct reproduction built once, changed `octo/demo` to private, rebuilt, and found both durable rows still public-safe.

Minimal fix: reconcile activity-backed evidence/claim public-safe status against the current build eligibility gate. On activity private/revoked/excluded/unapproved/invalid, clear or retire public-safe evidence and claims; preserve local-source behavior. Add profile rebuild regression.

### P2 - Activity metadata update creates duplicate public claims

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:152` creates claims from current title text, and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:671` has no stable activity-backed claim identity. Rebuilding after same `(repo, type, URL)` changes from `Title v1` to `Title v2` leaves two public-safe claims linked to the same updated evidence item.

Minimal fix: use a stable activity-backed claim identity and update it in place, or explicitly retire the superseded claim/version. Add a repeated-build metadata-update regression.

### P3 - Empty activity titles create malformed public claims

Live parsers accept empty PR/issue/review/workflow titles and `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/build_profile.py:149` emits a claim such as `octo/demo: `. Legacy rows are also accepted.

Minimal fix: require a nonempty normalized title at parser and profile eligibility boundaries, with discovered and legacy regression coverage.

### P3 - GitHub URL trust checks repeat common parsing logic

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:468` duplicates common public-GitHub URL parsing in activity identity handling. Extract one private shared URL-trust parser while keeping root repository and activity-specific path rules in their callers.

### P3 - Repo skill omits pre-discovery filename exclusion policy

`/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/.agents/skills/portfolio-maker/SKILL.md:36` says to edit only GitHub policy fields before discovery, omitting `excluded_file_patterns`. This contradicts README and Phase 1 policy; because CLI has no equivalent flag, users cannot apply case-insensitive filename glob exclusions before local candidate discovery.

Minimal fix: include `excluded_file_patterns` in pre-discovery approval fields and state its case-insensitive filename-glob semantics.

## Ponytail Cleanup

- The shared private URL-trust parser is the only code cleanup. It must not weaken current repository/activity identity checks.

## Next Minimal Checks

1. Reconcile durable GitHub evidence/claim safety with current eligibility and stabilize activity-backed claims across metadata updates.
2. Reject empty titles at parser and legacy-artifact boundaries.
3. Deduplicate URL trust parsing and complete the pre-discovery skill policy instruction.
4. Run focused/full tests, `git diff --check`, and direct revocation/update/empty-title reproductions.
