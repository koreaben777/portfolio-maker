# Portfolio Maker 0.1.0 Final Review Report

Date: 2026-07-10
Version: `0.1.0`
Final review status: NEEDS WORK
Reviewer improvement requests remaining: 15 P1/P2 items plus P3 cleanup
Implementation thread: MVP Developer (`019f4544-b93d-7760-9536-d08a6e9bf37b`)
Implementation worktree: `/Users/june_kim/Documents/portfolio-maker/.worktrees/portfolio-maker-mvp`
Branch: `codex/portfolio-maker-mvp`
Reviewed implementation HEAD: `f9a452717647227c1e0f61b471b51c99b5163aad`

## Executive Summary

The earlier PASS at `e7d2957` was superseded by an additional model-change review at the user's request. Four newly created independent reviewers found reproducible approval, privacy, evidence-integrity, recovery, and documentation gaps in `8b3e6f2`.

The MVP Developer completed one `@codex-fable5` fixback at `f9a4527`, increasing the suite from 70 to 86 passing tests and closing most initial Loop 7 findings. The same new reviewer team then re-reviewed the fix. That re-review remains NEEDS WORK because P1 TOCTOU and legacy-snapshot integrity gaps plus multiple P2 recovery/schema issues remain reproducible. The package version remains `0.1.0`.

## 0.1.0 Product Boundary

The implemented product is a dependency-light Python CLI and Codex skill for building a local, approval-gated career knowledge base and portfolio draft.

Implemented in 0.1.0:

- Local file discovery with default sensitive-path and forbidden-path policy checks.
- GitHub repository and activity discovery through `gh`, with repository exclusion and private-source policy enforcement before activity collection.
- Explicit source approval through `.portfolio-maker/reviews/source-approval.json`.
- Approved local-file ingestion with text extraction, secret masking, and raw snapshot persistence.
- SQLite-backed source, snapshot, and GitHub activity persistence with repeat-run protections.
- Evidence-derived `master-profile.json` generation from currently approved local sources.
- Markdown portfolio draft generation from the approved profile.
- User-facing CLI failures with non-zero exit codes and no expected-error traceback.
- English and Korean architecture specifications aligned with the runtime approval schema.

Explicitly deferred from 0.1.0:

- GitHub activity as profile or portfolio artifact input; GitHub is discovery-only.
- Repository allowlists and excluded file-pattern approval fields.
- Company-specific portfolio generation.
- Expanded normalized career/evidence schema beyond the current runtime tables.
- Automatic retention or deletion of `.portfolio-maker/` snapshots and database history.

## Review Team And Method

Loops 1 through 6 used the original four-role team. Loop 7 preserved the same roles but used four newly created reviewers for model-change objectivity:

- `@ponytail`: over-implementation, deletion candidates, speculative abstraction, and dependency review.
- `agency-router` / `codebase-onboarding`: end-to-end logical flow and ownership-boundary review.
- `agency-router` / `technical-writer`: architecture, plan, README, skill, handoff, and generated-artifact contract review.
- `agency-router` / `reality-checker`: bug reproduction, privacy boundaries, CLI exits, persistence, parsing, idempotency, and evidence-quality review.

Loop 7 used Parfit (`@ponytail`), Schrodinger (`codebase-onboarding`), Raman (`technical-writer`), and Arendt (`reality-checker`). Its single fixback was sent with marker `[TEAM_REVIEW_FIX_DONE_7]` and the required `@codex-fable5` sequence. The same four reviewers then performed the re-review. No second fixback was started because the requested additional loop was limited to one cycle.

## Review Progression

