---
name: portfolio-maker
description: Use when generating local evidence-based career artifacts from approved local files and explicitly approved public or private GitHub activities in this repository.
---

# Portfolio Maker Workflow

> Plugin users should invoke the `$portfolio-maker` plugin router for the
> end-to-end 0.2.0 workflow. This repository-local file remains the compatible
> repository entrypoint when plugin installation is unavailable; its existing
> CLI commands and 0.1.0 approval gates are intentionally retained below.

Use this skill to run Portfolio Maker safely from Codex app.

GitHub repositories and activities are discovery metadata by default. Only exact URLs in `approved_github_activity_urls` or `approved_private_github_activity_urls` can enter artifacts, after the matching origin and current policy checks pass. GitHub activity appears as reviewable evidence, never as an automatic project narrative or project.

The generated portfolio draft is a review-required portfolio skeleton. Narrative role, technical approach, and outcome writing remains deferred; the static HTML surface renders only evidence selected by its artifact policy. Raw local paths and private GitHub URLs remain withheld. In restricted output, a private repository name is allowed only when it is user-approved display text in a semantic project title or overview; automatic source labels remain safe/generic.

## Safety Rules

- Do not run `portfolio-maker ingest` until the user has reviewed `.portfolio-maker/reviews/discovery-report.md` and approved `.portfolio-maker/reviews/source-approval.json`.
- Do not print secrets, tokens, private key material, or credential values.
- Do not ask to inspect `.env`, private keys, password-manager exports, browser profiles, or forbidden folders.
- Keep generated public artifacts free of private raw paths.
- The HTML renderer requires Node.js LTS with npm. On a fresh checkout, run `(cd web/portfolio && npm ci)` once before the normal workspace command; do not install Vite globally or use an `npx` fallback.

## Workflow

1. Confirm the user's target:
   - master profile
   - portfolio draft
   - both
2. Ask for excluded folders and repositories.
3. Before the first GitHub discovery, create an approval sample:

```bash
portfolio-maker approve --workspace . --write-sample
```

This creates the sample only when no approval file exists. Use `portfolio-maker approve --workspace . --write-sample --force` only to deliberately reset an existing approval file.

4. Before discovery, persist the policy fields in `.portfolio-maker/reviews/source-approval.json`: `excluded_directories`, `forbidden_paths` (legacy alias, required for ingest/profile/draft revalidation), `excluded_repositories`, `allowed_repositories` (canonical `owner/repo` values only), `private_sources_allowed`, and `excluded_file_patterns` (case-insensitive filename globs). Keep both activity approval lists empty until discovery has recorded exact URLs.

5. Run discovery:

```bash
portfolio-maker discover --workspace .
```

`--exclude-directory PATH` is repeatable and persists the selected directories in
`excluded_directories`. `--forbidden-path` remains a discovery-only compatibility option;
persist the resulting policy before ingest, profile, or draft generation.

6. Ask the user to review:

```text
.portfolio-maker/reviews/discovery-report.md
```

7. In `GitHub Activities`, find each candidate URL only after confirming its matching entry in `GitHub Repositories` is marked `(public)` or is an explicitly allowed private repository. Copy selected exact public URLs into `approved_github_activity_urls` and exact private URLs into `approved_private_github_activity_urls`. A private URL may appear on this local discovery/approval surface so the user can make that explicit selection; it must not enter generated artifacts or the safe semantic review bundle. Do not approve excluded, missing, or stale activities.

8. Legacy workflow activities without persisted provenance remain ineligible for profile and portfolio artifacts. Recover them by completing a successful rediscovery:

```bash
portfolio-maker discover --workspace .
```

After discovery succeeds, review the report and reapprove the exact public activity URL in `approved_github_activity_urls` if it is still needed.

9. Ask the user to complete local source approval:

```text
.portfolio-maker/reviews/source-approval.json
```

Create the artifact selection policy after reviewing discovery and source approval:

```bash
portfolio-maker approve --workspace . --write-sample-artifact-policy
```

Review `.portfolio-maker/reviews/artifact-approval.json`. Its default delivery scope is
`restricted`; `open_public` is a separate, explicitly regenerated public-GitHub-only scope.
The artifact policy is required for per-artifact selection and is reloaded by
`build-profile`, `draft-portfolio`, and `render-html`; `ingest` revalidates source approval only.

10. On a fresh checkout, with Node.js LTS and npm available, install the Sites dependencies once:

```bash
(cd web/portfolio && npm ci)
```

11. To compose semantic portfolio projects, prepare the safe review bundle from the repository root:

```bash
portfolio-maker prepare-project-review --workspace .
```

Only `.portfolio-maker/reviews/project-review-input.json` is supplied to Codex. Codex may write
`project-candidates.json` and `project-candidates.md` using only that bundle. Review and edit the
candidate file, or write `project-approval.json` directly, then create the sample approval shape
when needed:

```bash
portfolio-maker approve --workspace . --write-sample-project-approval
portfolio-maker compose-projects --workspace .
```

Candidate output is not database truth. Only the user's `status: approved` projects are materialized;
rejected, unassigned, stale, policy-excluded, or unknown evidence is not a project. Without project
approval, generated project sections use an honest zero-project state while the evidence inventory
remains available for review.

12. After source, artifact, and project approval, run the following commands from the repository root:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

Restricted outputs may be used locally, sent to a verified recipient, or deployed through a
private Sites path after static validation. Do not infer public deployment permission from the
`portfolio-public.json` or `portfolio.html` filenames; public deployment requires an explicit
`open_public` policy and separate validation, and is not run by this workflow.

13. Review generated artifacts:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
.portfolio-maker/artifacts/portfolio-public.json
.portfolio-maker/artifacts/portfolio.html
```

14. Report:
   - what was generated
   - which commands were run
   - whether public artifacts avoided secrets and private raw paths
   - delivery scope and artifact-policy exclusions
   - any skipped sources or residual risks
