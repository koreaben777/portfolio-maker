# Portfolio Maker MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Codex app에서 호출 가능한 CLI 기반 MVP를 만들어 로컬 파일과 GitHub 활동 후보를 발견하고, 승인된 로컬 source만 ingest한 뒤 근거 기반 master profile과 portfolio draft를 생성한다. GitHub 활동은 이 MVP에서 discovery-only이며, artifact 입력은 후속 회사별 맞춤 생성 단계로 남긴다.

**Architecture:** MVP는 Codex Skill + CLI adapter + reusable application use cases 구조로 구현한다. 핵심 로직은 CLI나 Codex thread에 의존하지 않는 Python package에 두고, 저장소는 SQLite와 최소 snapshot file store를 사용한다. 향후 Codex app-server나 MCP adapter가 같은 use case를 재사용할 수 있도록 request/result dataclass와 structured progress event를 사용한다.

**Tech Stack:** Python 3.11+, standard library `argparse`, `dataclasses`, `json`, `sqlite3`, `pathlib`, `subprocess`; test runner `pytest`; packaging via `pyproject.toml`.

---

## Scope Notes

이 계획은 승인된 스펙의 MVP 범위만 구현한다.

포함:

- repo-scoped Codex skill
- Python CLI
- local discovery
- GitHub discovery via `gh` CLI JSON output
- approval gate
- SQLite schema and repositories
- minimal raw snapshot store
- approved local source ingestion
- evidence-based master profile JSON/Markdown
- public portfolio draft Markdown
- README setup guide
- automated tests for core policy, storage, and artifact behavior

제외:

- Google Drive
- company/JD research
- resume, cover letter, interview material
- OCR
- vector database
- MCP server
- Codex app-server companion
- standalone GUI

## File Structure

Create these files:

```text
pyproject.toml
README.md
.gitignore
.agents/skills/portfolio-maker/SKILL.md
src/portfolio_maker/__init__.py
src/portfolio_maker/__main__.py
src/portfolio_maker/adapters/__init__.py
src/portfolio_maker/adapters/cli.py
src/portfolio_maker/application/__init__.py
src/portfolio_maker/application/approval.py
src/portfolio_maker/application/build_profile.py
src/portfolio_maker/application/discovery.py
src/portfolio_maker/application/draft_portfolio.py
src/portfolio_maker/application/ingestion.py
src/portfolio_maker/application/models.py
src/portfolio_maker/domain/__init__.py
src/portfolio_maker/domain/models.py
src/portfolio_maker/infrastructure/__init__.py
src/portfolio_maker/infrastructure/artifacts.py
src/portfolio_maker/infrastructure/audit.py
src/portfolio_maker/infrastructure/extractors.py
src/portfolio_maker/infrastructure/github_connector.py
src/portfolio_maker/infrastructure/local_discovery.py
src/portfolio_maker/infrastructure/policy.py
src/portfolio_maker/infrastructure/snapshots.py
src/portfolio_maker/infrastructure/sqlite_repository.py
src/portfolio_maker/workspace.py
tests/conftest.py
tests/fixtures/github/gh_repo_list.json
tests/fixtures/github/gh_pr_list.json
tests/fixtures/github/gh_issue_list.json
tests/test_approval.py
tests/test_artifacts.py
tests/test_cli.py
tests/test_github_connector.py
tests/test_ingestion.py
tests/test_local_discovery.py
tests/test_policy.py
tests/test_profile_and_portfolio.py
tests/test_sqlite_repository.py
```

Responsibility boundaries:

- `adapters/cli.py`: argument parsing, safe terminal output, exit codes only.
- `application/*.py`: use cases and orchestration. No direct printing.
- `domain/models.py`: stable domain dataclasses and enum values.
- `infrastructure/*.py`: file system, GitHub CLI, SQLite, snapshots, extraction, artifact writing.
- `workspace.py`: `.portfolio-maker/` path resolution and directory creation.
- `.agents/skills/portfolio-maker/SKILL.md`: Codex app workflow instructions.

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `src/portfolio_maker/__init__.py`
- Create: `src/portfolio_maker/__main__.py`
- Create: `src/portfolio_maker/adapters/__init__.py`
- Create: `src/portfolio_maker/application/__init__.py`
- Create: `src/portfolio_maker/domain/__init__.py`
- Create: `src/portfolio_maker/infrastructure/__init__.py`
- Test: none for this scaffold task

- [ ] **Step 1: Create package metadata**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.build_meta"

[project]
name = "portfolio-maker"
version = "0.1.0"
description = "Codex-guided local career knowledge base and portfolio draft generator"
requires-python = ">=3.11"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
portfolio-maker = "portfolio_maker.adapters.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 2: Create Git ignore rules**

Create `.gitignore`:

```gitignore
.DS_Store
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.venv/
venv/
build/
dist/
*.egg-info/
.portfolio-maker/
```

- [ ] **Step 3: Create minimal README**

Create `README.md`:

```markdown
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
```

- [ ] **Step 4: Create package entrypoints**

Create `src/portfolio_maker/__init__.py`:

```python
__all__ = ["__version__"]

__version__ = "0.1.0"
```

Create `src/portfolio_maker/__main__.py`:

```python
from portfolio_maker.adapters.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
```

Create empty package marker files:

```python
# src/portfolio_maker/adapters/__init__.py
```

```python
# src/portfolio_maker/application/__init__.py
```

```python
# src/portfolio_maker/domain/__init__.py
```

```python
# src/portfolio_maker/infrastructure/__init__.py
```

- [ ] **Step 5: Verify package installation**

Run:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python - <<'PY'
import portfolio_maker
print(portfolio_maker.__version__)
PY
```

Expected:

```text
0.1.0
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore README.md src/portfolio_maker
git commit -m "chore: scaffold python package"
```

## Task 2: Domain and Application Models

**Files:**
- Create: `src/portfolio_maker/domain/models.py`
- Create: `src/portfolio_maker/application/models.py`
- Test: `tests/test_policy.py` will import these in later tasks

- [ ] **Step 1: Create domain models**

Create `src/portfolio_maker/domain/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SourceType(StrEnum):
    LOCAL_FILE = "local_file"
    LOCAL_DIRECTORY = "local_directory"
    GITHUB_REPOSITORY = "github_repository"
    GITHUB_ACTIVITY = "github_activity"


class SourceStatus(StrEnum):
    DISCOVERED = "discovered"
    APPROVED = "approved"
    INGESTED = "ingested"
    SKIPPED_POLICY = "skipped_policy"
    SKIPPED_PERMISSION_DENIED = "skipped_permission_denied"
    EXTRACT_FAILED = "extract_failed"
    PAUSED_RATE_LIMIT = "paused_rate_limit"
    NETWORK_FAILED = "network_failed"
    AUTH_FAILED = "auth_failed"
    STALE_SOURCE = "stale_source"


class EvidenceKind(StrEnum):
    FILE_TEXT = "file_text"
    README = "readme"
    COMMIT = "commit"
    PULL_REQUEST = "pull_request"
    ISSUE = "issue"
    REVIEW = "review"
    WORKFLOW_RUN = "workflow_run"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass(frozen=True)
class Source:
    id: int | None
    type: SourceType
    uri: str
    display_name: str
    owner: str | None
    status: SourceStatus


@dataclass(frozen=True)
class SourceSnapshot:
    id: int | None
    source_id: int
    snapshot_path: str
    content_hash: str
    extractor: str


@dataclass(frozen=True)
class EvidenceItem:
    id: int | None
    source_id: int
    snapshot_id: int | None
    kind: EvidenceKind
    locator: str
    quote_hash: str | None
    summary: str
    confidence: Confidence


@dataclass(frozen=True)
class GitHubActivity:
    id: int | None
    source_id: int | None
    repo: str
    activity_type: str
    url: str
    title: str
    state: str
    author: str
    created_at: str
    merged_at: str | None


@dataclass(frozen=True)
class Project:
    id: int | None
    name: str
    summary: str
    status: str
    visibility: str
    primary_source_id: int | None


