# Portfolio Maker

Portfolio Maker is a Codex app guided local workflow for building an evidence-based career profile and public portfolio draft from approved local files and GitHub activity.

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
- Public portfolio drafts must not include secrets, tokens, or private raw paths.
- Keep `.portfolio-maker/` out of Git.

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

GitHub rate limits are recorded as paused states. Re-run the same command after the limit resets.
