---
name: portfolio-maker
description: Use when generating a local evidence-based career profile or portfolio draft from approved local files and GitHub activity in this repository.
---

# Portfolio Maker Workflow

Use this skill to run the Portfolio Maker MVP safely from Codex app.

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
3. Run discovery:

```bash
portfolio-maker discover --workspace .
```

If the user supplied forbidden paths, pass one `--forbidden-path` argument per path.

4. Ask the user to review:

```text
.portfolio-maker/reviews/discovery-report.md
```

5. Create a sample approval file when needed:

```bash
portfolio-maker approve --workspace . --write-sample
```

6. Ask the user to edit and approve:

```text
.portfolio-maker/reviews/source-approval.json
```

7. After approval only, run:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```

8. Review generated artifacts:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
```

9. Report:
   - what was generated
   - which commands were run
   - whether public artifacts avoided secrets and private raw paths
   - any skipped sources or residual risks