@dataclass(frozen=True)
class CareerClaim:
    id: int | None
    claim_type: str
    text: str
    confidence: Confidence
    public_safe: bool


@dataclass(frozen=True)
class Artifact:
    id: int | None
    type: str
    path: str
    source_profile_version: str
```

- [ ] **Step 2: Create application request/result models**

Create `src/portfolio_maker/application/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    message: str
    count: int | None = None


@dataclass(frozen=True)
class DiscoverSourcesRequest:
    workspace: Path
    home: Path
    include_github: bool = True
    forbidden_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DiscoverSourcesResult:
    report_path: Path
    discovered_count: int
    skipped_count: int
    events: tuple[ProgressEvent, ...] = ()


@dataclass(frozen=True)
class ApprovalRequest:
    workspace: Path
    write_sample: bool = False


@dataclass(frozen=True)
class ApprovalResult:
    approval_path: Path
    approved_sources: int
    forbidden_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class IngestSourcesRequest:
    workspace: Path


@dataclass(frozen=True)
class IngestSourcesResult:
    ingested_count: int
    skipped_count: int
    snapshot_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class BuildProfileRequest:
    workspace: Path


@dataclass(frozen=True)
class BuildProfileResult:
    json_path: Path
    markdown_path: Path
    claim_count: int


@dataclass(frozen=True)
class DraftPortfolioRequest:
    workspace: Path


@dataclass(frozen=True)
class DraftPortfolioResult:
    markdown_path: Path
    project_count: int


@dataclass
class DiscoveryReport:
    local_candidates: list[dict[str, str]] = field(default_factory=list)
    github_candidates: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
```

- [ ] **Step 3: Import models in a smoke command**

Run:

```bash
python - <<'PY'
from pathlib import Path
from portfolio_maker.application.models import DiscoverSourcesRequest
from portfolio_maker.domain.models import SourceStatus

req = DiscoverSourcesRequest(workspace=Path("."), home=Path.home())
print(req.workspace)
print(SourceStatus.DISCOVERED.value)
PY
```

Expected:

```text
.
discovered
```

- [ ] **Step 4: Commit**

```bash
git add src/portfolio_maker/domain/models.py src/portfolio_maker/application/models.py
git commit -m "feat: add core domain models"
```

## Task 3: Workspace Paths and Audit Log

**Files:**
- Create: `src/portfolio_maker/workspace.py`
- Create: `src/portfolio_maker/infrastructure/audit.py`
- Test: `tests/conftest.py`
- Test: `tests/test_sqlite_repository.py`

- [ ] **Step 1: Write workspace path test**

Create `tests/conftest.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path
```

Create `tests/test_sqlite_repository.py` with the initial workspace test:

```python
from portfolio_maker.workspace import WorkspacePaths


