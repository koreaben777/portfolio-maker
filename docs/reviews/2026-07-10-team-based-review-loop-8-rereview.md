# Team Based Review Loop 8 - Re-Review Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `860414c47707ab080e408c9f96aa598d11234251`

Baseline: `8466c8fbcde8b3f30151e9fb942a7715f9623ff3`

Status: NEEDS WORK

## Evidence Checked

- MVP Developer implementation commit: `860414c fix: close team review loop 8 findings`.
- Same reviewer team reused: Parfit (`@ponytail`), Schrodinger (`codebase-onboarding`), Raman (`technical-writer`), Arendt (`reality-checker`).
- Focused regression set -> `76 passed`.
- `PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider` -> `100 passed`.
- `git diff --check 8466c8f..860414c` and `git show --check --format=short 860414c` -> pass.
- Independent adversarial checks covered parent-directory replacement, FIFO replacement, legacy snapshot migration, DB metadata drift, timestamped password-manager exports, approval sample overwrite, repository exclusion syntax, and the documented portfolio contract.

## Closed Findings

- Leaf-file replacement is read through one `O_NOFOLLOW` file descriptor with `fstat` and size checks.
- Current snapshot text/extractor integrity, damaged content-addressed snapshot repair, and stale malformed profile recovery are covered.
- Relative forbidden paths and invalid `~user` paths have clean normalization/error handling.
- Same-content recovery no longer creates a new snapshot row in the covered current-format path, and empty evidence creates no claim.
- GitHub privacy metadata, endpoint schemas, and case-insensitive exclusions are validated in the covered canonical path.
- Public display values receive additional masking, and the current output is described as a portfolio skeleton in README and skill documentation.

## Still Open

### P1 - Parent-directory replacement still bypasses the approved path

File: `src/portfolio_maker/infrastructure/extractors.py`

`O_NOFOLLOW` protects only the final component. Three reviewers independently replaced an approved file's parent directory with a symlink after canonical validation but before `os.open()`, and the reader returned unapproved content.

Minimum fix: walk every path component from a trusted directory descriptor using `dir_fd`/`openat` semantics plus `O_NOFOLLOW`, then `fstat` and read the final descriptor. Add a focused parent-symlink race regression.

### P1 - Legacy unmasked snapshot state is not fully migrated

Files: `src/portfolio_maker/application/ingestion.py`, `src/portfolio_maker/infrastructure/sqlite_repository.py`

Re-ingesting an actual `source-{id}.json` `text-v1` snapshot creates a safe v2 snapshot but leaves the old unmasked file and legacy DB row. This retains sensitive material and ambiguous latest-state metadata.

Minimum fix: migrate the same-source/same-content row to the v2 path/extractor, commit DB state, then delete only the managed legacy file. Cover the real legacy filename and DB row with a synthetic credential fixture.

### P1 - Timestamped password-manager exports remain discoverable

Files: `src/portfolio_maker/infrastructure/policy.py`, `src/portfolio_maker/infrastructure/local_discovery.py`

`bitwarden_export_20260710.json` and similar supported JSON/CSV names remain candidates, can be ingested, and can reach a public draft reference.

Minimum fix: add narrow case-insensitive patterns for known password-manager vendor/export prefixes with optional date/timestamp suffixes. Verify discovery, ingestion, and public artifact boundaries.

### P2 - FIFO replacement can block ingestion indefinitely

File: `src/portfolio_maker/infrastructure/extractors.py`

Replacing an approved regular file with a FIFO before open blocks because regular-file validation occurs after a blocking `os.open()`.

Minimum fix: retain a defensive `lstat`, open with `O_NONBLOCK | O_NOFOLLOW`, and accept only a regular-file `fstat`. Add a subprocess timeout regression.

### P2 - `approve --write-sample` destroys existing approval decisions

Files: `src/portfolio_maker/application/approval.py`, `src/portfolio_maker/adapters/cli.py`, `README.md`, `.agents/skills/portfolio-maker/SKILL.md`

Running the documented sample command on an existing workspace exits successfully and replaces approved URIs, forbidden paths, and repository exclusions with empty defaults.

Minimum fix: refuse overwrite with a clean non-zero error by default; require an explicit `--force` for intentional reset. Document that normal setup writes only when the approval file is absent.

### P2 - Snapshot DB metadata drift survives the idempotent skip path

Files: `src/portfolio_maker/application/ingestion.py`, `src/portfolio_maker/infrastructure/sqlite_repository.py`

When a valid v2 snapshot file is paired with a DB row whose extractor still says `text-v1`, ingestion skips and leaves the stale DB metadata unchanged.

Minimum fix: compare path, hash, and extractor metadata before skipping; repair the latest row when only DB metadata is stale. Make profile validation consume the same metadata contract.

### P2 - Invalid short repository exclusions fail silently

Files: `src/portfolio_maker/application/approval.py`, `src/portfolio_maker/infrastructure/github_connector.py`, `README.md`

An exclusion such as `demo` does not match `octo/demo`, but the approval loader and documentation do not reject or explain the invalid form.

Minimum fix: require canonical `owner/repo` syntax at approval load time and document it in README/sample. Do not add ambiguous short-name matching.

### P2 - Portfolio skeleton declaration still conflicts with specs and MVP plan

Files: both architecture specifications and `docs/superpowers/plans/2026-07-09-portfolio-maker-mvp.md`

README and the skill call the output a skeleton, but the bilingual specs still require problem/context, implementation details, technology stack, and evidence-backed outcomes as current output. The MVP plan also states an evidence-based portfolio goal.

Minimum fix: list only current skeleton fields as the 0.1.0 contract and move rich evidence-rendered fields and the corresponding plan goal to an explicit deferred section.

### P3 - Approval model retains unused state and repeats normalization

File: `src/portfolio_maker/application/approval.py`

`version` is validated then stored but never consumed. Forbidden paths are normalized for validation and normalized again when used.

Minimum fix: retain version validation without runtime storage, and normalize forbidden paths exactly once.

### P3 - Workflow documentation test pins prose rather than behavior

File: `tests/test_workflow_docs.py`

The test checks two English substrings but does not verify command order or CLI behavior, while blocking harmless wording changes.

Minimum fix: delete the brittle prose test. Keep behavioral CLI tests as the executable contract.

## Re-Review Outcome

NEEDS WORK. Loop 8 closes the previously reproduced leaf-level, snapshot-integrity, GitHub-schema, recovery, and normalization gaps, but parent-component path replacement, retained legacy sensitive state, timestamped password exports, and five user-facing state/contract defects remain reproducible. Continue with Loop 9 using the same four reviewers and the same review method. No remote was added and nothing was pushed.
