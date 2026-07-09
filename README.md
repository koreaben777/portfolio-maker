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

## Safety Rules

- The tool must not ingest source bodies until `.portfolio-maker/reviews/source-approval.json` approves them.
- The tool must not copy original files into `.portfolio-maker/`.
- Public artifacts must not expose secrets, tokens, or private raw paths.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Basic Commands

```bash
portfolio-maker discover --workspace .
portfolio-maker approve --workspace . --write-sample
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```
