---
name: portfolio-maker
description: Use when generating a local evidence-based career profile or portfolio draft from approved local files and explicitly approved public GitHub activities in this repository.
---

# Portfolio Maker Workflow

Use this skill to run the Portfolio Maker MVP safely from Codex app.

GitHub repositories and activities are discovery metadata by default. Only exact URLs in `approved_github_activity_urls` can enter profile or draft artifacts, and then only for a currently confirmed public repository that passes `allowed_repositories` and `excluded_repositories` revalidation. GitHub activity appears as reviewable evidence, never as an automatic project narrative.

The generated portfolio draft is a review-required portfolio skeleton. Narrative role, technical approach, and outcome writing remains deferred; the static HTML surface renders only verified GitHub-backed public claims, evidence, and their project timelines. Approved local source URIs do not grant public HTML admission; an explicit public label/description approval field is deferred.

## Safety Rules

- Do not run `portfolio-maker ingest` until the user has reviewed `.portfolio-maker/reviews/discovery-report.md` and approved `.portfolio-maker/reviews/source-approval.json`.
- Do not print secrets, tokens, private key material, or credential values.
- Do not ask to inspect `.env`, private keys, password-manager exports, browser profiles, or forbidden folders.
- Keep generated public artifacts free of private raw paths.

## Workflow

1. Confirm the user's target:
   - master profile
   - portfolio draft
   - both
2. Ask for forbidden folders and repositories.
3. Before the first GitHub discovery, create an approval sample:

```bash
portfolio-maker approve --workspace . --write-sample
```

This creates the sample only when no approval file exists. Use `portfolio-maker approve --workspace . --write-sample --force` only to deliberately reset an existing approval file.

4. Before discovery, persist the policy fields in `.portfolio-maker/reviews/source-approval.json`: `forbidden_paths` (required for ingest/profile/draft revalidation), `excluded_repositories`, `allowed_repositories` (canonical `owner/repo` values only), `private_sources_allowed`, and `excluded_file_patterns` (case-insensitive filename globs). Keep `approved_github_activity_urls` empty until discovery has recorded exact public activity URLs.

5. Run discovery:

```bash
portfolio-maker discover --workspace .
```

`--forbidden-path` applies only to this discovery run; persist paths in `forbidden_paths` before ingest, profile, or draft generation.

6. Ask the user to review:

```text
.portfolio-maker/reviews/discovery-report.md
```

7. In `GitHub Activities`, find each candidate URL only after confirming its matching entry in `GitHub Repositories` is marked `(public)`. Copy the selected exact URL into `approved_github_activity_urls`. Do not approve private, excluded, missing, or stale activities.

8. Legacy workflow activities without persisted provenance remain ineligible for profile and portfolio artifacts. Recover them by completing a successful rediscovery:

```bash
portfolio-maker discover --workspace .
```

After discovery succeeds, review the report and reapprove the exact public activity URL in `approved_github_activity_urls` if it is still needed.

9. Ask the user to complete local source approval:

```text
.portfolio-maker/reviews/source-approval.json
```

10. After approval only, run:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

11. Review generated artifacts:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
.portfolio-maker/artifacts/portfolio-public.json
.portfolio-maker/artifacts/portfolio.html
```

12. Report:
   - what was generated
   - which commands were run
   - whether public artifacts avoided secrets and private raw paths
   - any skipped sources or residual risks
