from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from portfolio_maker.application.approval import load_approval
from portfolio_maker.application.models import IngestSourcesRequest, IngestSourcesResult
from portfolio_maker.domain.models import SourceStatus, SourceType
from portfolio_maker.infrastructure.extractors import extract_text
from portfolio_maker.infrastructure.sqlite_repository import SQLiteRepository
from portfolio_maker.infrastructure.snapshots import SnapshotStore
from portfolio_maker.workspace import WorkspacePaths


def _path_from_file_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("Only file URIs are supported")
    return Path(unquote(parsed.path))


def ingest_sources(request: IngestSourcesRequest) -> IngestSourcesResult:
    paths = WorkspacePaths.from_root(request.workspace)
    paths.ensure()
    approval = load_approval(paths)
    approved_uris = set(approval.approved_source_uris)
    repository = SQLiteRepository(paths.db_path)
    repository.initialize()
    snapshots = SnapshotStore(paths)
    ingested_count = 0
    skipped_count = 0
    snapshot_paths: list[Path] = []

    for source in repository.list_sources():
        if source.uri not in approved_uris or source.type != SourceType.LOCAL_FILE:
            skipped_count += 1
            continue

        assert source.id is not None
        source_path = _path_from_file_uri(source.uri)
        extracted = extract_text(source_path)
        snapshot_path = snapshots.write_local_snapshot(source.id, source_path, extracted)
        repository.insert_source_snapshot(
            source.id,
            snapshot_path,
            extracted.content_hash,
            extracted.extractor,
        )
        repository.update_source_status(source.id, SourceStatus.INGESTED)
        snapshot_paths.append(snapshot_path)
        ingested_count += 1

    return IngestSourcesResult(
        ingested_count=ingested_count,
        skipped_count=skipped_count,
        snapshot_paths=tuple(snapshot_paths),
    )
