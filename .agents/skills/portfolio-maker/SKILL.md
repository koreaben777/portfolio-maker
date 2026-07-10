---
name: portfolio-maker
description: Use when generating a local evidence-based career profile or portfolio draft from approved local files in this repository. GitHub activity is discovery-only in this MVP.
---

# Portfolio Maker Workflow

Use this skill to run the Portfolio Maker MVP safely from Codex app.

GitHub repositories and activities can be discovered for review, but this MVP does not ingest them into profile or portfolio artifacts.

The generated portfolio draft is a review-required portfolio skeleton. It lists approved sources but leaves role, technical approach, and outcome as placeholders; evidence-rendered portfolio writing is deferred.

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

4. Edit `excluded_repositories` (canonical `owner/repo` values only) and `private_sources_allowed` in `.portfolio-maker/reviews/source-approval.json` when GitHub visibility rules are needed.

5. Run discovery:

```bash
portfolio-maker discover --workspace .
```

If the user supplied forbidden paths, pass one `--forbidden-path` argument per path.

6. Ask the user to review:

```text
.portfolio-maker/reviews/discovery-report.md
```

7. Ask the user to complete local source approval:

```text
.portfolio-maker/reviews/source-approval.json
```

8. After approval only, run:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```

9. Review generated artifacts:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
```

10. Report:
   - what was generated
   - which commands were run
   - whether public artifacts avoided secrets and private raw paths
   - any skipped sources or residual risks
