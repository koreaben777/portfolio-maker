# Team Based Review Loop 9 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `860414c47707ab080e408c9f96aa598d11234251`

Status: NEEDS WORK

## Source

This fixback is derived from `docs/reviews/2026-07-10-team-based-review-loop-8-rereview.md`. The same four reviewers independently reproduced the remaining boundaries after Loop 8 passed 100 tests.

## Required Fixes

### P1

1. Replace leaf-only `O_NOFOLLOW` with a component-by-component descriptor walk so parent-directory replacement cannot redirect an approved URI.
2. Fully migrate real `text-v1`/`source-{id}.json` snapshot state: update the same logical DB row, commit, and remove only the managed legacy file.
3. Reject timestamped/suffixed Bitwarden, LastPass, 1Password, and other explicitly supported password-manager export JSON/CSV names with narrow case-insensitive patterns.

### P2

1. Prevent FIFO replacement hangs with defensive `lstat`, nonblocking no-follow open, and regular-file `fstat` validation.
2. Make `approve --write-sample` non-destructive by default; add explicit `--force` reset behavior and aligned docs.
3. Repair stale snapshot extractor metadata before the idempotent skip path and use one path/hash/extractor metadata contract.
4. Reject repository exclusions that are not canonical `owner/repo` values and document the required form.
5. Align the bilingual specs and MVP plan with the actual 0.1.0 portfolio skeleton; defer rich evidence-rendered fields explicitly.

### P3

1. Remove unused retained approval version state and duplicate forbidden-path normalization while preserving input validation.
2. Delete the brittle English-substring workflow documentation test; retain behavioral CLI coverage.

## Required Verification

- Add a focused failing regression before each P1/P2 fix, using synthetic placeholders only.
- Run the focused security/state/CLI/documentation suite and the full suite.
- Run the Fable findings gate and a fresh adversarial self-review.
- Run `git diff --check` and `git show --check --format=short HEAD`.
- Commit only implementation, test, and product-document changes. Do not commit review reports or other user files.
- Do not add a remote or push.

## Initial Outcome

NEEDS WORK. Apply one minimal `@codex-fable5` fixback, report completion with `[TEAM_REVIEW_FIX_DONE_9]`, and return the result for re-review by the same four-agent team.
