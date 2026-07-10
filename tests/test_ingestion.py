from __future__ import annotations

import hashlib
import json

import pytest

from portfolio_maker.application.approval import ApprovalMissingError, write_sample_approval
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import IngestSourcesRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import SnapshotStore
from portfolio_maker.workspace import WorkspacePaths


def test_extract_text_masks_secrets_and_hashes_raw_bytes(tmp_path):
    source = tmp_path / "note.txt"
    raw = b"hello\napi_key=secret123\nbad:\xff"
    source.write_bytes(raw)

    extracted = extract_text(source)

    assert extracted.text == "hello\napi_key=[REDACTED]\nbad:\ufffd"
    assert extracted.content_hash == hashlib.sha256(raw).hexdigest()
    assert extracted.extractor == "text-v1"


def test_extract_text_masks_unquoted_multiword_secret_values(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text(
        "password: my secret value\nOPENAI_API_KEY=my secret value",
        encoding="utf-8",
    )

    extracted = extract_text(source)

    assert extracted.text == "password: [REDACTED]\nOPENAI_API_KEY=[REDACTED]"


def test_snapshot_store_writes_local_snapshot_json(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("password: hidden", encoding="utf-8")
    extracted = extract_text(source)
    paths = WorkspacePaths.from_root(tmp_path / "workspace")

    snapshot_path = SnapshotStore(paths).write_local_snapshot(7, source, extracted)

    assert snapshot_path == paths.local_snapshots_dir / "source-7.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == 7
    assert payload["source_uri"] == source.resolve().as_uri()
    assert payload["display_name"] == "note.txt"
    assert payload["content_hash"] == extracted.content_hash
    assert payload["extractor"] == "text-v1"
    assert payload["text"] == "password: [REDACTED]"
    assert payload["extracted_at"].endswith("Z")


def test_ingest_sources_raises_when_approval_file_missing(tmp_path):
    with pytest.raises(ApprovalMissingError):
        ingest_sources(IngestSourcesRequest(workspace=tmp_path))


def test_ingest_sources_ingests_approved_local_file(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("hello\napi_key=secret123", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    assert result.skipped_count == 0
    assert result.snapshot_paths == (paths.local_snapshots_dir / f"source-{source_id}.json",)
    assert repository.list_sources()[0].status == SourceStatus.INGESTED
    payload = json.loads(result.snapshot_paths[0].read_text(encoding="utf-8"))
    assert payload["text"] == "hello\napi_key=[REDACTED]"
    with repository.connect() as conn:
        row = conn.execute("SELECT source_id, snapshot_path FROM source_snapshots").fetchone()
    assert row["source_id"] == source_id
    assert row["snapshot_path"] == str(result.snapshot_paths[0])


def test_ingest_sources_skips_unapproved_local_and_approved_github_sources(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("hello", encoding="utf-8")
    github_uri = "https://github.com/example/project"
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [github_uri]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.GITHUB_REPOSITORY,
            uri=github_uri,
            display_name="example/project",
            owner="example",
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 2
    assert result.snapshot_paths == ()
    assert not any(paths.local_snapshots_dir.iterdir())
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 0


def test_ingest_sources_skips_approved_file_under_forbidden_path(tmp_path):
    workspace = tmp_path / "workspace"
    forbidden = tmp_path / "private"
    source_path = forbidden / "note.txt"
    source_path.parent.mkdir()
    source_path.write_text("hello", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    approval["forbidden_paths"] = [str(forbidden)]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 1
    assert result.snapshot_paths == ()
    assert not any(paths.local_snapshots_dir.iterdir())
    assert repository.list_sources()[0].status == SourceStatus.APPROVED
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 0


def test_ingest_sources_skips_approved_sensitive_file(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / ".env"
    source_path.write_text("OPENAI_API_KEY=fake-secret", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name=".env",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 1
    assert result.snapshot_paths == ()
    assert repository.list_sources()[0].status == SourceStatus.SKIPPED_POLICY
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 0


def test_ingest_sources_continues_after_missing_approved_file(tmp_path):
    workspace = tmp_path / "workspace"
    missing_path = tmp_path / "missing.txt"
    valid_path = tmp_path / "valid.txt"
    valid_path.write_text("valid evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [
        missing_path.resolve().as_uri(),
        valid_path.resolve().as_uri(),
    ]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    missing_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=missing_path.resolve().as_uri(),
            display_name="missing.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    valid_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=valid_path.resolve().as_uri(),
            display_name="valid.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    sources = {source.id: source.status for source in repository.list_sources()}
    assert result.ingested_count == 1
    assert result.skipped_count == 1
    assert sources[missing_id] == SourceStatus.STALE_SOURCE
    assert sources[valid_id] == SourceStatus.INGESTED
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 1


def test_ingest_sources_skips_already_ingested_source_without_duplicate_snapshot(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("hello", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    first = ingest_sources(IngestSourcesRequest(workspace=workspace))
    second = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert first.ingested_count == 1
    assert second.ingested_count == 0
    assert second.skipped_count == 1
    with repository.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 1


def test_ingest_sources_marks_deleted_ingested_source_stale(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("hello", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    ingest_sources(IngestSourcesRequest(workspace=workspace))
    source_path.unlink()
    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 1
    assert repository.list_sources()[0].status == SourceStatus.STALE_SOURCE
    with repository.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchone()[0]
    assert count == 1


def test_ingest_sources_reingests_changed_ingested_source(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("first evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri=source_path.resolve().as_uri(),
            display_name="note.txt",
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    ingest_sources(IngestSourcesRequest(workspace=workspace))
    source_path.write_text("changed evidence", encoding="utf-8")
    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    assert result.skipped_count == 0
    assert repository.list_sources()[0].status == SourceStatus.INGESTED
    with repository.connect() as conn:
        hashes = [
            row["content_hash"]
            for row in conn.execute(
                "SELECT content_hash FROM source_snapshots WHERE source_id = ? ORDER BY id",
                (source_id,),
            )
        ]
    assert len(hashes) == 2
    assert hashes[0] != hashes[1]


def test_upsert_source_does_not_downgrade_ingested_to_discovered(tmp_path):
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    source_id = repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///tmp/note.txt",
            display_name="note.txt",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )
    repository.update_source_status(source_id, SourceStatus.INGESTED)

    repository.upsert_source(
        Source(
            id=None,
            type=SourceType.LOCAL_FILE,
            uri="file:///tmp/note.txt",
            display_name="note.txt",
            owner=None,
            status=SourceStatus.DISCOVERED,
        )
    )

    assert repository.list_sources()[0].status == SourceStatus.INGESTED