def test_workspace_paths_create_expected_directories(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    assert paths.root == workspace / ".portfolio-maker"
    assert paths.db_path == workspace / ".portfolio-maker" / "portfolio.db"
    assert paths.reviews_dir.is_dir()
    assert paths.artifacts_dir.is_dir()
    assert paths.local_snapshots_dir.is_dir()
    assert paths.github_snapshots_dir.is_dir()
    assert paths.logs_dir.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_sqlite_repository.py::test_workspace_paths_create_expected_directories -v
```

Expected: FAIL with `ModuleNotFoundError` or missing `WorkspacePaths`.

- [ ] **Step 3: Implement workspace paths**

Create `src/portfolio_maker/workspace.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspacePaths:
    workspace: Path
    root: Path
    db_path: Path
    raw_dir: Path
    snapshots_dir: Path
    local_snapshots_dir: Path
    github_snapshots_dir: Path
    artifacts_dir: Path
    reviews_dir: Path
    logs_dir: Path
    audit_log_path: Path
    discovery_report_path: Path
    approval_path: Path
    master_profile_json_path: Path
    master_profile_md_path: Path
    portfolio_draft_path: Path

    @classmethod
    def from_root(cls, workspace: Path) -> "WorkspacePaths":
        workspace = workspace.resolve()
        root = workspace / ".portfolio-maker"
        raw_dir = root / "raw"
        snapshots_dir = raw_dir / "snapshots"
        artifacts_dir = root / "artifacts"
        reviews_dir = root / "reviews"
        logs_dir = root / "logs"
        return cls(
            workspace=workspace,
            root=root,
            db_path=root / "portfolio.db",
            raw_dir=raw_dir,
            snapshots_dir=snapshots_dir,
            local_snapshots_dir=snapshots_dir / "local",
            github_snapshots_dir=snapshots_dir / "github",
            artifacts_dir=artifacts_dir,
            reviews_dir=reviews_dir,
            logs_dir=logs_dir,
            audit_log_path=logs_dir / "audit.jsonl",
            discovery_report_path=reviews_dir / "discovery-report.md",
            approval_path=reviews_dir / "source-approval.json",
            master_profile_json_path=artifacts_dir / "master-profile.json",
            master_profile_md_path=artifacts_dir / "master-profile.md",
            portfolio_draft_path=artifacts_dir / "portfolio-draft.md",
        )

    def ensure(self) -> None:
        for path in (
            self.root,
            self.raw_dir,
            self.snapshots_dir,
            self.local_snapshots_dir,
            self.github_snapshots_dir,
            self.artifacts_dir,
            self.reviews_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Implement audit logger**

Create `src/portfolio_maker/infrastructure/audit.py`:

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AuditEvent:
    event_type: str
    message: str
    data: dict[str, Any]


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def write(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(event)
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            f.write("\n")
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
pytest tests/test_sqlite_repository.py::test_workspace_paths_create_expected_directories -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/workspace.py src/portfolio_maker/infrastructure/audit.py tests/conftest.py tests/test_sqlite_repository.py
git commit -m "feat: add workspace paths and audit log"
```

## Task 4: Policy Filters and Secret Masking

**Files:**
- Create: `src/portfolio_maker/infrastructure/policy.py`
- Test: `tests/test_policy.py`

- [ ] **Step 1: Write policy tests**

Create `tests/test_policy.py`:

```python
from pathlib import Path

from portfolio_maker.infrastructure.policy import (
    DEFAULT_EXCLUDED_NAMES,
    FilePolicy,
    mask_secrets,
)


def test_default_exclusions_include_sensitive_and_large_dirs():
    assert ".Trash" in DEFAULT_EXCLUDED_NAMES
    assert "Library" in DEFAULT_EXCLUDED_NAMES
    assert "node_modules" in DEFAULT_EXCLUDED_NAMES
    assert ".git" in DEFAULT_EXCLUDED_NAMES


def test_forbidden_path_blocks_descendants(tmp_path):
    forbidden = tmp_path / "private"
    target = forbidden / "notes.md"
    policy = FilePolicy(forbidden_paths=(forbidden,))

    assert policy.is_forbidden(target)
    assert policy.classify_path(target) == "forbidden"


def test_env_and_private_key_files_are_skipped(tmp_path):
    policy = FilePolicy()

    assert policy.classify_path(tmp_path / ".env") == "skipped_policy"
    assert policy.classify_path(tmp_path / "id_rsa") == "skipped_policy"
    assert policy.classify_path(tmp_path / "project.md") == "candidate"


def test_secret_masking_removes_token_values():
    text = "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234567890abcd\npassword = supersecret"

    masked = mask_secrets(text)

    assert "ghp_" not in masked
    assert "supersecret" not in masked
    assert "[REDACTED]" in masked
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_policy.py -v
```

Expected: FAIL with missing `portfolio_maker.infrastructure.policy`.

- [ ] **Step 3: Implement policy module**

Create `src/portfolio_maker/infrastructure/policy.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


DEFAULT_EXCLUDED_NAMES = {
    ".Trash",
    "Library",
    "Applications",
    "node_modules",
    ".git",
    ".venv",
    "venv",
    "__pycache__",
}

SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
}

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(password\s*=\s*)([^\s]+)"),
    re.compile(r"(?i)(api[_-]?key\s*=\s*)([^\s]+)"),
    re.compile(r"(?i)(token\s*=\s*)([^\s]+)"),
]


@dataclass(frozen=True)
class FilePolicy:
    forbidden_paths: tuple[Path, ...] = ()
    max_file_size_bytes: int = 2_000_000

    def is_forbidden(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        for forbidden in self.forbidden_paths:
            forbidden_resolved = forbidden.resolve(strict=False)
            if resolved == forbidden_resolved or forbidden_resolved in resolved.parents:
                return True
        return False

    def classify_path(self, path: Path) -> str:
        if self.is_forbidden(path):
            return "forbidden"
        if path.name in SENSITIVE_FILE_NAMES:
            return "skipped_policy"
        if any(part in DEFAULT_EXCLUDED_NAMES for part in path.parts):
            return "skipped_policy"
        return "candidate"


def mask_secrets(text: str) -> str:
    masked = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 2:
            masked = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", masked)
        else:
            masked = pattern.sub("[REDACTED]", masked)
    return masked
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_policy.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/policy.py tests/test_policy.py
git commit -m "feat: add file policy and secret masking"
```

## Task 5: SQLite Repository

**Files:**
- Create: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `tests/test_sqlite_repository.py`

- [ ] **Step 1: Add SQLite repository tests**

Append to `tests/test_sqlite_repository.py`:

```python
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository


def test_sqlite_repository_initializes_schema(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)

    repo.initialize()

    table_names = repo.table_names()
    assert "sources" in table_names
    assert "source_snapshots" in table_names
    assert "evidence_items" in table_names
    assert "github_activities" in table_names
    assert "career_claims" in table_names


def test_sqlite_repository_upserts_source(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()

    source_id = repo.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///tmp/example.md",
            display_name="example.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    sources = repo.list_sources()
    assert source_id == 1
    assert len(sources) == 1
    assert sources[0].uri == "file:///tmp/example.md"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_sqlite_repository.py -v
```

Expected: FAIL with missing `SQLiteRepository`.

- [ ] **Step 3: Implement SQLite schema and source operations**

Create `src/portfolio_maker/infrastructure/sqlite_repository.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from portfolio_maker.domain.models import Source, SourceStatus, SourceType


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  uri TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  owner TEXT,
  status TEXT NOT NULL,
  discovered_at TEXT DEFAULT CURRENT_TIMESTAMP,
  approved_at TEXT
);

CREATE TABLE IF NOT EXISTS source_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  snapshot_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  extractor TEXT NOT NULL,
  extracted_at TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  snapshot_id INTEGER,
  kind TEXT NOT NULL,
  locator TEXT NOT NULL,
  quote_hash TEXT,
  summary TEXT NOT NULL,
  confidence TEXT NOT NULL,
  FOREIGN KEY(source_id) REFERENCES sources(id),
  FOREIGN KEY(snapshot_id) REFERENCES source_snapshots(id)
);

CREATE TABLE IF NOT EXISTS github_activities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER,
  repo TEXT NOT NULL,
  activity_type TEXT NOT NULL,
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  state TEXT NOT NULL,
  author TEXT NOT NULL,
  created_at TEXT NOT NULL,
  merged_at TEXT,
  FOREIGN KEY(source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  summary TEXT NOT NULL,
  status TEXT NOT NULL,
  visibility TEXT NOT NULL,
  primary_source_id INTEGER,
  FOREIGN KEY(primary_source_id) REFERENCES sources(id)
);

CREATE TABLE IF NOT EXISTS career_claims (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  claim_type TEXT NOT NULL,
  text TEXT NOT NULL,
  confidence TEXT NOT NULL,
  public_safe INTEGER NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS claim_evidence (
  claim_id INTEGER NOT NULL,
  evidence_id INTEGER NOT NULL,
  support_level TEXT NOT NULL,
  PRIMARY KEY (claim_id, evidence_id),
  FOREIGN KEY(claim_id) REFERENCES career_claims(id),
  FOREIGN KEY(evidence_id) REFERENCES evidence_items(id)
);

CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  path TEXT NOT NULL,
  source_profile_version TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def table_names(self) -> set[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {row["name"] for row in rows}

    def upsert_source(self, source: Source) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sources (type, uri, display_name, owner, status)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(uri) DO UPDATE SET
                  type=excluded.type,
                  display_name=excluded.display_name,
                  owner=excluded.owner,
                  status=excluded.status
                """,
                (source.type.value, source.uri, source.display_name, source.owner, source.status.value),
            )
            row = conn.execute("SELECT id FROM sources WHERE uri = ?", (source.uri,)).fetchone()
            return int(row["id"])

    def list_sources(self, status: SourceStatus | None = None) -> list[Source]:
        sql = "SELECT id, type, uri, display_name, owner, status FROM sources"
        params: tuple[str, ...] = ()
        if status is not None:
            sql += " WHERE status = ?"
            params = (status.value,)
        sql += " ORDER BY id"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Source(
                id=int(row["id"]),
                type=SourceType(row["type"]),
                uri=row["uri"],
                display_name=row["display_name"],
                owner=row["owner"],
                status=SourceStatus(row["status"]),
            )
            for row in rows
        ]

    def update_source_status(self, source_id: int, status: SourceStatus) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE sources SET status = ? WHERE id = ?", (status.value, source_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_sqlite_repository.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_sqlite_repository.py
git commit -m "feat: add sqlite repository schema"
```

## Task 6: Local Discovery

**Files:**
- Create: `src/portfolio_maker/infrastructure/local_discovery.py`
- Create: `src/portfolio_maker/application/discovery.py`
- Test: `tests/test_local_discovery.py`

- [ ] **Step 1: Write local discovery tests**

Create `tests/test_local_discovery.py`:

```python
from pathlib import Path

from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.models import DiscoverSourcesRequest
from portfolio_maker.infrastructure.local_discovery import discover_local_candidates


def test_discover_local_candidates_skips_forbidden_and_policy_paths(tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "README.md").write_text("# Project\n", encoding="utf-8")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "secret.md").write_text("secret", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.json").write_text("{}", encoding="utf-8")

    candidates, skipped = discover_local_candidates(tmp_path, forbidden_paths=(tmp_path / "private",))

    assert any(candidate.path.name == "README.md" for candidate in candidates)
    assert any(item.reason == "forbidden" for item in skipped)
    assert any(item.reason == "skipped_policy" for item in skipped)


def test_discover_sources_writes_report_and_sources(workspace, tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "README.md").write_text("# Project\n", encoding="utf-8")

    result = discover_sources(
        DiscoverSourcesRequest(
            workspace=workspace,
            home=tmp_path,
            include_github=False,
            forbidden_paths=(),
        )
    )

    assert result.discovered_count == 1
    assert result.report_path.exists()
    assert "README.md" in result.report_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_local_discovery.py -v
```

Expected: FAIL with missing local discovery modules.

- [ ] **Step 3: Implement local discovery infrastructure**

Create `src/portfolio_maker/infrastructure/local_discovery.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.policy import FilePolicy


TEXT_EXTENSIONS = {".md", ".txt", ".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".toml"}


@dataclass(frozen=True)
class LocalCandidate:
    path: Path
    uri: str
    display_name: str


@dataclass(frozen=True)
class SkippedPath:
    path: Path
    reason: str


def discover_local_candidates(
    home: Path,
    forbidden_paths: tuple[Path, ...] = (),
    max_candidates: int = 500,
) -> tuple[list[LocalCandidate], list[SkippedPath]]:
    policy = FilePolicy(forbidden_paths=forbidden_paths)
    candidates: list[LocalCandidate] = []
    skipped: list[SkippedPath] = []

    for path in sorted(home.rglob("*")):
        classification = policy.classify_path(path)
        if classification != "candidate":
            skipped.append(SkippedPath(path=path, reason=classification))
            if path.is_dir():
                continue
            continue
        if path.is_dir():
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            skipped.append(SkippedPath(path=path, reason="unsupported_extension"))
            continue
        try:
            if path.stat().st_size > policy.max_file_size_bytes:
                skipped.append(SkippedPath(path=path, reason="skipped_policy"))
                continue
        except OSError:
            skipped.append(SkippedPath(path=path, reason="skipped_permission_denied"))
            continue
        candidates.append(LocalCandidate(path=path, uri=path.resolve().as_uri(), display_name=path.name))
        if len(candidates) >= max_candidates:
            break

    return candidates, skipped
```

- [ ] **Step 4: Implement discovery use case**

Create `src/portfolio_maker/application/discovery.py`:

```python
from __future__ import annotations

from portfolio_maker.application.models import DiscoverSourcesRequest, DiscoverSourcesResult, ProgressEvent
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.local_discovery import discover_local_candidates
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def discover_sources(request: DiscoverSourcesRequest) -> DiscoverSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()

    local_candidates, skipped = discover_local_candidates(
        request.home,
        forbidden_paths=request.forbidden_paths,
    )
    for candidate in local_candidates:
        repo.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri=candidate.uri,
                display_name=candidate.display_name,
                owner=None,
                status=SourceStatus.DISCOVERED,
            )
        )

    report_lines = [
        "# Discovery Report",
        "",
        "## Local Candidates",
        "",
    ]
    for candidate in local_candidates:
        report_lines.append(f"- `{candidate.display_name}`: `{candidate.path}`")
    report_lines.extend(["", "## Skipped", ""])
    for item in skipped[:200]:
        report_lines.append(f"- `{item.reason}`: `{item.path.name}`")
    paths.discovery_report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    return DiscoverSourcesResult(
        report_path=paths.discovery_report_path,
        discovered_count=len(local_candidates),
        skipped_count=len(skipped),
        events=(ProgressEvent(stage="discovery", message="local discovery complete", count=len(local_candidates)),),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_local_discovery.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/infrastructure/local_discovery.py src/portfolio_maker/application/discovery.py tests/test_local_discovery.py
git commit -m "feat: add local discovery"
```

## Task 7: Approval File and Gate

**Files:**
- Create: `src/portfolio_maker/application/approval.py`
- Test: `tests/test_approval.py`

- [ ] **Step 1: Write approval tests**

Create `tests/test_approval.py`:

```python
import json

import pytest

from portfolio_maker.application.approval import ApprovalMissingError, load_approval, write_sample_approval
from portfolio_maker.workspace import WorkspacePaths


def test_write_sample_approval_creates_json(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    approval_path = write_sample_approval(paths)

    payload = json.loads(approval_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["approved_source_uris"] == []
    assert payload["forbidden_paths"] == []


def test_load_approval_fails_closed_when_missing(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()

    with pytest.raises(ApprovalMissingError):
        load_approval(paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_approval.py -v
```

Expected: FAIL with missing approval module.

- [ ] **Step 3: Implement approval module**

Create `src/portfolio_maker/application/approval.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.workspace import WorkspacePaths


class ApprovalMissingError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceApproval:
    approved_source_uris: tuple[str, ...]
    forbidden_paths: tuple[str, ...]
    excluded_repositories: tuple[str, ...]
    private_sources_allowed: bool


def sample_approval_payload() -> dict[str, object]:
    return {
        "version": 1,
        "approved_source_uris": [],
        "forbidden_paths": [],
        "excluded_repositories": [],
        "private_sources_allowed": False,
    }


def write_sample_approval(paths: WorkspacePaths) -> Path:
    paths.ensure()
    paths.approval_path.write_text(
        json.dumps(sample_approval_payload(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return paths.approval_path


def load_approval(paths: WorkspacePaths) -> SourceApproval:
    if not paths.approval_path.exists():
        raise ApprovalMissingError(
            f"Approval file is required before ingestion: {paths.approval_path}"
        )
    payload = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    return SourceApproval(
        approved_source_uris=tuple(str(item) for item in payload.get("approved_source_uris", [])),
        forbidden_paths=tuple(str(item) for item in payload.get("forbidden_paths", [])),
        excluded_repositories=tuple(str(item) for item in payload.get("excluded_repositories", [])),
        private_sources_allowed=bool(payload.get("private_sources_allowed", False)),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_approval.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/portfolio_maker/application/approval.py tests/test_approval.py
git commit -m "feat: add source approval gate"
```

## Task 8: Snapshot Store and Text Extraction

**Files:**
- Create: `src/portfolio_maker/infrastructure/extractors.py`
- Create: `src/portfolio_maker/infrastructure/snapshots.py`
- Test: `tests/test_ingestion.py`

- [ ] **Step 1: Write extractor and snapshot tests**

Create `tests/test_ingestion.py`:

```python
import json

from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.snapshots import SnapshotStore
from portfolio_maker.workspace import WorkspacePaths


def test_extract_text_masks_secrets(tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("token=abc123\n# Project\n", encoding="utf-8")

    extracted = extract_text(source)

    assert "# Project" in extracted.text
    assert "abc123" not in extracted.text
    assert extracted.content_hash


def test_snapshot_store_writes_metadata_and_text(workspace, tmp_path):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    source = tmp_path / "README.md"
    source.write_text("# Demo\n", encoding="utf-8")
    extracted = extract_text(source)
    store = SnapshotStore(paths)

    snapshot_path = store.write_local_snapshot(source_id=7, source_path=source, extracted=extracted)

    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == 7
    assert payload["source_uri"].startswith("file://")
    assert payload["text"] == "# Demo\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_ingestion.py -v
```

Expected: FAIL with missing extractor and snapshot modules.

- [ ] **Step 3: Implement text extractor**

Create `src/portfolio_maker/infrastructure/extractors.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from portfolio_maker.infrastructure.policy import mask_secrets


@dataclass(frozen=True)
class ExtractedText:
    text: str
    content_hash: str
    extractor: str


def extract_text(path: Path) -> ExtractedText:
    raw = path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    return ExtractedText(
        text=mask_secrets(text),
        content_hash=content_hash,
        extractor="text-v1",
    )
```

- [ ] **Step 4: Implement snapshot store**

Create `src/portfolio_maker/infrastructure/snapshots.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from portfolio_maker.infrastructure.extractors import ExtractedText
from portfolio_maker.workspace import WorkspacePaths


class SnapshotStore:
    def __init__(self, paths: WorkspacePaths) -> None:
        self.paths = paths

    def write_local_snapshot(self, source_id: int, source_path: Path, extracted: ExtractedText) -> Path:
        self.paths.ensure()
        snapshot_path = self.paths.local_snapshots_dir / f"source-{source_id}.json"
        payload = {
            "source_id": source_id,
            "source_uri": source_path.resolve().as_uri(),
            "display_name": source_path.name,
            "content_hash": extracted.content_hash,
            "extractor": extracted.extractor,
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "text": extracted.text,
        }
        snapshot_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return snapshot_path
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_ingestion.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/infrastructure/extractors.py src/portfolio_maker/infrastructure/snapshots.py tests/test_ingestion.py
git commit -m "feat: add snapshot extraction"
```

## Task 9: GitHub Connector

**Files:**
- Create: `src/portfolio_maker/infrastructure/github_connector.py`
- Create: `tests/fixtures/github/gh_repo_list.json`
- Create: `tests/fixtures/github/gh_commit_list.json`
- Create: `tests/fixtures/github/gh_pr_list.json`
- Create: `tests/fixtures/github/gh_issue_list.json`
- Create: `tests/fixtures/github/gh_review_list.json`
- Create: `tests/fixtures/github/gh_workflow_run_list.json`
- Test: `tests/test_github_connector.py`

- [ ] **Step 1: Create GitHub fixtures**

Create `tests/fixtures/github/gh_repo_list.json`:

```json
[
  {
    "nameWithOwner": "octo/demo",
    "url": "https://github.com/octo/demo",
    "isPrivate": false,
    "description": "Demo portfolio project",
    "primaryLanguage": {"name": "Python"}
  }
]
```

Create `tests/fixtures/github/gh_pr_list.json`:

```json
[
  {
    "title": "Add RAG ingestion",
    "url": "https://github.com/octo/demo/pull/1",
    "state": "MERGED",
    "createdAt": "2026-01-01T00:00:00Z",
    "mergedAt": "2026-01-02T00:00:00Z",
    "author": {"login": "octo"}
  }
]
```

Create `tests/fixtures/github/gh_commit_list.json`:

```json
[
  {
    "sha": "abc123",
    "html_url": "https://github.com/octo/demo/commit/abc123",
    "commit": {
      "message": "Implement ingestion pipeline",
      "author": {
        "name": "octo",
        "date": "2026-01-04T00:00:00Z"
      }
    }
  }
]
```

Create `tests/fixtures/github/gh_issue_list.json`:

```json
[
  {
    "title": "Improve portfolio export",
    "url": "https://github.com/octo/demo/issues/2",
    "state": "CLOSED",
    "createdAt": "2026-01-03T00:00:00Z",
    "author": {"login": "octo"}
  }
]
```

Create `tests/fixtures/github/gh_review_list.json`:

```json
[
  {
    "pullRequest": {
      "title": "Add RAG ingestion",
      "url": "https://github.com/octo/demo/pull/1"
    },
    "state": "APPROVED",
    "author": {"login": "octo"},
    "submittedAt": "2026-01-06T00:00:00Z"
  }
]
```

Create `tests/fixtures/github/gh_workflow_run_list.json`:

```json
{
  "workflow_runs": [
    {
      "name": "CI",
      "html_url": "https://github.com/octo/demo/actions/runs/10",
      "status": "completed",
      "conclusion": "success",
      "created_at": "2026-01-05T00:00:00Z",
      "actor": {"login": "octo"}
    }
  ]
}
```

- [ ] **Step 2: Write GitHub connector tests**

Create `tests/test_github_connector.py`:

```python
import json
from pathlib import Path

from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubRepositoryCandidate,
    parse_commit_list,
    parse_issue_list,
    parse_pr_list,
    parse_review_list,
    parse_repo_list,
    parse_workflow_run_list,
)


def load_fixture(name: str):
    path = Path("tests/fixtures/github") / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_parse_repo_list():
    repos = parse_repo_list(load_fixture("gh_repo_list.json"))

    assert repos == [
        GitHubRepositoryCandidate(
            name_with_owner="octo/demo",
            url="https://github.com/octo/demo",
            is_private=False,
            description="Demo portfolio project",
            primary_language="Python",
        )
    ]


def test_parse_pr_and_issue_lists():
    prs = parse_pr_list("octo/demo", load_fixture("gh_pr_list.json"))
    issues = parse_issue_list("octo/demo", load_fixture("gh_issue_list.json"))

    assert prs[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="pull_request",
        url="https://github.com/octo/demo/pull/1",
        title="Add RAG ingestion",
        state="MERGED",
        author="octo",
        created_at="2026-01-01T00:00:00Z",
        merged_at="2026-01-02T00:00:00Z",
    )
    assert issues[0].activity_type == "issue"


def test_parse_commit_review_and_workflow_run_lists():
    commits = parse_commit_list("octo/demo", load_fixture("gh_commit_list.json"))
    reviews = parse_review_list("octo/demo", load_fixture("gh_review_list.json"))
    runs = parse_workflow_run_list("octo/demo", load_fixture("gh_workflow_run_list.json"))

    assert commits[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="commit",
        url="https://github.com/octo/demo/commit/abc123",
        title="Implement ingestion pipeline",
        state="committed",
        author="octo",
        created_at="2026-01-04T00:00:00Z",
        merged_at=None,
    )
    assert reviews[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="review",
        url="https://github.com/octo/demo/pull/1",
        title="Review: Add RAG ingestion",
        state="APPROVED",
        author="octo",
        created_at="2026-01-06T00:00:00Z",
        merged_at=None,
    )
    assert runs[0] == GitHubActivityCandidate(
        repo="octo/demo",
        activity_type="workflow_run",
        url="https://github.com/octo/demo/actions/runs/10",
        title="CI",
        state="success",
        author="octo",
        created_at="2026-01-05T00:00:00Z",
        merged_at=None,
    )
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
pytest tests/test_github_connector.py -v
```

Expected: FAIL with missing GitHub connector module.

- [ ] **Step 4: Implement GitHub connector parser**

Create `src/portfolio_maker/infrastructure/github_connector.py`:

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GitHubRepositoryCandidate:
    name_with_owner: str
    url: str
    is_private: bool
    description: str
    primary_language: str | None


@dataclass(frozen=True)
class GitHubActivityCandidate:
    repo: str
    activity_type: str
    url: str
    title: str
    state: str
    author: str
    created_at: str
    merged_at: str | None


def parse_repo_list(payload: list[dict]) -> list[GitHubRepositoryCandidate]:
    repos: list[GitHubRepositoryCandidate] = []
    for item in payload:
        language = item.get("primaryLanguage") or {}
        repos.append(
            GitHubRepositoryCandidate(
                name_with_owner=item["nameWithOwner"],
                url=item["url"],
                is_private=bool(item.get("isPrivate", False)),
                description=item.get("description") or "",
                primary_language=language.get("name"),
            )
        )
    return repos


def parse_pr_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="pull_request",
            url=item["url"],
            title=item["title"],
            state=item["state"],
            author=(item.get("author") or {}).get("login", ""),
            created_at=item["createdAt"],
            merged_at=item.get("mergedAt"),
        )
        for item in payload
    ]


def parse_issue_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    return [
        GitHubActivityCandidate(
            repo=repo,
            activity_type="issue",
            url=item["url"],
            title=item["title"],
            state=item["state"],
            author=(item.get("author") or {}).get("login", ""),
            created_at=item["createdAt"],
            merged_at=None,
        )
        for item in payload
    ]


def parse_commit_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in payload:
        commit = item.get("commit") or {}
        author = commit.get("author") or {}
        message = str(commit.get("message") or "").splitlines()[0]
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="commit",
                url=item.get("html_url") or "",
                title=message,
                state="committed",
                author=author.get("name") or "",
                created_at=author.get("date") or "",
                merged_at=None,
            )
        )
    return activities


def parse_review_list(repo: str, payload: list[dict]) -> list[GitHubActivityCandidate]:
    activities: list[GitHubActivityCandidate] = []
    for item in payload:
        pull_request = item.get("pullRequest") or {}
        user = item.get("user") or {}
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="review",
                url=pull_request.get("url") or item.get("html_url") or "",
                title=f"Review: {pull_request.get('title') or item.get('body') or 'pull request'}",
                state=item.get("state") or "",
                author=(item.get("author") or user).get("login", ""),
                created_at=item.get("submittedAt") or item.get("created_at") or "",
                merged_at=None,
            )
        )
    return activities


def parse_workflow_run_list(repo: str, payload: dict) -> list[GitHubActivityCandidate]:
    runs = payload.get("workflow_runs", [])
    activities: list[GitHubActivityCandidate] = []
    for item in runs:
        actor = item.get("actor") or {}
        activities.append(
            GitHubActivityCandidate(
                repo=repo,
                activity_type="workflow_run",
                url=item.get("html_url") or "",
                title=item.get("name") or "workflow",
                state=item.get("conclusion") or item.get("status") or "",
                author=actor.get("login") or "",
                created_at=item.get("created_at") or "",
                merged_at=None,
            )
        )
    return activities


def run_gh_json(args: list[str]) -> Any:
    completed = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_github_connector.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/infrastructure/github_connector.py tests/fixtures/github tests/test_github_connector.py
git commit -m "feat: add github activity parsing"
```

## Task 10: GitHub Discovery Integration

**Files:**
- Modify: `src/portfolio_maker/infrastructure/github_connector.py`
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Modify: `src/portfolio_maker/application/discovery.py`
- Modify: `tests/test_local_discovery.py`

- [ ] **Step 1: Add GitHub discovery integration test**

Append to `tests/test_local_discovery.py`:

```python
from portfolio_maker.infrastructure.github_connector import (
    GitHubActivityCandidate,
    GitHubRepositoryCandidate,
)
from portfolio_maker.domain.models import SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def test_discover_sources_includes_github_candidates(workspace, tmp_path, monkeypatch):
    def fake_discover_github_candidates():
        return (
            [
                GitHubRepositoryCandidate(
                    name_with_owner="octo/demo",
                    url="https://github.com/octo/demo",
                    is_private=False,
                    description="Demo portfolio project",
                    primary_language="Python",
                )
            ],
            [
                GitHubActivityCandidate(
                    repo="octo/demo",
                    activity_type="pull_request",
                    url="https://github.com/octo/demo/pull/1",
                    title="Add RAG ingestion",
                    state="MERGED",
                    author="octo",
                    created_at="2026-01-01T00:00:00Z",
                    merged_at="2026-01-02T00:00:00Z",
                )
            ],
        )

    monkeypatch.setattr(
        "portfolio_maker.application.discovery.discover_github_candidates",
        fake_discover_github_candidates,
    )

    result = discover_sources(
        DiscoverSourcesRequest(
            workspace=workspace,
            home=tmp_path,
            include_github=True,
            forbidden_paths=(),
        )
    )

    paths = WorkspacePaths.from_root(workspace)
    repo = SQLiteRepository(paths.db_path)
    sources = repo.list_sources()
    report = result.report_path.read_text(encoding="utf-8")

    assert any(source.type == SourceType.GITHUB_REPOSITORY for source in sources)
    assert "octo/demo" in report
    assert "Add RAG ingestion" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_local_discovery.py::test_discover_sources_includes_github_candidates -v
```

Expected: FAIL because `discover_github_candidates` is not imported into the discovery use case.

- [ ] **Step 3: Add GitHub activity repository method**

Append this import to `src/portfolio_maker/infrastructure/sqlite_repository.py`:

```python
from portfolio_maker.domain.models import GitHubActivity
```

If the file already imports several domain models in one line, change that import to:

```python
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
```

Append this method to `SQLiteRepository`:

```python
    def insert_github_activity(self, activity: GitHubActivity) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO github_activities
                  (source_id, repo, activity_type, url, title, state, author, created_at, merged_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity.source_id,
                    activity.repo,
                    activity.activity_type,
                    activity.url,
                    activity.title,
                    activity.state,
                    activity.author,
                    activity.created_at,
                    activity.merged_at,
                ),
            )
            return int(cursor.lastrowid)
```

- [ ] **Step 4: Add GitHub discovery command wrapper**

Append to `src/portfolio_maker/infrastructure/github_connector.py`:

```python
def discover_github_candidates() -> tuple[list[GitHubRepositoryCandidate], list[GitHubActivityCandidate]]:
    repo_payload = run_gh_json(
        [
            "repo",
            "list",
            "--json",
            "nameWithOwner,url,isPrivate,description,primaryLanguage",
            "--limit",
            "100",
        ]
    )
    repos = parse_repo_list(repo_payload)
    activities: list[GitHubActivityCandidate] = []
    for repo in repos:
        pr_payload = run_gh_json(
            [
                "pr",
                "list",
                "--repo",
                repo.name_with_owner,
                "--state",
                "all",
                "--json",
                "title,url,state,createdAt,mergedAt,author",
                "--limit",
                "100",
            ]
        )
        commit_payload = run_gh_json(
            [
                "api",
                f"repos/{repo.name_with_owner}/commits",
                "--paginate",
            ]
        )
        issue_payload = run_gh_json(
            [
                "issue",
                "list",
                "--repo",
                repo.name_with_owner,
                "--state",
                "all",
                "--json",
                "title,url,state,createdAt,author",
                "--limit",
                "100",
            ]
        )
        review_payload = run_gh_json(
            [
                "api",
                f"repos/{repo.name_with_owner}/pulls/comments",
                "--paginate",
            ]
        )
        workflow_payload = run_gh_json(
            [
                "api",
                f"repos/{repo.name_with_owner}/actions/runs",
            ]
        )
        activities.extend(parse_commit_list(repo.name_with_owner, commit_payload))
        activities.extend(parse_pr_list(repo.name_with_owner, pr_payload))
        activities.extend(parse_issue_list(repo.name_with_owner, issue_payload))
        activities.extend(parse_review_list(repo.name_with_owner, review_payload))
        activities.extend(parse_workflow_run_list(repo.name_with_owner, workflow_payload))
    return repos, activities
```

- [ ] **Step 5: Integrate GitHub discovery into use case**

Modify imports in `src/portfolio_maker/application/discovery.py`:

```python
from portfolio_maker.domain.models import GitHubActivity, Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.github_connector import discover_github_candidates
```

Inside `discover_sources`, after local candidates are inserted and before report writing, add:

```python
    github_repos = []
    github_activities = []
    if request.include_github:
        github_repos, github_activities = discover_github_candidates()
        for github_repo in github_repos:
            repo.upsert_source(
                Source(
                    id=None,
                    type=SourceType.GITHUB_REPOSITORY,
                    uri=github_repo.url,
                    display_name=github_repo.name_with_owner,
                    owner=github_repo.name_with_owner.split("/", 1)[0],
                    status=SourceStatus.DISCOVERED,
                )
            )
        for activity in github_activities:
            repo.insert_github_activity(
                GitHubActivity(
                    id=None,
                    source_id=None,
                    repo=activity.repo,
                    activity_type=activity.activity_type,
                    url=activity.url,
                    title=activity.title,
                    state=activity.state,
                    author=activity.author,
                    created_at=activity.created_at,
                    merged_at=activity.merged_at,
                )
            )
```

Then add GitHub sections to the report before the skipped section:

```python
    report_lines.extend(["", "## GitHub Repositories", ""])
    for github_repo in github_repos:
        visibility = "private" if github_repo.is_private else "public"
        report_lines.append(f"- `{github_repo.name_with_owner}` ({visibility}): {github_repo.url}")
    report_lines.extend(["", "## GitHub Activities", ""])
    for activity in github_activities:
        report_lines.append(f"- `{activity.activity_type}` `{activity.repo}`: {activity.title} {activity.url}")
```

Update the returned `discovered_count` to include GitHub repositories and activities:

```python
    total_discovered = len(local_candidates) + len(github_repos) + len(github_activities)
```

Return `discovered_count=total_discovered`.

- [ ] **Step 6: Run GitHub integration test**

Run:

```bash
pytest tests/test_local_discovery.py::test_discover_sources_includes_github_candidates -v
```

Expected: PASS.

- [ ] **Step 7: Run connector and discovery tests**

Run:

```bash
pytest tests/test_github_connector.py tests/test_local_discovery.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/portfolio_maker/infrastructure/github_connector.py src/portfolio_maker/infrastructure/sqlite_repository.py src/portfolio_maker/application/discovery.py tests/test_local_discovery.py
git commit -m "feat: include github candidates in discovery"
```

## Task 11: Ingestion Use Case

**Files:**
- Modify: `src/portfolio_maker/infrastructure/sqlite_repository.py`
- Create: `src/portfolio_maker/application/ingestion.py`
- Modify: `tests/test_ingestion.py`

- [ ] **Step 1: Add ingestion gate test**

Append to `tests/test_ingestion.py`:

```python
import json

import pytest

from portfolio_maker.application.approval import ApprovalMissingError
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import IngestSourcesRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository


def test_ingest_sources_requires_approval(workspace, tmp_path):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()
    source = tmp_path / "README.md"
    source.write_text("# Demo\n", encoding="utf-8")
    repo.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source.resolve().as_uri(),
            display_name="README.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    with pytest.raises(ApprovalMissingError):
        ingest_sources(IngestSourcesRequest(workspace=workspace))


def test_ingest_sources_writes_snapshot_for_approved_source(workspace, tmp_path):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()
    source = tmp_path / "README.md"
    source.write_text("# Demo\n", encoding="utf-8")
    uri = source.resolve().as_uri()
    repo.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=uri,
            display_name="README.md",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    paths.approval_path.write_text(
        json.dumps(
            {
                "version": 1,
                "approved_source_uris": [uri],
                "forbidden_paths": [],
                "excluded_repositories": [],
                "private_sources_allowed": False,
            }
        ),
        encoding="utf-8",
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    assert result.snapshot_paths[0].exists()
    assert repo.list_sources()[0].status == SourceStatus.INGESTED
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_ingestion.py -v
```

Expected: FAIL with missing ingestion use case or missing snapshot repository methods.

- [ ] **Step 3: Add snapshot repository method**

Append these methods to `SQLiteRepository` in `src/portfolio_maker/infrastructure/sqlite_repository.py`:

```python
    def insert_source_snapshot(
        self,
        source_id: int,
        snapshot_path: str,
        content_hash: str,
        extractor: str,
    ) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO source_snapshots (source_id, snapshot_path, content_hash, extractor)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, snapshot_path, content_hash, extractor),
            )
            return int(cursor.lastrowid)
```

- [ ] **Step 4: Implement ingestion use case**

Create `src/portfolio_maker/application/ingestion.py`:

```python
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse, unquote

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.snapshots import SnapshotStore
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Only file URIs can be converted to paths: {uri}")
    return Path(unquote(parsed.path))


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()
    store = SnapshotStore(paths)

    ingested = 0
    skipped = 0
    snapshot_paths: list[Path] = []

    for source in repo.list_sources():
        if source.uri not in approved_uris:
            skipped += 1
            continue
        if source.type != SourceType.LOCAL_FILE:
            skipped += 1
            continue
        source_path = _path_from_file_uri(source.uri)
        extracted = extract_text(source_path)
        snapshot_path = store.write_local_snapshot(source.id or 0, source_path, extracted)
        repo.insert_source_snapshot(
            source_id=source.id or 0,
            snapshot_path=str(snapshot_path),
            content_hash=extracted.content_hash,
            extractor=extracted.extractor,
        )
        repo.update_source_status(source.id or 0, SourceStatus.INGESTED)
        snapshot_paths.append(snapshot_path)
        ingested += 1

    return IngestSourcesResult(
        ingested_count=ingested,
        skipped_count=skipped,
        snapshot_paths=tuple(snapshot_paths),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
pytest tests/test_ingestion.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/application/ingestion.py src/portfolio_maker/infrastructure/sqlite_repository.py tests/test_ingestion.py
git commit -m "feat: ingest approved local sources"
```

## Task 12: Master Profile and Portfolio Artifacts

**Files:**
- Create: `src/portfolio_maker/infrastructure/artifacts.py`
- Create: `src/portfolio_maker/application/build_profile.py`
- Create: `src/portfolio_maker/application/draft_portfolio.py`
- Test: `tests/test_artifacts.py`
- Test: `tests/test_profile_and_portfolio.py`

- [ ] **Step 1: Write artifact tests**

Create `tests/test_artifacts.py`:

```python
import json

from portfolio_maker.infrastructure.artifacts import write_json, write_markdown


def test_write_json_and_markdown(workspace):
    json_path = workspace / "profile.json"
    md_path = workspace / "profile.md"

    write_json(json_path, {"name": "Demo"})
    write_markdown(md_path, "# Demo\n")

    assert json.loads(json_path.read_text(encoding="utf-8")) == {"name": "Demo"}
    assert md_path.read_text(encoding="utf-8") == "# Demo\n"
```

Create `tests/test_profile_and_portfolio.py`:

```python
import json

from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.models import BuildProfileRequest, DraftPortfolioRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def test_build_profile_and_draft_portfolio_from_ingested_sources(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()
    repo.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///tmp/demo/README.md",
            display_name="README.md",
            owner=None,
            status=SourceStatus.INGESTED,
        )
    )

    profile_result = build_profile(BuildProfileRequest(workspace=workspace))
    portfolio_result = draft_portfolio(DraftPortfolioRequest(workspace=workspace))

    profile = json.loads(profile_result.json_path.read_text(encoding="utf-8"))
    assert profile["sources"][0]["display_name"] == "README.md"
    assert profile_result.markdown_path.exists()
    assert portfolio_result.markdown_path.exists()
    assert "README.md" in portfolio_result.markdown_path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_artifacts.py tests/test_profile_and_portfolio.py -v
```

Expected: FAIL with missing artifact/profile modules.

- [ ] **Step 3: Implement artifact writers**

Create `src/portfolio_maker/infrastructure/artifacts.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_markdown(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
```

- [ ] **Step 4: Implement profile builder**

Create `src/portfolio_maker/application/build_profile.py`:

```python
from __future__ import annotations

from portfolio_maker.application.models import BuildProfileRequest, BuildProfileResult
from portfolio_maker.domain.models import SourceStatus
from portfolio_maker.infrastructure.artifacts import write_json, write_markdown
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.workspace import WorkspacePaths


def build_profile(request: BuildProfileRequest) -> BuildProfileResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    repo = SQLiteRepository(paths.db_path)
    repo.initialize()
    ingested_sources = repo.list_sources(status=SourceStatus.INGESTED)
    payload = {
        "version": 1,
        "sources": [
            {
                "type": source.type.value,
                "uri": source.uri,
                "display_name": source.display_name,
                "status": source.status.value,
            }
            for source in ingested_sources
        ],
        "claims": [
            {
                "claim_type": "project_evidence",
                "text": f"User has portfolio evidence in {source.display_name}.",
                "confidence": "medium",
                "public_safe": False,
                "evidence_uri": source.uri,
            }
            for source in ingested_sources
        ],
    }
    markdown_lines = ["# Master Profile", "", "## Sources", ""]
    for source in ingested_sources:
        markdown_lines.append(f"- `{source.display_name}` ({source.type.value})")
    markdown_lines.extend(["", "## Claims", ""])
    for claim in payload["claims"]:
        markdown_lines.append(f"- {claim['text']} Evidence: `{claim['evidence_uri']}`")

    write_json(paths.master_profile_json_path, payload)
    write_markdown(paths.master_profile_md_path, "\n".join(markdown_lines) + "\n")
    return BuildProfileResult(
        json_path=paths.master_profile_json_path,
        markdown_path=paths.master_profile_md_path,
        claim_count=len(payload["claims"]),
    )
```

- [ ] **Step 5: Implement portfolio drafter**

Create `src/portfolio_maker/application/draft_portfolio.py`:

```python
from __future__ import annotations

import json

from portfolio_maker.application.models import DraftPortfolioRequest, DraftPortfolioResult
from portfolio_maker.infrastructure.artifacts import write_markdown
from portfolio_maker.workspace import WorkspacePaths


def draft_portfolio(request: DraftPortfolioRequest) -> DraftPortfolioResult:
    paths = WorkspacePaths.from_root(request.workspace)
    profile = json.loads(paths.master_profile_json_path.read_text(encoding="utf-8"))
    sources = profile.get("sources", [])
    lines = ["# Portfolio Draft", "", "## Projects", ""]
    for source in sources:
        display_name = source["display_name"]
        lines.extend(
            [
                f"### {display_name}",
                "",
                "This project is included because approved local evidence was found.",
                "",
                "- Role: Evidence review required",
                "- Technical approach: Evidence review required",
                "- Outcome: Evidence review required",
                "",
                f"Internal evidence reference: `{display_name}`",
                "",
            ]
        )
    write_markdown(paths.portfolio_draft_path, "\n".join(lines))
    return DraftPortfolioResult(markdown_path=paths.portfolio_draft_path, project_count=len(sources))
```

- [ ] **Step 6: Run tests to verify they pass**

Run:

```bash
pytest tests/test_artifacts.py tests/test_profile_and_portfolio.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/portfolio_maker/infrastructure/artifacts.py src/portfolio_maker/application/build_profile.py src/portfolio_maker/application/draft_portfolio.py tests/test_artifacts.py tests/test_profile_and_portfolio.py
git commit -m "feat: generate profile and portfolio artifacts"
```

## Task 13: CLI Adapter

**Files:**
- Create: `src/portfolio_maker/adapters/cli.py`
- Modify: `src/portfolio_maker/__main__.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write CLI tests**

Create `tests/test_cli.py`:

```python
from portfolio_maker.adapters.cli import main


def test_cli_discover_command_creates_report(workspace, tmp_path):
    (tmp_path / "project").mkdir()
    (tmp_path / "project" / "README.md").write_text("# Demo\n", encoding="utf-8")

    exit_code = main(["discover", "--workspace", str(workspace), "--home", str(tmp_path), "--no-github"])

    assert exit_code == 0
    assert (workspace / ".portfolio-maker" / "reviews" / "discovery-report.md").exists()


def test_cli_approve_write_sample(workspace):
    exit_code = main(["approve", "--workspace", str(workspace), "--write-sample"])

    assert exit_code == 0
    assert (workspace / ".portfolio-maker" / "reviews" / "source-approval.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: FAIL with missing CLI module.

- [ ] **Step 3: Implement CLI adapter**

Create `src/portfolio_maker/adapters/cli.py`:

```python
from __future__ import annotations

import argparse
from pathlib import Path

from portfolio_maker.application.approval import write_sample_approval
from portfolio_maker.application.build_profile import build_profile
from portfolio_maker.application.discovery import discover_sources
from portfolio_maker.application.draft_portfolio import draft_portfolio
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import (
    BuildProfileRequest,
    DiscoverSourcesRequest,
    DraftPortfolioRequest,
    IngestSourcesRequest,
)
from portfolio_maker.workspace import WorkspacePaths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portfolio-maker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover")
    discover.add_argument("--workspace", type=Path, default=Path("."))
    discover.add_argument("--home", type=Path, default=Path.home())
    discover.add_argument("--no-github", action="store_true")
    discover.add_argument("--forbidden-path", type=Path, action="append", default=[])

    approve = subparsers.add_parser("approve")
    approve.add_argument("--workspace", type=Path, default=Path("."))
    approve.add_argument("--write-sample", action="store_true")

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--workspace", type=Path, default=Path("."))

    build_profile_parser = subparsers.add_parser("build-profile")
    build_profile_parser.add_argument("--workspace", type=Path, default=Path("."))

    draft = subparsers.add_parser("draft-portfolio")
    draft.add_argument("--workspace", type=Path, default=Path("."))

    run_mvp = subparsers.add_parser("run-mvp")
    run_mvp.add_argument("--workspace", type=Path, default=Path("."))
    run_mvp.add_argument("--home", type=Path, default=Path.home())
    run_mvp.add_argument("--no-github", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "discover":
        result = discover_sources(
            DiscoverSourcesRequest(
                workspace=args.workspace,
                home=args.home,
                include_github=not args.no_github,
                forbidden_paths=tuple(args.forbidden_path),
            )
        )
        print(f"Discovery report: {result.report_path}")
        print(f"Discovered: {result.discovered_count}, skipped: {result.skipped_count}")
        return 0

    if args.command == "approve":
        paths = WorkspacePaths.from_root(args.workspace)
        if args.write_sample:
            approval_path = write_sample_approval(paths)
            print(f"Sample approval file written: {approval_path}")
            return 0
        print(f"Approval file path: {paths.approval_path}")
        return 0

    if args.command == "ingest":
        result = ingest_sources(IngestSourcesRequest(workspace=args.workspace))
        print(f"Ingested: {result.ingested_count}, skipped: {result.skipped_count}")
        return 0

    if args.command == "build-profile":
        result = build_profile(BuildProfileRequest(workspace=args.workspace))
        print(f"Master profile JSON: {result.json_path}")
        print(f"Master profile Markdown: {result.markdown_path}")
        return 0

    if args.command == "draft-portfolio":
        result = draft_portfolio(DraftPortfolioRequest(workspace=args.workspace))
        print(f"Portfolio draft: {result.markdown_path}")
        return 0

    if args.command == "run-mvp":
        discover_result = discover_sources(
            DiscoverSourcesRequest(
                workspace=args.workspace,
                home=args.home,
                include_github=not args.no_github,
            )
        )
        print(f"Discovery report: {discover_result.report_path}")
        print("Review and approve sources before running ingestion.")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

Run:

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/portfolio_maker/adapters/cli.py src/portfolio_maker/__main__.py tests/test_cli.py
git commit -m "feat: add portfolio maker cli"
```

## Task 14: Codex Skill Workflow

**Files:**
- Create: `.agents/skills/portfolio-maker/SKILL.md`

- [ ] **Step 1: Create Codex skill**

Create `.agents/skills/portfolio-maker/SKILL.md`:

```markdown
---
name: portfolio-maker
description: Use when generating a local evidence-based career profile or portfolio draft from approved local files in this repository. GitHub activity is discovery-only in this MVP.
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
```

- [ ] **Step 2: Verify skill file contains frontmatter**

Run:

```bash
sed -n '1,40p' .agents/skills/portfolio-maker/SKILL.md
```

Expected output starts with:

```text
---
name: portfolio-maker
description:
```

- [ ] **Step 3: Commit**

```bash
git add .agents/skills/portfolio-maker/SKILL.md
git commit -m "feat: add portfolio maker codex skill"
```

## Task 15: Documentation and End-to-End Verification

**Files:**
- Modify: `README.md`
- Test: full test suite and manual fixture run

- [ ] **Step 1: Extend README with safe workflow**

Replace `README.md` with:

```markdown
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
```

- [ ] **Step 2: Run full tests**

Run:

```bash
pytest -v
```

Expected: PASS.

- [ ] **Step 3: Run manual fixture workflow**

Run:

```bash
tmp_home=$(mktemp -d)
mkdir -p "$tmp_home/demo"
printf '# Demo Project\nImplemented ingestion and portfolio export.\n' > "$tmp_home/demo/README.md"
portfolio-maker discover --workspace . --home "$tmp_home" --no-github
portfolio-maker approve --workspace . --write-sample
```

Edit `.portfolio-maker/reviews/source-approval.json` so `approved_source_uris` contains the discovered `file://` URI from `.portfolio-maker/reviews/discovery-report.md`.

Then run:

```bash
portfolio-maker ingest --workspace .
portfolio-maker build-profile --workspace .
portfolio-maker draft-portfolio --workspace .
```

Expected files:

```text
.portfolio-maker/artifacts/master-profile.json
.portfolio-maker/artifacts/master-profile.md
.portfolio-maker/artifacts/portfolio-draft.md
```

- [ ] **Step 4: Inspect public artifact for unsafe strings**

Run:

```bash
rg -n "ghp_|token=|password=|/Users/" .portfolio-maker/artifacts/portfolio-draft.md
```

Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: add mvp setup and workflow guide"
```

## Final Verification Checklist

- [ ] `pytest -v` passes.
- [ ] `portfolio-maker discover --workspace . --no-github` creates `.portfolio-maker/reviews/discovery-report.md`.
- [ ] `portfolio-maker ingest --workspace .` fails when approval is missing.
- [ ] Approved local files generate snapshots under `.portfolio-maker/raw/snapshots/local/`.
- [ ] `portfolio-maker build-profile --workspace .` creates master profile JSON and Markdown.
- [ ] `portfolio-maker draft-portfolio --workspace .` creates portfolio draft Markdown.
- [ ] Public portfolio draft contains no detected token, password, or private raw path.
- [ ] `.portfolio-maker/` is ignored by Git.
- [ ] `.agents/skills/portfolio-maker/SKILL.md` exists and documents the approval gate.
- [ ] `git status --short` shows only intentional source, test, doc, and skill changes before each commit.
