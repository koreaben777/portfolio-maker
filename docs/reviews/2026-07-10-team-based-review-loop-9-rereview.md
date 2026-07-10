# Team Based Review Loop 9 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `b1ff2899a548f8ac51200e129df8ab01c0d28a2a`

Baseline: `860414c47707ab080e408c9f96aa598d11234251`

Status: NEEDS WORK

## Evidence Checked

- Same reviewer team: Parfit (`@ponytail`), Schrodinger (`codebase-onboarding`), Raman (`technical-writer`), Arendt (`reality-checker`).
- Focused regression set -> `86 passed`.
- Full suite -> `110 passed`.
- `git diff --check 860414c..b1ff289` and `git show --check --format=short b1ff289` -> pass.
- Independent checks covered component descriptor walking, FIFO replacement, timestamped exports, approval creation, legacy cleanup interruption/retry, changed-source legacy state, managed-directory replacement, repository identifiers, and the 0.1.0 document hierarchy.

## Closed Findings

- Approved files are opened by component from a trusted directory descriptor and read from a nonblocking final regular-file descriptor.
- Parent-component redirection and FIFO blocking reproductions are closed.
- Normal same-content legacy migration updates the row and removes the managed legacy file.
- Snapshot file/path/hash/extractor metadata are compared through one runtime contract.
- Default approval sample creation preserves an already-existing file in the normal sequential case; `--force` performs the explicit reset.
- Timestamped vendor export names covered by the new pattern are rejected across discovery, ingestion, and public display.
- Basic `owner/repo` validation and detailed skeleton/deferred contracts are present.
- Unused approval version state, duplicate forbidden-path normalization, and the prose-pinning workflow test were removed.

## Still Open

### P1 - Legacy cleanup is not durable, complete, or descriptor-relative

File: `src/portfolio_maker/application/ingestion.py`

Independent reproductions found three related state failures:

- If legacy `unlink()` fails after the DB row is updated to v2, the next run takes the valid-v2 early return and never retries cleanup.
- When current source content differs from the latest legacy row, a new v2 row is added while the old unmasked file and DB row remain.
- Replacing the managed snapshot directory before path-based `unlink()` can delete an external same-name file and invalidate the new snapshot path.

Minimum fix: add an idempotent legacy-cleanup phase that runs before every early return, tracks every legacy row independently of current source hash, and uses a validated managed-directory descriptor plus relative unlink with inode/type checks. Make interruption and retry deterministic; do not rely only on reordering DB update and unlink.

### P1 - Timestamped browser password exports remain candidates

File: `src/portfolio_maker/infrastructure/policy.py`

`chrome_passwords_20260710.csv` and `firefox_logins_20260710.json` remain candidates even though their unsuffixed forms are recognized. The same policy feeds discovery and public display.

Minimum fix: apply the narrow optional date/time suffix rule to the existing Chrome password and Firefox login stems. Add discovery, ingestion, and public artifact regressions.

### P2 - Non-force approval sample creation is not atomic

File: `src/portfolio_maker/application/approval.py`

The current `exists()` then `write_text()` sequence can overwrite a file created between the check and write even when `force=False`.

Minimum fix: use exclusive creation (`x`/`O_EXCL`) for non-force mode and map `FileExistsError` to the existing clean error. Keep explicit overwrite only in force mode.

### P2 - Repository exclusion parser accepts noncanonical identifiers

File: `src/portfolio_maker/application/approval.py`

The current regex accepts dot components and invalid leading forms such as `../repo`, `owner/..`, `_owner/repo`, and `-owner/repo`. These values cannot reliably match GitHub `nameWithOwner` values and silently defeat exclusion intent.

Minimum fix: implement one canonical `owner/repo` parser shared by approval validation and comparison. Reject `.`/`..` components and invalid leading characters; add focused invalid-form tests.

### P3 - Architecture spec priority still overstates the 0.1.0 output

Files: both bilingual architecture specifications, line 19

The detailed sections now define a review-required skeleton, but the top priority still says the current output is an evidence-based portfolio draft.

Minimum fix: state that 0.1.0 generates an evidence-based master profile and a review-required portfolio skeleton, while evidence-rendered portfolio output is deferred.

### P3 - Superseded metadata APIs and pass-through wrapper remain

Files: `src/portfolio_maker/infrastructure/sqlite_repository.py`, `src/portfolio_maker/application/approval.py`

Production now uses unified snapshot metadata, but older path/hash lookup APIs remain for tests. `approval_forbidden_paths()` also accepts an unused paths argument and returns the stored field unchanged.

Minimum fix: update tests/callers to unified metadata and direct `approval.forbidden_paths`, then delete the obsolete APIs and wrapper.

## Re-Review Outcome

NEEDS WORK. The descriptor walk, FIFO defense, normal migration, CLI force contract, metadata checks, and detailed document contract are materially improved, but interruption-safe legacy cleanup and two fail-closed input policies remain incomplete. Continue with Loop 10 using the same four reviewers and method. No remote was added and nothing was pushed.
