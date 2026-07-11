from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import portfolio_maker.infrastructure.extractors as extractors
import portfolio_maker.infrastructure.snapshots as snapshots
from portfolio_maker.application.approval import ApprovalMissingError, write_sample_approval
from portfolio_maker.application.ingestion import ingest_sources
from portfolio_maker.application.models import IngestSourcesRequest
from portfolio_maker.domain.models import Source, SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.policy import FilePolicy
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import write_local_snapshot
from portfolio_maker.workspace import WorkspacePaths


def test_extract_text_masks_secrets_and_hashes_raw_bytes(tmp_path):
    source = tmp_path / "note.txt"
    raw = b"hello\napi_key=secret123\nbad:\xff"
    source.write_bytes(raw)

    extracted = extract_text(source)

    assert extracted.text == "hello\napi_key=[REDACTED]\nbad:\ufffd"
    assert extracted.content_hash == hashlib.sha256(raw).hexdigest()
    assert extracted.extractor == "text-v2"


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

    snapshot_path = write_local_snapshot(paths, 7, source, extracted)

    assert snapshot_path == paths.local_snapshots_dir / f"source-7-{extracted.content_hash}.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["source_id"] == 7
    assert payload["source_uri"] == source.resolve().as_uri()
    assert payload["display_name"] == "note.txt"
    assert payload["content_hash"] == extracted.content_hash
    assert payload["extractor"] == "text-v2"
    assert payload["text"] == "password: [REDACTED]"
    assert payload["extracted_at"].endswith("Z")


def test_approved_extraction_reads_open_file_after_path_replacement(tmp_path, monkeypatch):
    source_path = tmp_path / "note.txt"
    replacement_path = tmp_path / "replacement.txt"
    source_path.write_text("approved evidence", encoding="utf-8")
    replacement_path.write_text("unapproved evidence", encoding="utf-8")
    source_uri = source_path.resolve().as_uri()
    original_open = extractors.os.open

    def replace_path_after_open(path, flags, *, dir_fd=None):
        if dir_fd is None:
            descriptor = original_open(path, flags)
        else:
            descriptor = original_open(path, flags, dir_fd=dir_fd)
        if path == source_path.name and dir_fd is not None:
            source_path.unlink()
            source_path.symlink_to(replacement_path)
        return descriptor

    monkeypatch.setattr(extractors.os, "open", replace_path_after_open)

    _, extracted = extractors.extract_approved_text(
        source_uri,
        FilePolicy(),
    )

    assert extracted.text == "approved evidence"


def test_approved_extraction_keeps_parent_descriptor_after_parent_replacement(tmp_path, monkeypatch):
    source_parent = tmp_path / "approved"
    source_path = source_parent / "note.txt"
    target_parent = tmp_path / "target"
    target_path = target_parent / "note.txt"
    source_parent.mkdir()
    target_parent.mkdir()
    source_path.write_text("approved evidence", encoding="utf-8")
    target_path.write_text("unapproved evidence", encoding="utf-8")
    source_uri = source_path.resolve().as_uri()
    original_open = extractors.os.open
    replaced = False

    def replace_parent_before_final_open(path, flags, *, dir_fd=None):
        nonlocal replaced
        if not replaced and (
            path == source_path or (path == source_path.name and dir_fd is not None)
        ):
            source_parent.rename(tmp_path / "approved-original")
            source_parent.symlink_to(target_parent, target_is_directory=True)
            replaced = True
        if dir_fd is None:
            return original_open(path, flags)
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(extractors.os, "open", replace_parent_before_final_open)

    _, extracted = extractors.extract_approved_text(source_uri, FilePolicy())

    assert extracted.text == "approved evidence"


