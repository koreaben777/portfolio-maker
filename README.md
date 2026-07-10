# Portfolio Maker

Portfolio Maker is a Codex app guided local workflow for building an evidence-based career profile and public portfolio draft from approved local files. GitHub activity is discovery-only in this MVP.

## MVP Scope

- Local file discovery
- GitHub activity discovery through `gh`
- Explicit source approval before ingestion
- SQLite-backed career evidence store
- Minimal extracted-text snapshots
- Master profile JSON/Markdown
- Portfolio draft Markdown

## Requirements

- macOS
- Codex app
- Python 3.11+
- Git
- GitHub CLI `gh` for GitHub discovery

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Safety Rules

- Discovery may list candidates, but ingestion is blocked until `.portfolio-maker/reviews/source-approval.json` exists.
- Profile generation rechecks the current approval and forbidden-path policy, the original file hash, and the latest snapshot before using an ingested source.
- Original files are not copied into `.portfolio-maker/`.
- Extracted snapshots are masked for common secret patterns.
- Private GitHub repositories are skipped unless `private_sources_allowed` is set to `true` in the approval file.
- Repositories listed in `excluded_repositories` are skipped during GitHub discovery.
- Public portfolio drafts must not include secrets, tokens, or private raw paths.
- Keep `.portfolio-maker/` out of Git.

## MVP Contract

- The evidence store uses `sources`, `source_snapshots`, and `github_activities` at runtime.
- GitHub repositories and activities are discovered and reviewed, but are not ingested into profile or portfolio artifacts until the later company-specific generation phase.
- Normalized evidence, claim, and artifact tables are not created until the later company-specific generation phase needs runtime readers and writers.
- The 0.1.0 portfolio draft is a review-required portfolio skeleton: it lists approved sources but does not render evidence into role, technical approach, or outcome claims. Evidence-rendered portfolio writing is deferred.

## Codex App Workflow

In Codex app, invoke the repo skill:

```text
$portfolio-maker
```

Then follow the approval flow:

```bash
portfolio-maker approve --workspace . --write-sample
```

Before the first GitHub discovery, edit `excluded_repositories` and `private_sources_allowed` in the sample approval file when needed. Then run:

```bash
portfolio-maker discover --workspace .
```

Review and complete local source approvals:

```text
.portfolio-maker/reviews/discovery-report.md
.portfolio-maker/reviews/source-approval.json
```

After approval:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```

Generated artifacts:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
```

## Troubleshooting

### GitHub authentication

Run:

```bash
gh auth status
```

If authentication is missing, run:

```bash
gh auth login
```

Use the narrowest read-only access that supports repository and activity reads.

### Permission errors

Permission-denied paths are skipped and recorded. Add sensitive folders to `forbidden_paths` in `.portfolio-maker/reviews/source-approval.json`.

### Rate limits

GitHub discovery failures are recorded in the discovery report while local discovery continues. Re-run after the limit resets, or use `--no-github`.

### Discovery limits

Local discovery records at most 500 candidates. GitHub repository, pull request, and issue commands request at most 100 items; GitHub API endpoints are not paginated. Discovery reports may therefore be incomplete.
