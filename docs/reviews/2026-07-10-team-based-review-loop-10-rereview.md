# Team Based Review Loop 10 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `30d3cf3bf73bd3f85e7da12c430273a0eb412e09`

Baseline: `b1ff2899a548f8ac51200e129df8ab01c0d28a2a`

Status: PASS

## Evidence Checked

- Same four review lanes were retained:
  - Goodall: `@ponytail` over-implementation and deletion review.
  - Beauvoir: replacement `agency-router` / `codebase-onboarding` logical-flow review after Lovelace did not return a result.
  - Chandrasekhar: `agency-router` / `technical-writer` contract review.
  - Darwin: `agency-router` / `reality-checker` bug, privacy, CLI, idempotency, and TOCTOU review.
- Implementation commits reviewed:
  - `cdd0eaef539709f4fd962e70d82bc53c8309b19c` closed the initial Loop 10 findings from `docs/reviews/2026-07-10-team-based-review-loop-10-initial.md`.
  - `30d3cf3bf73bd3f85e7da12c430273a0eb412e09` closed the additional re-review findings found against `cdd0eae`.
- Local focused verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_approval.py tests/test_policy.py tests/test_ingestion.py tests/test_profile_and_portfolio.py tests/test_github_connector.py
90 passed
```

- Local full verification:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
125 passed
```

- Hygiene:

```text
git diff --check
passed

git show --check --format=short HEAD
passed
```

## Closed Findings

- P1 legacy cleanup is now idempotent before ingestion early return and covers stale `text-v1` rows even when current source content changed.
- P1 legacy migration now reads the legacy file, writes the v2 snapshot, verifies the managed snapshot directory inode, and unlinks the legacy file under one validated `dir_fd` lifetime.
- P1 legacy migration rejects symlink and ordinary directory replacement before DB state is updated; tests cover both external symlink write and same-path replacement directory cases.
- P1 Chrome and Firefox timestamped browser password exports such as `chrome_passwords_20260710.csv` and `firefox_logins_20260710.json` are rejected by policy and kept out of public artifacts.
- P2 non-force approval sample creation uses exclusive creation and preserves concurrently created files; `--force` remains the explicit overwrite path.
- P2 repository exclusions and GitHub repository payloads now share canonical `owner/repo` validation and reject dot components plus invalid leading forms.
- P2 malformed GitHub `nameWithOwner` payloads are converted to `GitHubDiscoveryError` and remain on the CLI's controlled error path.
- P3 bilingual architecture specs now describe 0.1.0 as a master-profile plus review-required portfolio skeleton release.
- P3 superseded legacy snapshot helper APIs used only by the old split migration path were removed after the unified migration helper landed.

## Findings

None. All four reviewer lanes returned PASS with no remaining improvement request.

## Ponytail Cleanup

- Goodall initially flagged that the first Loop 10 fixback still reopened the managed snapshot path between legacy read and migrated v2 write.
- `30d3cf3` removed that split flow and kept the descriptor-relative write/read machinery because it directly protects the MVP privacy and integrity boundary.
- The final Ponytail re-review found no release-blocking over-implementation or deletion request.

## Next Minimal Checks

- Treat `30d3cf3bf73bd3f85e7da12c430273a0eb412e09` as the reviewed 0.1.0 implementation state.
- Keep GitHub activity as discovery-only for 0.1.0; do not imply profile/portfolio artifact use.
- Before any future release, rerun the full suite and repeat the descriptor replacement regressions if snapshot code changes.

## Re-Review Outcome

PASS. Version `0.1.0` has no remaining reviewer improvement request from the maintained four-lane review team.