def test_approved_extraction_rejects_fifo_without_blocking(tmp_path):
    fifo_path = tmp_path / "source.fifo"
    os.mkfifo(fifo_path)
    environment = os.environ | {"PYTHONPATH": str(Path.cwd() / "src")}
    script = """
from pathlib import Path
import sys
from portfolio_maker.infrastructure.extractors import extract_approved_text
from portfolio_maker.infrastructure.policy import FilePolicy, SourcePathPolicyError

try:
    extract_approved_text(Path(sys.argv[1]).as_uri(), FilePolicy())
except SourcePathPolicyError:
    raise SystemExit(0)
raise SystemExit(1)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script, str(fifo_path)],
        check=False,
        env=environment,
        timeout=2,
    )

    assert completed.returncode == 0


def test_snapshot_store_repairs_damaged_content_addressed_file(tmp_path):
    source = tmp_path / "note.txt"
    source.write_text("approved evidence", encoding="utf-8")
    extracted = extract_text(source)
    paths = WorkspacePaths.from_root(tmp_path / "workspace")
    snapshot_path = write_local_snapshot(paths, 7, source, extracted)
    snapshot_path.write_text("{not-json", encoding="utf-8")

    repaired_path = write_local_snapshot(paths, 7, source, extracted)

    assert repaired_path == snapshot_path
    assert json.loads(repaired_path.read_text(encoding="utf-8"))["text"] == "approved evidence"


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
    expected_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert result.snapshot_paths == (
        paths.local_snapshots_dir / f"source-{source_id}-{expected_hash}.json",
    )
    assert repository.list_sources()[0].status == SourceStatus.INGESTED
    payload = json.loads(result.snapshot_paths[0].read_text(encoding="utf-8"))
    assert payload["text"] == "hello\napi_key=[REDACTED]"
    with repository._connection() as conn:
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
    with repository._connection() as conn:
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
    assert repository.list_sources()[0].status == SourceStatus.SKIPPED_POLICY
    with repository._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 0


def test_ingest_sources_anchors_relative_forbidden_path_to_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    source_path = workspace / "private" / "note.txt"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("private evidence", encoding="utf-8")
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [source_path.resolve().as_uri()]
    approval["forbidden_paths"] = ["private"]
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
    monkeypatch.chdir(tmp_path)

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 1
    assert not any(paths.local_snapshots_dir.iterdir())


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
    with repository._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0] == 0


def test_ingest_sources_skips_timestamped_password_export(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "bitwarden_export_20260710.json"
    source_path.write_text("{}", encoding="utf-8")
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
            display_name=source_path.name,
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert repository.list_sources()[0].status == SourceStatus.SKIPPED_POLICY


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
    with repository._connection() as conn:
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
    with repository._connection() as conn:
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
    with repository._connection() as conn:
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

    first = ingest_sources(IngestSourcesRequest(workspace=workspace))
    source_path.write_text("changed evidence", encoding="utf-8")
    second = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert second.ingested_count == 1
    assert second.skipped_count == 0
    assert repository.list_sources()[0].status == SourceStatus.INGESTED
    with repository._connection() as conn:
        snapshots = [
            (row["content_hash"], row["snapshot_path"])
            for row in conn.execute(
                "SELECT content_hash, snapshot_path FROM source_snapshots WHERE source_id = ? ORDER BY id",
                (source_id,),
            )
        ]
    assert len(snapshots) == 2
    assert snapshots[0][0] != snapshots[1][0]
    assert snapshots[0][1] != snapshots[1][1]
    assert json.loads(first.snapshot_paths[0].read_text(encoding="utf-8"))["text"] == "first evidence"
    assert json.loads(second.snapshot_paths[0].read_text(encoding="utf-8"))["text"] == "changed evidence"


def test_ingest_sources_recovers_stale_same_content_without_duplicate_snapshot(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("approved evidence", encoding="utf-8")
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
    repository.update_source_status(source_id, SourceStatus.STALE_SOURCE)

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert repository.list_sources()[0].status == SourceStatus.INGESTED
    with repository._connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE source_id = ?", (source_id,)
        ).fetchone()[0] == 1


def test_ingest_sources_rewrites_legacy_snapshot_metadata_in_place(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("approved evidence", encoding="utf-8")
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
    first = ingest_sources(IngestSourcesRequest(workspace=workspace))
    payload = json.loads(first.snapshot_paths[0].read_text(encoding="utf-8"))
    payload["extractor"] = "text-v1"
    first.snapshot_paths[0].write_text(json.dumps(payload), encoding="utf-8")
    with repository._connection() as conn:
        conn.execute(
            "UPDATE source_snapshots SET extractor = ? WHERE source_id = ?",
            ("text-v1", source_id),
        )

    ingest_sources(IngestSourcesRequest(workspace=workspace))

    with repository._connection() as conn:
        rows = conn.execute(
            "SELECT extractor FROM source_snapshots WHERE source_id = ?", (source_id,)
        ).fetchall()
    assert [row["extractor"] for row in rows] == ["text-v2"]


def test_ingest_sources_migrates_verified_managed_legacy_snapshot(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("api_key=synthetic-placeholder", encoding="utf-8")
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
    raw = source_path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    legacy_path = paths.local_snapshots_dir / f"source-{source_id}.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_uri": source_path.resolve().as_uri(),
                "display_name": source_path.name,
                "content_hash": content_hash,
                "extractor": "text-v1",
                "extracted_at": "2026-07-10T00:00:00Z",
                "text": "api_key=synthetic-placeholder",
            }
        ),
        encoding="utf-8",
    )
    legacy_row_id = repository.insert_source_snapshot(
        source_id,
        legacy_path,
        content_hash,
        "text-v1",
    )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert result.skipped_count == 1
    assert not legacy_path.exists()
    with repository._connection() as conn:
        rows = conn.execute(
            "SELECT id, snapshot_path, extractor FROM source_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == legacy_row_id
    assert rows[0]["snapshot_path"] != str(legacy_path)
    assert rows[0]["extractor"] == "text-v2"


def test_ingest_sources_retries_legacy_cleanup_after_unlink_failure(tmp_path, monkeypatch):
    workspace, source_path, paths, repository, source_id, legacy_path = _legacy_snapshot_state(
        tmp_path,
        "api_key=synthetic-placeholder",
    )
    original_unlink = snapshots.os.unlink
    failed_once = False

    def fail_legacy_unlink_once(path, *args, **kwargs):
        nonlocal failed_once
        if path == legacy_path.name and not failed_once:
            failed_once = True
            raise OSError("synthetic cleanup interruption")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(snapshots.os, "unlink", fail_legacy_unlink_once)
    with pytest.raises(OSError, match="synthetic cleanup interruption"):
        ingest_sources(IngestSourcesRequest(workspace=workspace))
    monkeypatch.setattr(snapshots.os, "unlink", original_unlink)

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.skipped_count == 1
    assert not legacy_path.exists()
    with repository._connection() as conn:
        rows = conn.execute(
            "SELECT snapshot_path, extractor FROM source_snapshots WHERE source_id = ?",
            (source_id,),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["extractor"] == "text-v2"
    assert rows[0]["snapshot_path"] != str(legacy_path)


def test_ingest_sources_migrates_legacy_snapshot_when_source_content_changed(tmp_path):
    workspace, source_path, paths, repository, source_id, legacy_path = _legacy_snapshot_state(
        tmp_path,
        "old api_key=synthetic-placeholder",
    )
    source_path.write_text("new approved evidence", encoding="utf-8")

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 1
    assert not legacy_path.exists()
    with repository._connection() as conn:
        rows = conn.execute(
            "SELECT snapshot_path, extractor FROM source_snapshots WHERE source_id = ? ORDER BY id",
            (source_id,),
        ).fetchall()
    assert len(rows) == 2
    assert all(row["extractor"] == "text-v2" for row in rows)
    assert all(row["snapshot_path"] != str(legacy_path) for row in rows)


def test_ingest_sources_legacy_cleanup_does_not_follow_replaced_managed_directory(
    tmp_path,
    monkeypatch,
):
    workspace, _, paths, _, _, legacy_path = _legacy_snapshot_state(
        tmp_path,
        "api_key=synthetic-placeholder",
    )
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_file = external_dir / legacy_path.name
    external_file.write_text("external marker", encoding="utf-8")
    original_dir = paths.local_snapshots_dir.with_name("local-original")
    original_unlink = snapshots.os.unlink
    swapped = False

    def replace_directory_before_path_unlink(path, *args, **kwargs):
        nonlocal swapped
        if path == legacy_path.name and not swapped:
            swapped = True
            paths.local_snapshots_dir.rename(original_dir)
            paths.local_snapshots_dir.symlink_to(external_dir, target_is_directory=True)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(snapshots.os, "unlink", replace_directory_before_path_unlink)

    with pytest.raises(OSError, match="managed snapshot directory changed"):
        ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert external_file.read_text(encoding="utf-8") == "external marker"


def test_ingest_sources_legacy_migration_does_not_write_through_replaced_directory(
    tmp_path,
    monkeypatch,
):
    workspace, _, paths, _, source_id, legacy_path = _legacy_snapshot_state(
        tmp_path,
        "api_key=synthetic-placeholder",
    )
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    original_dir = paths.local_snapshots_dir.with_name("local-original")
    replacement_snapshot = paths.local_snapshots_dir / f"source-{source_id}-{payload['content_hash']}.json"
    original_read_json = snapshots._read_regular_json
    swapped = False

    def replace_directory_after_legacy_read(directory_descriptor, filename):
        nonlocal swapped
        loaded = original_read_json(directory_descriptor, filename)
        if filename == legacy_path.name and loaded is not None and not swapped:
            swapped = True
            paths.local_snapshots_dir.rename(original_dir)
            paths.local_snapshots_dir.mkdir()
        return loaded

    monkeypatch.setattr(
        snapshots,
        "_read_regular_json",
        replace_directory_after_legacy_read,
    )

    with pytest.raises(OSError, match="managed snapshot directory changed"):
        ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert not replacement_snapshot.exists()
    assert (original_dir / legacy_path.name).exists()


def _legacy_snapshot_state(tmp_path, text):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text(text, encoding="utf-8")
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
            display_name=source_path.name,
            owner=None,
            status=SourceStatus.APPROVED,
        )
    )
    content_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    legacy_path = paths.local_snapshots_dir / f"source-{source_id}.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "source_uri": source_path.resolve().as_uri(),
                "display_name": source_path.name,
                "content_hash": content_hash,
                "extractor": "text-v1",
                "extracted_at": "2026-07-10T00:00:00Z",
                "text": text,
            }
        ),
        encoding="utf-8",
    )
    repository.insert_source_snapshot(source_id, legacy_path, content_hash, "text-v1")
    return workspace, source_path, paths, repository, source_id, legacy_path


def test_ingest_sources_repairs_stale_db_extractor_before_idempotent_skip(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    source_path.write_text("approved evidence", encoding="utf-8")
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
    with repository._connection() as conn:
        conn.execute(
            "UPDATE source_snapshots SET extractor = ? WHERE source_id = ?",
            ("text-v1", source_id),
        )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    with repository._connection() as conn:
        extractor = conn.execute(
            "SELECT extractor FROM source_snapshots WHERE source_id = ?", (source_id,)
        ).fetchone()["extractor"]
    assert extractor == "text-v2"


def test_ingest_sources_rejects_replaced_approved_path_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    source_path = tmp_path / "note.txt"
    target_path = tmp_path / "replacement.txt"
    source_path.write_text("approved evidence", encoding="utf-8")
    target_path.write_text("unapproved evidence", encoding="utf-8")
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
    source_path.unlink()
    source_path.symlink_to(target_path)

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert repository.list_sources()[0].status == SourceStatus.SKIPPED_POLICY
    with repository._connection() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM source_snapshots WHERE source_id = ?", (source_id,)
        ).fetchone()[0] == 0


def test_ingest_sources_rejects_nonregular_noncanonical_and_oversized_approved_paths(tmp_path):
    workspace = tmp_path / "workspace"
    directory_path = tmp_path / "directory"
    directory_path.mkdir()
    source_path = tmp_path / "note.txt"
    source_path.write_text("evidence", encoding="utf-8")
    oversized_path = tmp_path / "oversized.txt"
    oversized_path.write_bytes(b"x" * 2_000_001)
    (tmp_path / "nested").mkdir()
    noncanonical_uri = (tmp_path / "nested" / ".." / "note.txt").as_uri()
    paths = WorkspacePaths.from_root(workspace)
    write_sample_approval(paths)
    approval = json.loads(paths.approval_path.read_text(encoding="utf-8"))
    approval["approved_source_uris"] = [
        directory_path.resolve().as_uri(),
        noncanonical_uri,
        oversized_path.resolve().as_uri(),
    ]
    paths.approval_path.write_text(json.dumps(approval), encoding="utf-8")
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    for uri, display_name in (
        (directory_path.resolve().as_uri(), "directory"),
        (noncanonical_uri, "note.txt"),
        (oversized_path.resolve().as_uri(), "oversized.txt"),
    ):
        repository.upsert_source(
            Source(
                id=None,
                type=SourceType.LOCAL_FILE,
                uri=uri,
                display_name=display_name,
                owner=None,
                status=SourceStatus.APPROVED,
            )
        )

    result = ingest_sources(IngestSourcesRequest(workspace=workspace))

    assert result.ingested_count == 0
    assert {source.status for source in repository.list_sources()} == {SourceStatus.SKIPPED_POLICY}


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