| Stage | Reviewed HEAD | Result | Main outcome |
| --- | --- | --- | --- |
| Initial review | `a15462f` | NEEDS WORK | Privacy, artifact grounding, CLI, GitHub, persistence, approval, and documentation gaps identified. |
| Re-review 1 | `a7db341` | NEEDS WORK | Initial defects substantially closed; secret masking and GitHub/approval boundaries remained. |
| Re-review 2 | `a504145` | NEEDS WORK | Multi-word secret masking, stale verification, and GitHub artifact-scope documentation remained. |
| Re-review 3 | `c8ea28a` | NEEDS WORK | Current-approval enforcement and narrow plan/runtime drift remained. |
| Re-review 4 | `6a19ea8` | NEEDS WORK | Non-object approval JSON and handoff schema wording remained. |
| Re-review 5 | `d491cd1` | NEEDS WORK | Architecture specs still overstated approval fields. |
| Re-review 6 | `e7d2957` | PASS | All four reviewer lanes reported no remaining improvement request. |
| Loop 7 initial | `8b3e6f2` | NEEDS WORK | New reviewers reproduced approval-path, privacy, evidence freshness, snapshot, GitHub, CLI, and documentation defects. |
| Loop 7 fixback | `f9a4527` | NEEDS WORK | Most initial findings closed; adversarial re-review found remaining P1/P2 defects. |

## Final Verification Evidence

Reviewer-verified commands at `f9a4527`:

```text
PYTHONDONTWRITEBYTECODE=1 ./.venv/bin/python -m pytest -q -p no:cacheprovider
86 passed

git show --check --format=short HEAD
passed

git diff --check
passed
```

Behavioral evidence covered by the Loop 7 re-review:

- Approval JSON type validation and safe CLI error mapping.
- Approval revocation and newly forbidden-path enforcement during artifact generation.
- Mixed-case sensitive filename handling and secret masking during ingestion.
- Local discovery survival when GitHub is unavailable.
- Private and excluded repository filtering before per-repository GitHub activity calls.
- Per-repository GitHub partial-failure preservation.
- Repeat-run protection for local ingestion and GitHub activity storage.
- Alignment improvements to README and bilingual architecture specifications.
- Post-validation path replacement, legacy/tampered snapshot masking, damaged snapshot recovery, malformed stale profile recovery, endpoint field omissions, and case-variant repository exclusions.

## Final Reviewer Decisions

- Ponytail: NEEDS WORK. Remaining TOCTOU and stale-profile recovery defects plus previously reported cleanup remain.
- Logical-flow reviewer: NEEDS WORK. Legacy snapshot masking, public filename, path normalization, duplicate snapshot, casefold exclusion, and empty-evidence issues remain.
- Documentation reviewer: NEEDS WORK. TOCTOU, password-export naming, damaged snapshot recovery, draft contract, and first-discovery approval flow remain.
- Reality/bug reviewer: NEEDS WORK. Snapshot integrity, TOCTOU, privacy field validation, endpoint schema, and clean-error gaps remain reproducible.

The release gate is therefore open as NEEDS WORK for version 0.1.0.

## Blocking Findings And Residual Risks

Blocking release findings are documented in `docs/reviews/2026-07-10-team-based-review-loop-7-rereview.md`. The highest-risk items are the approval-path TOCTOU window, legacy/tampered snapshot masking and integrity, password-export/public-filename privacy gaps, and fail-open GitHub privacy metadata.

- Live authenticated GitHub discovery depends on the user's `gh` installation, credentials, network, and GitHub API availability. The final pass validated this path through committed mocks and fixtures, not a live account read.
- GitHub remains discovery-only, so GitHub activity does not affect generated artifacts in this release.
- Local raw snapshots and SQLite history persist until the user deletes the `.portfolio-maker/` workspace state.
- OS-level path replacement between validation and read remains possible at `f9a4527`.
- Historical `text-v1` snapshots are not migrated to the strengthened masking contract.

The first three bullets include documented product boundaries. The final two and the linked re-review findings are unresolved reviewer improvement requests.

## Planned Publication Contents

Publication has not occurred. The user authorized adding `koreaben777/portfolio-maker` as `origin` and pushing `8b3e6f2` to `main` only if the additional loop produced no feedback. Loop 7 produced actionable feedback and ended NEEDS WORK, so no remote was added and no push was performed.

A future publication is planned to include:

- The Portfolio Maker 0.1.0 source, tests, README, architecture, plan, and handoff documents.
- The `portfolio-maker` Codex skill used to operate the product.
- The reusable `team-based-review-loop` skill containing the fixed `@ponytail` and agency-router reviewer roles plus the `@codex-fable5` implementation-feedback protocol.
- The complete in-repository review reports for Loops 2 through 7 and this final report.

Local `.codex-fable5/` runtime state and `.portfolio-maker/` generated user data are excluded from publication.
