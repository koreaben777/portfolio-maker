# Team Based Review Loop 2 - Re-Review Findings

Date: 2026-07-10
Target thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Target worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch / HEAD: `codex/portfolio-maker-mvp` / `a504145 fix: address team review loop 2 findings`
Status: NEEDS WORK

## Evidence Checked

- Main verification: `./.venv/bin/python -m pytest -q` -> `62 passed`.
- Diff hygiene: `git diff --check` -> passed.
- Manual clean-workspace CLI flow: `discover --no-github -> approve --write-sample -> ingest -> build-profile -> draft-portfolio` completed in a temporary workspace.
- Manual secret scan across generated artifacts and local snapshots from that temporary flow found no `fake-secret` or raw `OPENAI_API_KEY=fake...` value.
- Reviewer lanes rerun with the same team:
  - `@ponytail`: over-implementation review.
  - `agency-router` / `codebase-onboarding`: logical flow review.
  - `agency-router` / `technical-writer`: docs and plan drift review.
  - `agency-router` / `reality-checker`: bug and validation review.

## Closed Findings

- Approval forbidden paths are applied during discovery reruns.
- Sensitive file names such as `.env` are skipped by ingestion even if manually approved.
- Prefixed single-token secret keys such as `OPENAI_API_KEY`, `GITHUB_TOKEN`, and `AWS_SECRET_ACCESS_KEY` are masked by covered tests.
- Private and explicitly excluded GitHub repositories are filtered before per-repo activity calls.
- Per-repo GitHub activity failures preserve successfully discovered repositories and unrelated activity results.
- GitHub is documented and tested as discovery-only for the current MVP profile/portfolio artifacts.
- Stale handoff wording and excluded-directory case sensitivity were corrected.

## Findings

### P1 - Unquoted multi-word secret values can be partially left in snapshots

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/policy.py:46`
- Evidence: `mask_secrets("password: my secret value")` redacts only the first token after the key and leaves trailing secret words. The same boundary can affect `OPENAI_API_KEY=my secret value`.
- Risk: extracted snapshots can retain part of a detected secret value, conflicting with the architecture requirement to mask detected secrets in snapshots.
- Smallest fix: add failing policy/extraction tests for unquoted multi-word key-value secrets and redact bare key-value values through a safe line or delimiter boundary.

### P2 - Already ingested sources are skipped before stale-source verification

- Location: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/ingestion.py:47`
- Evidence: if a file is ingested once and then deleted, a second `ingest` skips the `INGESTED` source before checking file existence or content hash, leaving status as `ingested`.
- Risk: source status can falsely claim current evidence exists after the file is missing or changed.
- Smallest fix: for approved already-ingested local sources, verify existence and current content hash against the latest snapshot before skipping. Mark missing sources `stale_source`; either re-ingest changed sources or explicitly document and test the chosen policy.

### P2 - Architecture specs still claim GitHub activity ingestion into MVP artifacts

- Location:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:75`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design-ko.md:75`
- Evidence: specs still say approved GitHub activity is ingested, while implementation skips non-`LOCAL_FILE` sources in ingestion and README/skill/plan now define GitHub as discovery-only.
- Related spec drift: GitHub policy and integration-test lines still imply private repo ingestion or fixture GitHub API response ingestion for the current MVP.
- Smallest fix: align both English and Korean architecture specs with the implemented MVP contract: approved local files feed snapshots/profile/portfolio; GitHub repositories and activities are stored as discovery metadata only until a later company-specific generation phase.

### P3 - Architecture spec status is stale

- Location:
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design.md:4`
  - `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/docs/superpowers/specs/2026-07-09-portfolio-maker-architecture-design-ko.md:4`
- Evidence: both specs still say written spec pending user review, but the MVP implementation is now on `codex/portfolio-maker-mvp` and has passed multiple review/fixback rounds.
- Smallest fix: mark the specs as approved historical architecture docs with a short note that the implemented `0.1.0` MVP narrows GitHub to discovery-only.

## Ponytail Cleanup

The ponytail lane still has improvement requests:

- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/domain/models.py:9`: remove future-only enum values and unused domain classes until they have a runtime reader/writer.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/sqlite_repository.py:32`: remove future-only normalized tables (`evidence_items`, `projects`, `career_claims`, `claim_evidence`, `artifacts`) until company-specific generation uses them.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/application/models.py:78`: delete unused `DiscoveryReport` dataclass and unused `field` import.
- `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp/src/portfolio_maker/infrastructure/github_connector.py:18`: stop fetching/parsing unused `description` and `primary_language` fields.

Estimated shrink opportunity: about 144 lines.

## Next Minimal Checks

- Add focused regression tests for multi-word bare secret masking.
- Add focused regression tests for re-ingesting already-ingested missing and changed files.
- Re-run `./.venv/bin/python -m pytest -q`.
- Re-run `git diff --check`.
- Re-run the same four review lanes after the fixback.
