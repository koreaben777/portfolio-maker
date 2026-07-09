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
