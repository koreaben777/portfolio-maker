---
name: portfolio-artifacts
description: Use when generating, validating, previewing, or preparing delivery of Portfolio Maker profile, Markdown, manifest, or interactive HTML artifacts.
---

# Portfolio Artifacts

Use this skill for the artifact layer only. Source discovery, source approval, semantic indexing, and project review remain separate authorities. A filename containing `public` never grants public delivery permission.

## Required Gates

Run every gate against the current workspace state. Do not reuse an old policy hash, project review input, project approval, or generated artifact after changing an approval or policy.

1. Confirm the current source approval and artifact policy. The policy file is `.portfolio-maker/reviews/artifact-approval.json`. For a new workspace, initialize it once with:

   ```bash
   portfolio-maker approve --workspace . --write-sample-artifact-policy
   ```

   Review each artifact's `delivery_scope` and `include_*` fields before continuing. The default is `restricted`. `open_public` may not include local or private GitHub evidence and requires a separate regeneration and validation. Never use `--force` to overwrite an existing approval without explicit user authorization.

2. Ingest only after current source approval is settled, then refresh the safe review input. This is the required policy revalidation gate before any artifact build:

   ```bash
   portfolio-maker ingest --workspace .
   portfolio-maker prepare-project-review --workspace .
   ```

   `prepare-project-review` re-reads current source approval and artifact policy, performs current evidence selection, records the resulting policy hash, and writes only safe review fields. Stop on an approval or policy error. Do not read raw sources, snapshots, the database, private GitHub URLs, or review files outside the safe review input while composing projects.

3. Require active approved semantic projects before building. Review the safe input, create candidates with `review_required: true`, and have the user approve the project grouping. Materialize the approval with:

   ```bash
   portfolio-maker approve --workspace . --write-sample-project-approval
   portfolio-maker compose-projects --workspace .
   portfolio-maker list-projects --workspace . --format table
   ```

   Use `compose-projects --mode review` or `--mode automatic` when the v2 review interface is active. Only active decision states (`manually_approved`, `auto_included_high`, `auto_included_medium`) are eligible for artifact projections. `review_required`, `excluded`, `inactive`, rejected, unassigned, stale, or policy-excluded evidence is not a project input. Zero approved projects is a valid empty state; do not invent a project from one file, repository, or activity.

## Build And Validate

After the gates above, run the actual repository commands in order:

```bash
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
portfolio-maker render-html --workspace .
```

Each builder must re-read current policy and evidence selection. The resulting files are normally under `.portfolio-maker/artifacts/`: `master-profile.json`, `master-profile.md`, `portfolio-draft.md`, `portfolio-public.json`, and `portfolio.html`.

`render-html` is the canonical HTML path. It builds the local Sites project in an isolated temporary directory, runs `npm run build`, calls `validate_static_output`, inlines the CSS and JavaScript, and writes the managed canonical HTML only after those checks pass. Install the repository's Sites dependencies first when needed:

```bash
(cd web/portfolio && npm ci)
```

For a focused direct check of an already-built Sites distribution, use the existing validator API rather than inventing a CLI command:

```bash
PYTHONPATH=src python -c 'from pathlib import Path; from portfolio_maker.infrastructure.static_site import validate_static_output; validate_static_output(Path("web/portfolio/dist"))'
```

Accept an artifact only when static validation and a content scan pass. Check generated HTML, JavaScript, and manifest for:

- runtime `fetch(`, `XMLHttpRequest`, or other data loading;
- `portfolio.db`, `.portfolio-maker`, `file://`, raw absolute local paths, source/snapshot/review locators, or private GitHub URLs;
- credentials, tokens, API keys, passwords, private keys, or secret-shaped text;
- root-relative asset references instead of self-contained relative/inlined assets.

Sites receives only the build-time safe manifest and generated presentation inputs. Never pass raw SQLite files, source files, snapshots, review bundles, approval files, or credentials to Sites. Keep the local managed artifacts as the canonical output.

## Delivery And Hosting

Generation, preview, and hosting are separate choices. Ask for an explicit deployment choice after validation: `private` or `public`. Do not invoke Sites hosting automatically and do not return a synthetic URL.

- `restricted` is the default and permits local use, authorized direct delivery, or authorized private hosting. Private deployment still requires explicit user authorization; use the existing `prepare_private_deployment(DeploymentArtifact(...))` guard.
- Public hosting requires an explicit user approval and a freshly generated artifact whose relevant policy says `delivery_scope: open_public`. Use the existing `prepare_public_deployment(DeploymentArtifact(...))` guard; it rejects restricted output.
- A `portfolio-public.json` or `portfolio.html` filename does not change either rule. Never promote restricted evidence to `open_public` implicitly.

If the user has not chosen hosting or has not authorized the requested scope, stop after local validation and report the canonical artifact paths without deployment.
