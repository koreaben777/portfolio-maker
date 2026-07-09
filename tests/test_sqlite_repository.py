import json

from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.audit import AuditEvent, AuditLog
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
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
    assert not paths.db_path.exists()
    assert not paths.audit_log_path.exists()
    assert not paths.master_profile_json_path.exists()
    assert not paths.master_profile_md_path.exists()
    assert not paths.portfolio_draft_path.exists()


def test_audit_log_write_records_jsonl_event(workspace):
    paths = WorkspacePaths.from_root(workspace)
    paths.ensure()
    audit_log = AuditLog(paths.audit_log_path)

    audit_log.write(
        AuditEvent(
            event_type="workspace.initialized",
            message="Workspace ready",
            data={"workspace": str(paths.workspace)},
        )
    )

    lines = paths.audit_log_path.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["event_type"] == "workspace.initialized"
    assert payload["message"] == "Workspace ready"
    assert payload["data"] == {"workspace": str(paths.workspace)}
    assert "created_at" in payload
    assert set(payload) == {"event_type", "message", "data", "created_at"}


def test_sqlite_repository_initialize_creates_schema_tables(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)

    repository.initialize()

    assert {
        "sources",
        "source_snapshots",
        "evidence_items",
        "github_activities",
        "career_claims",
    } <= repository.table_names()


def test_sqlite_repository_upsert_source_lists_inserted_source(workspace):
    paths = WorkspacePaths.from_root(workspace)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()

    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_DIRECTORY,
            uri="/workspace/project",
            display_name="project",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    sources = repository.list_sources()

    assert source_id == 1
    assert len(sources) == 1
    assert sources[0].id == 1
    assert sources[0].uri == "/workspace/project"
