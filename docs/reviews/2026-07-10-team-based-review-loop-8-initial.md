# Team Based Review Loop 8 - Initial Findings

Date: 2026-07-10

Target thread: `MVP Developer` (`019f4544-b93d-7760-9536-d08a6e9bf37b`)

Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`

Branch / HEAD: `codex/portfolio-maker-mvp` / `8466c8fbcde8b3f30151e9fb942a7715f9623ff3`

Implementation baseline: `f9a452717647227c1e0f61b471b51c99b5163aad`

Status: NEEDS WORK

## Evidence Checked

- Source report: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/reviews/2026-07-10-team-based-review-loop-7-rereview.md`
- Same reviewer team: Parfit (`@ponytail`), Schrodinger (`codebase-onboarding`), Raman (`technical-writer`), Arendt (`reality-checker`).
- Full suite at the implementation baseline: `86 passed`.
- `git show --check --format=short f9a4527` and `git diff --check 8b3e6f2..f9a4527` passed.
- Post-validation path replacement, legacy/tampered snapshot, damaged snapshot recovery, malformed stale profile, relative CLI forbidden path, case-variant GitHub exclusion, empty evidence, endpoint schema omissions, and invalid tilde path were directly reproduced.

## Findings

### P1 - Approval path validation/read TOCTOU

`approved_regular_file_path()` validates a path before a separate read. Replacing the path after validation can still ingest unapproved content. Combine open, `O_NOFOLLOW`, `fstat`, size/regular-file validation, and read on one file descriptor. Apply the same primitive to ingestion and profile freshness checks.

### P1 - Legacy or tampered snapshot integrity and masking

`text-v1` snapshots are accepted without comparing snapshot `text` and extractor version against the current extraction result. Version the strengthened masking contract, reject or re-ingest legacy snapshots, and detect metadata-preserving text tampering.

### P1 - Sensitive filename and password-manager export gaps

Known password-manager export variants can still be discovered, and secret-shaped filenames can reach public draft headings/references. Add narrow case-insensitive export rules, reject secret-shaped filenames at discovery/ingestion, and mask display values again at the public artifact boundary.

### P1 - GitHub privacy metadata fails open

Missing or non-bool `isPrivate` defaults to public. Require the field and bool type; malformed repository payloads must become a GitHub discovery failure status.

### P2 - Damaged snapshot recovery

An existing content-addressed snapshot is returned without validating its JSON/schema/content. Re-ingest can report success while leaving the file corrupt. Validate existing snapshots and atomically rewrite invalid files before DB/status updates.

### P2 - Stale malformed profile blocks draft recovery

`draft_portfolio()` validates the old profile before immediately rebuilding it. Remove the pre-validation and validate only the newly generated profile.

### P2 - Path normalization and clean CLI errors

- Relative CLI `--forbidden-path` must resolve against workspace, matching approval JSON behavior.
- Invalid `~user` expansion must become `ApprovalFormatError`, not traceback.

### P2 - Snapshot idempotency and empty evidence

- Same-content recovery after `STALE_SOURCE` must restore status without inserting a duplicate snapshot row/path.
- Empty snapshot text must not generate the synthetic claim `Approved evidence captured.`.

### P2 - GitHub canonicalization and endpoint schemas

- Compare `excluded_repositories` with casefolded canonical `owner/repo` values.
- Require stable fields for review/workflow payloads; `{}` and empty items must not create silent empty or fabricated activities.

### P2 - Product and workflow contract drift

- Current output is a portfolio skeleton with placeholders, not an evidence-rendered portfolio. Implement evidence rendering or explicitly define and defer the richer contract.
- Repository exclusions cannot affect the documented first discovery unless approval is created/edited first or a mandatory re-discovery step is documented.

## Ponytail Cleanup

Do not combine broad cleanup with the P1/P2 repair. After correctness is closed, reviewers will separately decide whether the historical plan, artifact writer wrapper, duplicate GitHub DTO, or reserved schema fields still require deletion or documentation. `SourceApproval.version` may be validated without being retained in the runtime model if no consumer needs it.

## Next Minimal Checks

1. Add focused failing checks for every finding before fixes.
2. Use one shared file-descriptor read primitive for TOCTOU-sensitive paths.
3. Verify legacy/tampered/damaged snapshot migration and recovery.
4. Verify public filenames, GitHub privacy/schema, path normalization, and clean CLI exits.
5. Verify same-content idempotency, empty evidence, draft recovery, and workflow docs.
6. Run focused regressions, full suite, findings gate, `git diff --check`, and `git show --check`.

## Initial Outcome

NEEDS WORK. Continue with one `@codex-fable5` fixback and re-review with the same four reviewers.
