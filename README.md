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
- Original files are not copied into `.portfolio-maker/`.
- Extracted snapshots are masked for common secret patterns.
- Private GitHub repositories are skipped unless `private_sources_allowed` is set to `true` in the approval file.
- Repositories listed in `excluded_repositories` are skipped during GitHub discovery.
- Public portfolio drafts must not include secrets, tokens, or private raw paths.
- Keep `.portfolio-maker/` out of Git.

## MVP Contract

- The evidence store uses `sources`, `source_snapshots`, and `github_activities` at runtime.
- GitHub repositories and activities are discovered and reviewed, but are not ingested into profile or portfolio artifacts until the later company-specific generation phase.
- Normalized `evidence_items`, `career_claims`, and artifact rows remain reserved for the later company-specific generation phase.

## Codex App Workflow

In Codex app, invoke the repo skill:

```text
$portfolio-maker
```

Then follow the approval flow:

```bash
portfolio-maker discover --workspace .
portfolio-maker approve --workspace . --write-sample
```

Review:

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
