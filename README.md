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

- Python 3.11 or newer

## Safety Rules

- The tool must not ingest source bodies until `.portfolio-maker/reviews/source-approval.json` approves them.
- The tool must not copy original files into `.portfolio-maker/`.
- Public artifacts must not expose secrets, tokens, or private raw paths.

## Local Setup

```bash
PYTHON_BIN=python3.11  # replace with your Python 3.11+ executable if needed
$PYTHON_BIN -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
pytest
```

## Planned Commands

The following MVP commands are planned for later tasks and are not implemented yet:

```bash
portfolio-maker discover --workspace .
portfolio-maker approve --workspace . --write-sample
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```

The currently working smoke command is:

```bash
portfolio-maker
```
